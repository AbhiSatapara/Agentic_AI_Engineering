"""Refresh the local AIDTM knowledge base from the official website.

    python knowledge_ingestion.py              # update data/knowledge/*.json
    python knowledge_ingestion.py --dry-run    # show what would change, write nothing
    python knowledge_ingestion.py --validate   # check the JSON files only (no network)
    python knowledge_ingestion.py --force      # accept large size changes

How it works
- Each page in PAGES is downloaded once (robots.txt is respected, with a polite delay).
- Each "part" is the text between two headings on that page. It becomes one knowledge document.
- A document is only replaced when the new text is valid. Empty, failed, suspiciously
  shorter/longer or duplicate results never overwrite existing knowledge.
- Documents with "managed_by": "manual" (or ids not listed below) are never touched.

To track a new page or section, add it to PAGES. No other code changes are needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.robotparser
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "data" / "knowledge"
BASE = "https://www.aidtm.ac.in"
USER_AGENT = "AIDTM-Assistant-KnowledgeUpdater/1.0 (student support chatbot; polite, low volume)"
REQUEST_DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 20

MIN_CHARS = 40  # anything shorter is treated as a failed extraction
SHRINK_LIMIT = 0.4  # refuse to replace a document with text < 40% of its old length
GROWTH_LIMIT = 2.5  # ...or > 250% (usually means footer/menu text leaked in)
DEFAULT_MAX_CHARS = 6000

# knowledge file -> category label shown in each document
FILES = {
    "about_aidtm.json": "About AIDTM",
    "programmes.json": "Programmes",
    "admissions.json": "Admissions",
    "curriculum.json": "Curriculum",
    "fees.json": "Fees",
    "placements.json": "Placements",
    "student_facilities.json": "Student Facilities",
    "faculty.json": "Faculty",
}

# --------------------------------------------------------------------------- #
# What to collect.  start/end are heading (or, for `end`, any text) prefixes.
#   nth       : use the n-th heading that matches `start` (default 1)
#   end       : stop at the first later line starting with this text.
#               Without it, the part ends at the next heading of the same or higher level.
#   keep_footer: do not cut at footer markers (only the contact page needs this)
# --------------------------------------------------------------------------- #
PAGES = [
    {
        "url": f"{BASE}/about-us",
        "parts": [
            {"id": "about-overview", "file": "about_aidtm.json", "title": "About AIDTM",
             "tags": ["aidtm", "college", "institute", "established", "2020", "overview", "bda", "aids"],
             "start": "About AIDTM", "end": "Message from Trustee"},
            {"id": "about-vision-mission", "file": "about_aidtm.json", "title": "AIDTM vision and mission",
             "tags": ["vision", "mission", "aidtm"], "start": "Vision"},
            {"id": "about-adani-group", "file": "about_aidtm.json", "title": "About the Adani Group",
             "tags": ["adani", "group", "nation building", "growth with goodness"], "start": "About Adani Group"},
            {"id": "about-team", "file": "about_aidtm.json", "title": "AIDTM team (administration and support staff)",
             "tags": ["team", "staff", "administration", "admission manager", "counsellor", "librarian"],
             "start": "AIDTM Team", "end": "Urvi Shah"},
            {"id": "about-statutory-documents", "file": "about_aidtm.json",
             "title": "Statutory documents and committees",
             "tags": ["aicte", "approval", "anti-ragging", "grievance", "women development", "sc/st", "mandatory disclosure"],
             "start": "Statutory Documents", "max_chars": 700},
        ],
    },
    {
        "url": f"{BASE}/ContactUs",
        "parts": [
            {"id": "about-contact", "file": "about_aidtm.json", "title": "Contact details and address",
             "tags": ["contact", "address", "phone", "email", "location", "gandhinagar", "reach"],
             "start": "Contact Us", "end": "Write to us", "keep_footer": True},
        ],
    },
    {
        "url": f"{BASE}/academics",
        "parts": [
            {"id": "programme-list", "file": "programmes.json", "title": "Programmes offered by AIDTM",
             "tags": ["courses", "programmes", "offered", "pgdm", "bda", "aids", "epaaa", "executive", "list"],
             "start": "Programmes", "end": "We Care About Your Privacy", "max_chars": 2500},
        ],
    },
    {
        "url": f"{BASE}/academics/pgdm-big-data-analytics",
        "parts": [
            {"id": "programme-bda-overview", "file": "programmes.json",
             "title": "PGDM in Big Data Analytics: overview",
             "tags": ["bda", "big data analytics", "pgdm", "duration", "two-year", "intake", "overview"],
             "start": "About Programme", "end": "Importance of Big Data Analytics"},
            {"id": "programme-bda-careers", "file": "programmes.json",
             "title": "PGDM in Big Data Analytics: careers, industries, recruiters and pedagogy",
             "tags": ["bda", "careers", "jobs", "industries", "recruiters", "pedagogy", "why join"],
             "start": "Importance of Big Data Analytics", "end": "Term Wise Credit Course structure"},
            {"id": "curriculum-bda", "file": "curriculum.json",
             "title": "PGDM in Big Data Analytics: subjects and credit system",
             "tags": ["bda", "subjects", "syllabus", "curriculum", "courses", "credits", "modules"],
             "start": "Term Wise Credit Course structure"},
        ],
    },
    {
        "url": f"{BASE}/academics/pgdm-ai-data-science",
        "parts": [
            {"id": "programme-aids-overview", "file": "programmes.json",
             "title": "PGDM in AI & Data Science: overview",
             "tags": ["aids", "ai", "data science", "artificial intelligence", "pgdm", "duration", "two-year", "intake", "overview"],
             "start": "About Programme", "end": "Importance of Artificial Intelligence & Data Science"},
            {"id": "programme-aids-careers", "file": "programmes.json",
             "title": "PGDM in AI & Data Science: careers, industries, recruiters and pedagogy",
             "tags": ["aids", "careers", "jobs", "industries", "recruiters", "pedagogy", "why join"],
             "start": "Importance of Artificial Intelligence & Data Science", "end": "Course structure"},
            {"id": "curriculum-aids", "file": "curriculum.json",
             "title": "PGDM in AI & Data Science: subjects and credit system",
             "tags": ["aids", "subjects", "syllabus", "curriculum", "courses", "credits", "modules"],
             "start": "Course structure"},
        ],
    },
    {
        "url": f"{BASE}/academics/executive-program-ai-analytics-automation",
        "parts": [
            {"id": "programme-epaaa", "file": "programmes.json",
             "title": "Executive Program in AI, Analytics & Automation (EPAAA): overview",
             "tags": ["epaaa", "executive", "executive programme", "working professionals", "weekend", "online", "duration"],
             "start": "About Executive Program in AI, Analytics & Automation", "end": "Program Highlights"},
            {"id": "fees-epaaa", "file": "fees.json",
             "title": "EPAAA: highlights, duration, start date and fee",
             "tags": ["epaaa", "executive", "fee", "fees", "duration", "start date", "gst"],
             "start": "Program Highlights", "end": "Tools & Techniques Covered"},
            {"id": "curriculum-epaaa", "file": "curriculum.json",
             "title": "EPAAA: tools, curriculum modules and learning outcomes",
             "tags": ["epaaa", "executive", "curriculum", "modules", "subjects", "tools", "power bi", "learning outcomes"],
             "start": "Tools & Techniques Covered", "end": "Who Can Apply"},
            {"id": "admission-epaaa", "file": "admissions.json",
             "title": "EPAAA: who can apply and how to apply",
             "tags": ["epaaa", "executive", "eligibility", "apply", "admission", "registration", "contact"],
             "start": "Who Can Apply", "end": "Program Certificate"},
        ],
    },
    {
        "url": f"{BASE}/admission",
        "parts": [
            {"id": "admission-overview", "file": "admissions.json",
             "title": "Admissions for PGDM Batch 2027-29: overview and selection",
             "tags": ["admission", "admissions", "apply", "batch 2027-29", "selection", "scholarship", "brochure", "policy"],
             "start": "Inviting Application", "end": "Admission Calendar"},
            {"id": "admission-calendar", "file": "admissions.json", "title": "Admission calendar and important dates",
             "tags": ["admission", "dates", "calendar", "deadline", "last date", "application form", "commitment fee"],
             "start": "Admission Calendar", "end": "Fee Structure"},
            {"id": "fees-pgdm-first-table", "file": "fees.json",
             "title": "PGDM fee structure (first table on the Admissions page, not labelled with a batch)",
             "tags": ["fee", "fees", "pgdm", "installment", "registration fee", "caution deposit", "total fee", "bda", "aids"],
             "start": "Fee Structure", "end": "Fee Structure for PGDM"},
            {"id": "fees-pgdm-batch-2027-29", "file": "fees.json",
             "title": "Fee structure for PGDM (BDA) and PGDM (AI & DS), Batch 2027-29",
             "tags": ["fee", "fees", "pgdm", "installment", "registration fee", "caution deposit", "total fee", "batch 2027-29", "bda", "aids"],
             "start": "Fee Structure for PGDM", "end": "We Care About Your Privacy", "max_chars": 2500},
        ],
    },
    {
        "url": f"{BASE}/apply-admission",
        "parts": [
            {"id": "admission-application-guidelines", "file": "admissions.json",
             "title": "Application guidelines and documents needed",
             "tags": ["apply", "application", "guidelines", "documents", "registration", "photograph", "marksheet"],
             "start": "Application Guidelines", "end": "Programme Highlights"},
            {"id": "admission-eligibility-bda", "file": "admissions.json",
             "title": "Eligibility criteria: PGDM in Big Data Analytics",
             "tags": ["bda", "eligibility", "requirements", "criteria", "admission", "cat", "xat", "cmat", "gmat", "mat", "atma", "percentage"],
             "start": "Eligibility Criteria", "nth": 1, "end": "PGDM (AI & Data Science)"},
            {"id": "admission-eligibility-aids", "file": "admissions.json",
             "title": "Eligibility criteria: PGDM in AI & Data Science",
             "tags": ["aids", "eligibility", "requirements", "criteria", "admission", "cat", "xat", "cmat", "gmat", "mat", "atma", "percentage"],
             "start": "Eligibility Criteria", "nth": 2, "end": "Top Recruiters"},
            {"id": "placements-highlights", "file": "placements.json",
             "title": "Placement highlights (from the Apply page)",
             "tags": ["placement", "placements", "ctc", "package", "salary", "average", "highest", "pre placement offers", "ppo"],
             "start": "Placement Highlights", "end": "Work Experience"},
            {"id": "facilities-programme-highlights", "file": "student_facilities.json",
             "title": "Programme highlights (IBM Software Lab, internship and capstone)",
             "tags": ["ibm", "software lab", "internship", "capstone", "industry", "highlights"],
             "start": "Programme Highlights", "end": "Placement Highlights"},
        ],
    },
    {
        "url": f"{BASE}/Placement",
        "parts": [
            {"id": "placements-overview", "file": "placements.json",
             "title": "Placements at AIDTM: key numbers",
             "tags": ["placement", "placements", "ctc", "package", "salary", "highest", "lowest", "pre placement offers", "ppo", "100%"],
             "start": "Placement", "nth": 1, "end": "Summer Internship Program"},
            {"id": "placements-internship-capstone", "file": "placements.json",
             "title": "Summer internship programme and capstone project",
             "tags": ["internship", "sip", "summer internship", "capstone", "project", "placement"],
             "start": "Summer Internship Program", "end": "Academic Diversity"},
            {"id": "placements-profiles-roles", "file": "placements.json",
             "title": "Student backgrounds, industries and roles offered",
             "tags": ["roles", "jobs", "industries", "background", "b.com", "bba", "be", "b.tech", "bca", "placement"],
             "start": "Academic Diversity", "end": "Industry Visits"},
            {"id": "placements-industry-visits", "file": "placements.json",
             "title": "Industry visits and placement report",
             "tags": ["industry visits", "industrial visits", "placement report", "exposure"],
             "start": "Industry Visits", "end": "Leading Recruiters"},
            {"id": "placements-recruiters", "file": "placements.json", "title": "Leading recruiters",
             "tags": ["recruiters", "companies", "placement", "hiring", "adani", "kpmg", "deloitte", "tcs"],
             "start": "Leading Recruiters", "nth": 1, "end": "We Care About Your Privacy", "max_chars": 1500},
        ],
    },
    {
        "url": f"{BASE}/faculty",
        "parts": [
            {"id": "faculty-list", "file": "faculty.json", "title": "AIDTM faculty members",
             "tags": ["faculty", "professor", "dean", "teachers", "teaching staff", "who teaches"],
             "start": "Faculty", "nth": 1, "end": "We Care About Your Privacy", "max_chars": 2500},
        ],
    },
]

# Text that marks the start of page footers / cookie banners / social links.
FOOTER_MARKERS = ("facebook", "we care about your privacy", "opp. maharaj hotel lane")
NOISE_LINES = re.compile(
    r"^(learn more|know more|know all|view all|apply now|apply online|enquire now|read more|click to return.*|"
    r"×|#|edit preferences|accept all|accept necessary|save)$",
    re.I,
)
BLOCK_TAGS = {"p", "li", "div", "section", "article", "td", "th", "dd", "dt", "blockquote",
              "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "body"}
HEADINGS = {f"h{i}": i for i in range(1, 7)}


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def allowed_by_robots(url: str) -> bool:
    """Respect robots.txt. A missing robots.txt means 'allowed'."""
    parts = urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
            _robots[root] = rp
        except Exception:  # noqa: BLE001 - robots unreachable: proceed politely
            _robots[root] = None
    rp = _robots[root]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def fetch_html(url: str) -> str:
    import requests  # imported here so --validate works without network libs

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.encoding = resp.encoding or "utf-8"
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5)
    raise RuntimeError(str(last_error))


# --------------------------------------------------------------------------- #
# HTML -> ordered text lines -> sections
# --------------------------------------------------------------------------- #
def norm(text: str) -> str:
    text = text.lower().replace("–", "-").replace("—", "-").replace("’", "'")
    text = re.sub(r"[*:_]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def html_to_lines(html: str) -> list[tuple[int | None, str]]:
    """Return [(heading_level or None, text)] in reading order."""
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer", "img"]):
        tag.decompose()
    for tag in soup.find_all(True):
        ident = " ".join([tag.get("id") or ""] + (tag.get("class") or [])).lower()
        if "cookie" in ident:
            tag.decompose()

    # keep table rows together: "cell | cell | cell"
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) or "-" for c in tr.find_all(["th", "td"])]
        row = soup.new_tag("p")  # its own block, so every row becomes its own line
        row.string = " | ".join(cells)
        tr.replace_with(row)

    lines: list[tuple[int | None, str]] = []
    current_block, buffer, level = None, [], None

    def flush():
        text = re.sub(r"\s+", " ", " ".join(buffer)).strip()
        if text:
            lines.append((level, text))

    for s in soup.find_all(string=True):
        if not isinstance(s, NavigableString) or not s.strip():
            continue
        block = next((p for p in s.parents if getattr(p, "name", None) in BLOCK_TAGS), None)
        if block is not current_block:
            flush()
            current_block, buffer = block, []
            level = HEADINGS.get(getattr(block, "name", ""), None)
        buffer.append(s.strip())
    flush()
    return lines


def extract_part(lines, part: dict) -> str | None:
    start, nth = norm(part["start"]), part.get("nth", 1)
    end = norm(part["end"]) if part.get("end") else None
    matches = [i for i, (lvl, t) in enumerate(lines) if lvl and norm(t).startswith(start)]
    if len(matches) < nth:
        return None
    i0 = matches[nth - 1]
    level0 = lines[i0][0]
    out: list[str] = []
    for lvl, text in lines[i0 + 1:]:
        n = norm(text)
        if end and n.startswith(end):
            break
        if not end and lvl and lvl <= level0:
            break
        if not part.get("keep_footer") and n.startswith(FOOTER_MARKERS):
            break
        if NOISE_LINES.match(text.strip()):
            continue
        out.append(f"### {text}" if lvl else text)
    text = "\n".join(out).strip()
    if not text:
        return None
    max_chars = part.get("max_chars", DEFAULT_MAX_CHARS)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text


# --------------------------------------------------------------------------- #
# Knowledge files
# --------------------------------------------------------------------------- #
def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode("utf-8")).hexdigest()[:16]


def load_file(name: str) -> list[dict]:
    path = KNOWLEDGE_DIR / name
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        # never lose a damaged file silently
        path.replace(path.with_name(f"{path.stem}.corrupt-{date.today():%Y%m%d}{path.suffix}"))
        return []


def write_file(name: str, docs: list[dict]) -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=KNOWLEDGE_DIR, prefix=".kb-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, KNOWLEDGE_DIR / name)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


REQUIRED_FIELDS = ("id", "title", "category", "content", "source_url", "last_updated")


def validate() -> int:
    problems, seen = 0, set()
    for name in FILES:
        for doc in load_file(name):
            missing = [k for k in REQUIRED_FIELDS if not str(doc.get(k, "")).strip()]
            if missing:
                print(f"  ✗ {name}: {doc.get('id', '?')} missing {missing}")
                problems += 1
            if doc.get("id") in seen:
                print(f"  ✗ duplicate id {doc['id']}")
                problems += 1
            seen.add(doc.get("id"))
    print(f"Validated {len(seen)} documents, {problems} problem(s).")
    return 1 if problems else 0


# --------------------------------------------------------------------------- #
# Main update routine
# --------------------------------------------------------------------------- #
def run(dry_run: bool = False, force: bool = False, only: list[str] | None = None) -> int:
    today = date.today().isoformat()
    store = {name: load_file(name) for name in FILES}
    by_id = {d["id"]: (name, d) for name, docs in store.items() for d in docs if "id" in d}
    seen_hashes = {content_hash(d.get("content", "")): d["id"] for _, d in by_id.values() if d.get("content")}

    report: list[tuple[str, str, str]] = []  # (status, label, detail)
    pages_ok = pages_failed = 0
    changed_files: set[str] = set()

    for page in PAGES:
        url = page["url"]
        if only and not any(o in url for o in only):
            continue
        if not allowed_by_robots(url):
            report.append(("SKIPPED", url, "disallowed by robots.txt"))
            pages_failed += 1
            continue
        try:
            html = fetch_html(url)
            lines = html_to_lines(html)
            if not lines:
                raise RuntimeError("page had no readable text")
            pages_ok += 1
            report.append(("PAGE OK", url, f"{len(lines)} text blocks"))
        except Exception as exc:  # noqa: BLE001
            pages_failed += 1
            report.append(("PAGE FAILED", url, f"{exc} (existing knowledge kept)"))
            continue
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)

        for part in page["parts"]:
            pid, fname = part["id"], part["file"]
            text = extract_part(lines, part)
            old = by_id.get(pid, (None, None))[1]
            if text is None or len(text) < MIN_CHARS:
                report.append(("KEPT OLD", pid, "section not found or empty on the page"))
                continue
            if old and old.get("managed_by") == "manual":
                report.append(("SKIPPED", pid, "marked as manually managed"))
                continue
            h = content_hash(text)
            if old and h == content_hash(old.get("content", "")):
                report.append(("UNCHANGED", pid, ""))
                continue
            if seen_hashes.get(h) not in (None, pid):
                report.append(("DUPLICATE", pid, f"same text as {seen_hashes[h]}; not stored twice"))
                continue
            if old and not force:
                ratio = len(text) / max(len(old["content"]), 1)
                if ratio < SHRINK_LIMIT or ratio > GROWTH_LIMIT:
                    report.append(("KEPT OLD", pid, f"new text is {ratio:.0%} of old size; review, then use --force"))
                    continue
            doc = {
                "id": pid,
                "title": part["title"],
                "category": FILES[fname],
                "content": text,
                "source_url": url,
                "last_updated": today,
                "tags": part.get("tags", []),
                "managed_by": "ingestion",
            }
            docs = store[fname]
            if old:
                for i, d in enumerate(docs):
                    if d.get("id") == pid:
                        docs[i] = doc
                report.append(("UPDATED", pid, ""))
            else:
                docs.append(doc)
                report.append(("NEW", pid, ""))
            by_id[pid] = (fname, doc)
            seen_hashes[h] = pid
            changed_files.add(fname)

    print("\nKnowledge update report")
    print("-" * 60)
    for status, label, detail in report:
        print(f"{status:12} {label}  {detail}".rstrip())
    print("-" * 60)
    counts = {s: sum(1 for r in report if r[0] == s) for s in {r[0] for r in report}}
    print(f"Pages OK: {pages_ok}, failed/skipped: {pages_failed}.  " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))

    if dry_run:
        print("Dry run: nothing was written.")
    elif changed_files:
        for fname in sorted(changed_files):
            write_file(fname, store[fname])
        print("Written: " + ", ".join(sorted(changed_files)))
    else:
        print("No files needed changes.")
    return 1 if pages_ok == 0 and pages_failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh the AIDTM knowledge base from the official website.")
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing files")
    ap.add_argument("--force", action="store_true", help="accept large size changes")
    ap.add_argument("--validate", action="store_true", help="validate local JSON files and exit")
    ap.add_argument("--only", nargs="*", help="only pages whose URL contains one of these words")
    args = ap.parse_args()
    if args.validate:
        sys.exit(validate())
    sys.exit(run(dry_run=args.dry_run, force=args.force, only=args.only))


if __name__ == "__main__":
    main()
