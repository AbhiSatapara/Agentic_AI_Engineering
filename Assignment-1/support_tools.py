"""JSON-backed student support tools for the AIDTM assignment project."""
from __future__ import annotations
import json, re, uuid
from datetime import datetime
from pathlib import Path

from retrieval import retrieve, format_context

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STUDENTS_FILE = DATA / "students.json"
MEMORY_FILE = DATA / "student_memory.json"
REQUESTS_FILE = DATA / "support_requests.json"
PAYMENT_POLICY_FILE = DATA / "knowledge" / "payment_failure_policy.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def check_student_fee_status(student_id: str) -> dict:
    sid = (student_id or "").strip().upper()
    if not sid:
        return {"ok": False, "message": "Authenticated student identity is unavailable."}
    data = _read(STUDENTS_FILE, {"students": []})
    student = next((s for s in data.get("students", []) if s.get("student_id", "").upper() == sid), None)
    if not student:
        return {"ok": False, "message": "No fee record was found for the signed-in student."}
    return {"ok": True, "student": {k: student.get(k) for k in ("student_id", "name", "programme", "batch", "total_fee", "paid_fee", "pending_fee", "fee_status", "payment_history")}}


def retrieve_payment_policy(question: str) -> dict:
    """Load the explicit illustrative payment-failure policy used in the assignment scenario."""
    policy = _read(PAYMENT_POLICY_FILE, {})
    if not policy or not policy.get("rules"):
        return {"found": False, "message": "Payment scenario policy file is unavailable."}
    rules = policy["rules"]
    return {
        "found": True,
        "policy_name": policy.get("title", "Payment portal failure procedure"),
        "policy_type": policy.get("policy_type", "assignment_scenario"),
        "rules": rules,
        "student_guidance": policy.get("student_guidance", []),
    }


def create_support_request(student_id: str, issue: str) -> dict:
    """Record a support request in the project's local JSON ticket register."""
    sid = (student_id or "").strip().upper()
    if not sid:
        return {"ok": False, "message": "The signed-in student identity could not be verified."}
    if not issue or len(issue.strip()) < 8:
        return {"ok": False, "message": "Please provide a short description of the issue."}
    rows = _read(REQUESTS_FILE, {"requests": []})
    ticket = {
        "ticket_id": "AIDTM-" + uuid.uuid4().hex[:8].upper(),
        "student_id": sid,
        "issue": issue.strip(),
        "category": "Fee Payment Issue" if re.search(r"fee|payment|portal|transaction", issue, re.I) else "Student Support",
        "status": "Open",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    rows.setdefault("requests", []).append(ticket)
    _write(REQUESTS_FILE, rows)
    return {"ok": True, "ticket": ticket, "message": "Support request recorded successfully."}


def remember_student_fact(student_id: str, fact_type: str, value: str) -> dict:
    sid = (student_id or "").strip().upper()
    kind, val = (fact_type or "").strip().lower(), (value or "").strip()
    if not sid:
        return {"ok": False, "message": "Student identity is unavailable."}
    if kind not in {"programme", "learning_preference", "communication_preference", "career_interest"}:
        return {"ok": False, "message": "Only programme, learning preference, communication preference, or career interest can be remembered."}
    if not val or len(val) > 180 or re.search(r"\b(password|otp|pin|card number|bank account|medical|diagnos|disability|religion|caste|salary|fee balance)\b", val, re.I):
        return {"ok": False, "message": "This information is empty, too long, or sensitive/temporary and will not be stored."}
    data = _read(MEMORY_FILE, {"students": {}})
    data.setdefault("students", {}).setdefault(sid, {})[kind] = val
    _write(MEMORY_FILE, data)
    return {"ok": True, "message": f"Saved the student's {kind.replace('_', ' ')} for future conversations."}


def get_student_memory(student_id: str) -> dict:
    sid = (student_id or "").strip().upper()
    if not sid:
        return {"found": False, "message": "Student identity is unavailable."}
    data = _read(MEMORY_FILE, {"students": {}})
    facts = data.get("students", {}).get(sid, {})
    return {"found": bool(facts), "facts": facts}
