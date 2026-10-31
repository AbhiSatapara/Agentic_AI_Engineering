"""Search the local AIDTM knowledge base (data/knowledge/*.json).

Pipeline for every student question:
  1. Understand: fix spelling, detect the programme(s) and the topic(s) being asked about,
     and resolve follow-ups ("what subjects are included in it?") from earlier messages.
  2. Search: BM25 keyword scoring over the documents, combined with topic/programme boosts.
  3. Select: return the few most relevant documents (several when needed, e.g. comparisons).

Why not embeddings / a vector database?  The knowledge base is ~35 small documents with a
fixed vocabulary (programme names, fees, eligibility...).  Keyword search plus the boosts
below is fast, needs no extra dependencies or downloads, and is easy to debug.  If the
knowledge base grows to hundreds of pages, embeddings can be added behind `retrieve()`.
"""

from __future__ import annotations

import difflib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "data" / "knowledge"

MAX_DOCS = 6
MAX_DOCS_COMPARE = 8
MAX_CONTEXT_CHARS = 12000
RELATIVE_CUTOFF = 0.45  # keep documents scoring at least this fraction of the best one
MIN_RAW_BM25 = 2.0  # with no topic/programme detected, weaker keyword matches count as "nothing found"

STOPWORDS = set(
    "a an the is are was were be been am do does did of in on at to for from with by about as and or but if so "
    "that this these those it its i me my we our you your they them their he she his her can could would should "
    "will shall may might please tell give show want need know let us get got have has had what which who whom "
    "how when where why there here than then also too very just any some more most all both each other such "
    "hi hello hey thanks thank ok okay yes no not into out up down over under again once".split()
)
PROTECTED = {"aids", "bda", "ds", "ai", "epaaa", "pgdm", "cat", "xat", "mat", "class", "less", "pass"}

ENTITY_TAGS = {"bda", "aids", "epaaa"}
ENTITY_PATTERNS = {
    "bda": r"\bbda\b|big data|business data analytics",
    "aids": r"\baids\b|\bai\s*(?:&|and|/)?\s*(?:ds|data\s*science)\b|artificial intelligence|\bdata science\b|\bgen\s*ai\b",
    "epaaa": r"\bepaaa\b|executive|working professional|tech edp|exec ed",
}
ENTITY_TERMS = {
    "bda": ["bda", "big", "data", "analytic"],
    "aids": ["aids", "ai", "data", "science"],
    "epaaa": ["epaaa", "executive"],
}

# topic -> (regex, categories to favour, tags to favour, documents to pin when no programme is named)
INTENTS: dict[str, dict] = {
    "programmes": dict(
        pattern=r"\b(course|courses|programme|programmes|program|programs|degree|degrees|pgdm|offer|offers|offered|offering|diploma|specialization|specialisation|stream|streams|branch|branches)\b",
        categories=["Programmes"], tags=[], pin=["programme-list"]),
    "curriculum": dict(
        pattern=r"\b(subject|subjects|syllabus|curriculum|module|modules|taught|teach|topic|topics|semester|term|credit|credits|learn|study|studies|covered|include|included)\b",
        categories=["Curriculum"], tags=[], pin=[]),
    "fees": dict(
        pattern=r"\b(fee|fees|cost|costs|tuition|installment|installments|instalment|pay|payment|price|expensive|cheap|afford|money|deposit|refund|rs|inr|rupees|lakh|lakhs)\b|how much",
        categories=["Fees"], tags=[], pin=[]),
    "admissions": dict(
        pattern=r"\b(admission|admissions|admit|eligib\w*|requirement|requirements|criteria|qualification|qualifications|qualify|apply|applying|application|entrance|cat|xat|cmat|gmat|mat|atma|selection|scholarship|scholarships|calendar|deadline|document|documents|brochure|enrol|enroll|enrolment|percentage|marks|interview)\b|last date",
        categories=["Admissions"], tags=[], pin=[]),
    "placements": dict(
        pattern=r"\b(placement|placements|package|packages|salary|salaries|ctc|lpa|recruit\w*|job|jobs|career|careers|internship|internships|capstone|company|companies|hire|hiring|hired|role|roles|scope|opportunities|opportunity)\b",
        categories=["Placements"], tags=["careers", "jobs"], pin=[]),
    "facilities": dict(
        pattern=r"\b(facility|facilities|infrastructure|campus|library|lab|labs|classroom|classrooms|hostel|hostels|accommodation|canteen|cafeteria|mess|sport|sports|gym|amenity|amenities|wifi|transport|bus|tour|activities|events|support|committee|ragging|grievance|safety|medical)\b|student life",
        categories=["Student Facilities"], tags=["facilities", "anti-ragging", "grievance"], pin=["facilities-overview"]),
    "faculty": dict(
        pattern=r"\b(faculty|faculties|professor|professors|dean|teacher|teachers|teaches|staff|team|mentor|mentors|instructor|lecturer)\b",
        categories=["Faculty"], tags=["faculty"], pin=[]),
    "contact": dict(
        pattern=r"\b(contact|phone|number|call|email|mail|address|location|located|reach|whatsapp|directions|website)\b|where is",
        categories=["About AIDTM"], tags=["contact"], pin=["about-contact"]),
    "duration": dict(
        pattern=r"\b(duration|years|year|months|month|length|full time|part time|weekend)\b|how long|how many years",
        categories=["Programmes"], tags=["duration"], pin=[]),
    "compare": dict(
        pattern=r"\b(difference|differences|compare|comparison|versus|vs|better|distinguish|similarities|similar|which one|which is)\b",
        categories=["Programmes", "Curriculum", "Admissions"], tags=["overview", "eligibility", "subjects"], pin=[]),
}
ABOUT_PATTERN = r"\b(aidtm|college|institute|institution|adani|vision|mission|established|history|about)\b"
FOLLOWUP_PATTERN = r"\b(it|its|this|that|they|them|those|these|there|same|also|too|then)\b|^(and|what about|how about|ok and|so)\b"

# words people often type that are not in the documents themselves
EXTRA_VOCAB = (
    "curriculum syllabus subjects eligibility requirements admission admissions placement placements scholarship "
    "facilities infrastructure programme programmes courses difference compare duration executive analytics "
    "science artificial intelligence information address contact faculty professor internship capstone "
    "recruiters package salary hostel library campus eligibility criteria application deadline"
).split()


@dataclass
class Doc:
    id: str
    title: str
    category: str
    content: str
    source_url: str
    last_updated: str
    tags: list[str] = field(default_factory=list)
    tf: Counter = field(default_factory=Counter)
    length: int = 0

    @property
    def entities(self) -> set[str]:
        return {t for t in self.tags if t in ENTITY_TAGS}


@dataclass
class Retrieval:
    docs: list[Doc]
    entities: set[str]
    intents: list[str]
    query: str  # the corrected / expanded question actually searched

    @property
    def found(self) -> bool:
        return bool(self.docs)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def stem(token: str) -> str:
    if token in PROTECTED or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("&", " and ")
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in STOPWORDS]


_index: dict = {"sig": None}


def _build_index() -> dict:
    docs: list[Doc] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.json")):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a damaged file must never break the chatbot
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not str(item.get("content", "")).strip() or not item.get("id"):
                continue
            tags = [str(t).lower() for t in item.get("tags", [])]
            doc = Doc(
                id=str(item["id"]), title=str(item.get("title", "")), category=str(item.get("category", "")),
                content=str(item["content"]), source_url=str(item.get("source_url", "")),
                last_updated=str(item.get("last_updated", "")), tags=tags,
            )
            # title and tags count double: they say what the document is about
            weighted = tokenize(doc.title) * 2 + tokenize(" ".join(tags)) * 2 + tokenize(doc.content)
            doc.tf, doc.length = Counter(weighted), len(weighted)
            docs.append(doc)
    n = max(len(docs), 1)
    df: Counter = Counter()
    for d in docs:
        df.update(d.tf.keys())
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    vocab = set(EXTRA_VOCAB)
    for d in docs:
        vocab.update(re.findall(r"[a-z]{4,}", d.content.lower()))
        vocab.update(re.findall(r"[a-z]{4,}", d.title.lower()))
    return {"docs": docs, "idf": idf, "avgdl": sum(d.length for d in docs) / n, "vocab": vocab}


def load_index() -> dict:
    """Rebuilt automatically when any knowledge file changes (no restart needed)."""
    files = sorted(KNOWLEDGE_DIR.glob("*.json")) if KNOWLEDGE_DIR.exists() else []
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _index.get("sig") != sig:
        _index.update(_build_index())
        _index["sig"] = sig
    return _index


def all_documents() -> list[Doc]:
    return load_index()["docs"]


# --------------------------------------------------------------------------- #
# Question understanding
# --------------------------------------------------------------------------- #
def correct_spelling(text: str, vocab: set[str]) -> str:
    """Fix obvious typos ('carriculum' -> 'curriculum') using the knowledge-base vocabulary."""
    out = []
    for word in re.findall(r"[A-Za-z0-9&/']+|[^\sA-Za-z0-9&/']", text.lower()):
        if len(word) >= 5 and word.isalpha() and word not in vocab and word not in STOPWORDS:
            match = difflib.get_close_matches(word, vocab, n=1, cutoff=0.84)
            word = match[0] if match else word
        out.append(word)
    return " ".join(out)


def detect_entities(text: str) -> set[str]:
    return {name for name, pat in ENTITY_PATTERNS.items() if re.search(pat, text)}


def detect_intents(text: str) -> list[str]:
    return [name for name, spec in INTENTS.items() if re.search(spec["pattern"], text)]


def _history_context(history: list[dict]) -> tuple[set[str], list[str]]:
    """Programmes and topics from the most recent earlier student messages."""
    vocab = load_index()["vocab"]
    entities: set[str] = set()
    intents: list[str] = []
    for msg in reversed([m for m in history if m.get("role") == "user"][-6:]):
        text = correct_spelling(msg["content"], vocab)
        if not entities:
            entities = detect_entities(text)
        if not intents:
            intents = [i for i in detect_intents(text) if i != "compare"]
        if entities and intents:
            break
    return entities, intents


def understand(question: str, history: list[dict]) -> tuple[str, set[str], list[str]]:
    """Return (corrected question, programmes, topics), using earlier messages for follow-ups."""
    vocab = load_index()["vocab"]
    text = correct_spelling(question, vocab)
    entities, intents = detect_entities(text), detect_intents(text)

    short = len(tokenize(text)) <= 9
    follow_up = bool(history) and (short or re.search(FOLLOWUP_PATTERN, text) is not None)
    if follow_up:
        prev_entities, prev_intents = _history_context(history)
        if not entities:
            entities = set(prev_entities)
        if not intents or (not set(intents) - {"compare"} and short):
            intents = intents + [i for i in prev_intents if i not in intents]
        elif entities and short and not intents:
            intents = list(prev_intents)

    if "compare" in intents:  # comparisons need both sides
        if not entities:
            entities = {"bda", "aids"}
        elif entities <= {"bda", "aids"}:
            entities = {"bda", "aids"}
        else:
            entities = entities | {"bda", "aids"}

    if not intents and not entities and re.search(ABOUT_PATTERN, text):
        intents = ["about"]
    return text, entities, intents


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _bm25(query_terms: list[str], doc: Doc, idx: dict, k1: float = 1.5, b: float = 0.75) -> float:
    score = 0.0
    for term in set(query_terms):
        f = doc.tf.get(term, 0)
        if f:
            score += idx["idf"].get(term, 0.0) * f * (k1 + 1) / (f + k1 * (1 - b + b * doc.length / idx["avgdl"]))
    return score


def retrieve(question: str, history: list[dict] | None = None) -> Retrieval:
    idx = load_index()
    docs: list[Doc] = idx["docs"]
    text, entities, intents = understand(question, history or [])

    terms = tokenize(text)
    for e in entities:
        terms += ENTITY_TERMS[e]
    raw = {d.id: _bm25(terms, d, idx) for d in docs}
    top_raw = max(raw.values(), default=0.0)

    if not entities and not intents and top_raw < MIN_RAW_BM25:
        return Retrieval([], entities, intents, text)  # nothing in the knowledge base relates to this

    favoured_categories = {c for i in intents for c in INTENTS.get(i, {}).get("categories", [])}
    favoured_tags = {t for i in intents for t in INTENTS.get(i, {}).get("tags", [])}
    if "about" in intents:
        favoured_categories.add("About AIDTM")
    pinned = {p for i in intents for p in INTENTS.get(i, {}).get("pin", [])} if not entities else set()
    if "about" in intents:
        pinned.add("about-overview")

    scored: list[tuple[float, Doc]] = []
    for d in docs:
        score = raw[d.id] / top_raw if top_raw else 0.0
        if d.category in favoured_categories:
            score += 0.7
        if favoured_tags & set(d.tags):
            score += 0.4
        if d.id in pinned:
            score += 0.6
        if entities and d.entities:
            overlap = d.entities & entities
            if overlap:
                score += 0.8 * len(overlap) / len(d.entities)
            else:
                score -= 0.6  # a document about a different programme
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)

    limit = MAX_DOCS_COMPARE if "compare" in intents else MAX_DOCS
    best = scored[0][0]
    chosen: list[Doc] = []
    size = 0
    for score, d in scored:
        if len(chosen) >= limit or score < RELATIVE_CUTOFF * best or score <= 0.5:
            break
        if size + len(d.content) > MAX_CONTEXT_CHARS and chosen:
            continue
        chosen.append(d)
        size += len(d.content)
    return Retrieval(chosen, entities, intents, text)


def format_context(docs: list[Doc]) -> str:
    parts = []
    for d in docs:
        parts.append(
            f"### {d.title}\n(Category: {d.category} | Source: {d.source_url} | Last updated: {d.last_updated})\n{d.content}"
        )
    return "\n\n".join(parts)
