"""JSON-backed, identity-scoped student support tools for Assignment 2."""
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
APPROVALS_FILE = DATA / "approval_requests.json"
AUDIT_FILE = DATA / "audit_logs.json"


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


def _audit(actor: str, action: str, target: str, status: str, details: dict | None = None):
    rows = _read(AUDIT_FILE, {"events": []})
    rows.setdefault("events", []).append({
        "audit_id": "AUD-" + uuid.uuid4().hex[:10].upper(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "actor_student_id": actor,
        "action": action,
        "target_student_id": target,
        "status": status,
        "details": details or {},
    })
    _write(AUDIT_FILE, rows)


def _authorized(requested_student_id: str, authenticated_student_id: str) -> bool:
    requested = (requested_student_id or authenticated_student_id or "").strip().upper()
    actor = (authenticated_student_id or "").strip().upper()
    return bool(actor and requested == actor)


def check_student_fee_status(student_id: str, authenticated_student_id: str = "") -> dict:
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "read_fee_record", sid or "UNKNOWN", "DENIED", {"reason": "cross_student_access"})
        return {"ok": False, "code": "ACCESS_DENIED", "message": "Access denied. You can only view your own student record."}
    data = _read(STUDENTS_FILE, {"students": []})
    student = next((s for s in data.get("students", []) if s.get("student_id", "").upper() == actor), None)
    if not student:
        _audit(actor, "read_fee_record", actor, "NOT_FOUND")
        return {"ok": False, "message": "No fee record was found for the signed-in student."}
    _audit(actor, "read_fee_record", actor, "ALLOWED")
    return {"ok": True, "student": {k: student.get(k) for k in ("student_id", "name", "programme", "batch", "total_fee", "paid_fee", "pending_fee", "fee_status", "payment_history", "scholarship_status")}}


def retrieve_payment_policy(question: str) -> dict:
    policy = _read(PAYMENT_POLICY_FILE, {})
    if not policy or not policy.get("rules"):
        return {"found": False, "message": "Payment scenario policy file is unavailable."}
    return {"found": True, "policy_name": policy.get("title", "Payment portal failure procedure"),
            "policy_type": policy.get("policy_type", "assignment_scenario"), "rules": policy["rules"],
            "student_guidance": policy.get("student_guidance", [])}


def create_support_request(student_id: str, issue: str, authenticated_student_id: str = "") -> dict:
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "create_support_request", sid or "UNKNOWN", "DENIED")
        return {"ok": False, "code": "ACCESS_DENIED", "message": "Access denied. You can only create a request for your own account."}
    if not issue or len(issue.strip()) < 8:
        return {"ok": False, "message": "Please provide a short description of the issue."}
    rows = _read(REQUESTS_FILE, {"requests": []})
    ticket = {"ticket_id": "AIDTM-" + uuid.uuid4().hex[:8].upper(), "student_id": actor,
              "issue": issue.strip(), "category": "Fee Payment Issue" if re.search(r"fee|payment|portal|transaction", issue, re.I) else "Student Support",
              "status": "Open", "created_at": datetime.now().isoformat(timespec="seconds")}
    rows.setdefault("requests", []).append(ticket)
    _write(REQUESTS_FILE, rows)
    _audit(actor, "create_support_request", actor, "COMPLETED", {"ticket_id": ticket["ticket_id"]})
    return {"ok": True, "ticket": ticket, "message": "Support request recorded successfully."}


def get_student_contact_details(student_id: str, authenticated_student_id: str = "") -> dict:
    """Return contact details only for the authenticated student; deny cross-student requests."""
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "read_contact_details", sid or "UNKNOWN", "DENIED", {"reason": "cross_student_access"})
        return {"ok": False, "code": "ACCESS_DENIED", "message": "Access denied. You can only view your own contact details."}
    data = _read(STUDENTS_FILE, {"students": []})
    student = next((s for s in data.get("students", []) if s.get("student_id", "").upper() == actor), None)
    if not student:
        _audit(actor, "read_contact_details", actor, "NOT_FOUND")
        return {"ok": False, "message": "Your student record could not be found."}
    _audit(actor, "read_contact_details", actor, "ALLOWED")
    return {"ok": True, "student": {"student_id": actor, "name": student.get("name", "Student"),
                                      "mobile_number": student.get("mobile_number", "Not provided"),
                                      "email": student.get("email", "Not provided")}}


def request_contact_update(student_id: str, field: str, new_value: str, authenticated_student_id: str = "") -> dict:
    """Create a pending request; never changes the student record here."""
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    field = (field or "").strip().lower()
    value = (new_value or "").strip()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "request_contact_update", sid or "UNKNOWN", "DENIED", {"field": field})
        return {"ok": False, "code": "ACCESS_DENIED", "message": "Access denied. You can only request changes to your own account."}
    if field not in {"mobile_number", "email"}:
        return {"ok": False, "message": "Only mobile number and email updates are supported."}
    if field == "mobile_number" and not re.fullmatch(r"\+?[0-9][0-9\s-]{7,14}", value):
        return {"ok": False, "message": "Please provide a valid mobile number."}
    if field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return {"ok": False, "message": "Please provide a valid email address."}
    students = _read(STUDENTS_FILE, {"students": []})
    if not any(s.get("student_id", "").upper() == actor for s in students.get("students", [])):
        return {"ok": False, "message": "Signed-in student record was not found."}
    approvals = _read(APPROVALS_FILE, {"requests": []})
    # Keep only one unresolved request per student and field to avoid conflicting approvals.
    if any(x.get("student_id") == actor and x.get("field") == field and x.get("status") == "PENDING" for x in approvals.get("requests", [])):
        return {"ok": False, "message": "You already have a pending approval request for this field. Please approve or reject it first."}
    request = {"request_id": "APR-" + uuid.uuid4().hex[:10].upper(), "student_id": actor,
               "field": field, "proposed_value": value, "status": "PENDING",
               "requested_at": datetime.now().isoformat(timespec="seconds"), "decided_at": None,
               "decision": None, "executed": False}
    approvals.setdefault("requests", []).append(request)
    _write(APPROVALS_FILE, approvals)
    _audit(actor, "request_contact_update", actor, "PENDING_APPROVAL", {"request_id": request["request_id"], "field": field})
    return {"ok": True, "approval_required": True, "request": request,
            "message": "Your update request is pending. The contact detail has not been changed. Please review and approve or reject it in the approval panel."}


def decide_contact_update(request_id: str, authenticated_student_id: str, approve: bool) -> dict:
    """Approve/reject only the signed-in student's pending request; execute after approval."""
    actor = (authenticated_student_id or "").strip().upper()
    approvals = _read(APPROVALS_FILE, {"requests": []})
    req = next((x for x in approvals.get("requests", []) if x.get("request_id") == request_id), None)
    if not actor or not req or req.get("student_id") != actor:
        _audit(actor or "UNKNOWN", "decide_contact_update", "UNKNOWN", "DENIED", {"request_id": request_id})
        return {"ok": False, "message": "This approval request is not available to your account."}
    if req.get("status") != "PENDING":
        return {"ok": False, "message": "This request has already been processed."}
    req["decided_at"] = datetime.now().isoformat(timespec="seconds")
    req["decision"] = "APPROVED" if approve else "REJECTED"
    if not approve:
        req["status"] = "REJECTED"
        req["executed"] = False
        _write(APPROVALS_FILE, approvals)
        _audit(actor, "decide_contact_update", actor, "REJECTED_NOT_EXECUTED", {"request_id": request_id, "field": req["field"]})
        field_label = "mobile number" if req["field"] == "mobile_number" else "email address"
        return {"ok": True, "status": "REJECTED", "executed": False,
                "message": f"You selected Reject. Your registered {field_label} has not been changed; your existing contact details remain unchanged."}

    students = _read(STUDENTS_FILE, {"students": []})
    student = next((s for s in students.get("students", []) if s.get("student_id", "").upper() == actor), None)
    if not student:
        req["status"] = "FAILED"
        _write(APPROVALS_FILE, approvals)
        _audit(actor, "execute_contact_update", actor, "FAILED", {"request_id": request_id, "reason": "student_not_found"})
        return {"ok": False, "message": "Student record could not be found. No change was made."}
    field = req["field"]
    if field not in {"mobile_number", "email"}:
        req["status"] = "FAILED"
        _write(APPROVALS_FILE, approvals)
        _audit(actor, "execute_contact_update", actor, "FAILED", {"request_id": request_id, "reason": "invalid_field"})
        return {"ok": False, "message": "Unsupported field. No change was made."}
    old_value = student.get(field, "")
    student[field] = req["proposed_value"]
    _write(STUDENTS_FILE, students)
    req["status"] = "APPROVED_EXECUTED"
    req["executed"] = True
    _write(APPROVALS_FILE, approvals)
    _audit(actor, "execute_contact_update", actor, "APPROVED_AND_EXECUTED", {"request_id": request_id, "field": field, "changed": True})
    field_label = "mobile number" if field == "mobile_number" else "email address"
    return {"ok": True, "status": req["status"], "executed": True,
            "message": f"Success! Your registered {field_label} has been updated successfully."}


def remember_student_fact(student_id: str, fact_type: str, value: str, authenticated_student_id: str = "") -> dict:
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "remember_student_fact", sid or "UNKNOWN", "DENIED")
        return {"ok": False, "message": "Access denied."}
    kind, val = (fact_type or "").strip().lower(), (value or "").strip()
    if kind not in {"programme", "learning_preference", "communication_preference", "career_interest"}:
        return {"ok": False, "message": "Only programme, learning preference, communication preference, or career interest can be remembered."}
    if not val or len(val) > 180 or re.search(r"\b(password|passcode|otp|one[- ]time password|pin|card number|bank account|medical|diagnos|disability|religion|caste|salary|fee balance)\b", val, re.I):
        _audit(actor, "remember_student_fact", actor, "BLOCKED_SENSITIVE_MEMORY", {"fact_type": kind})
        return {"ok": False, "message": "This information is empty, too long, or sensitive/temporary and will not be stored."}
    data = _read(MEMORY_FILE, {"students": {}})
    data.setdefault("students", {}).setdefault(actor, {})[kind] = val
    _write(MEMORY_FILE, data)
    _audit(actor, "remember_student_fact", actor, "SAVED", {"fact_type": kind})
    return {"ok": True, "message": f"Saved the student's {kind.replace('_', ' ')} for future conversations."}


def get_student_memory(student_id: str, authenticated_student_id: str = "") -> dict:
    actor = (authenticated_student_id or "").strip().upper()
    sid = (student_id or actor).strip().upper()
    if not actor or not _authorized(sid, actor):
        _audit(actor or "UNKNOWN", "read_student_memory", sid or "UNKNOWN", "DENIED")
        return {"found": False, "message": "Access denied."}
    data = _read(MEMORY_FILE, {"students": {}})
    facts = data.get("students", {}).get(actor, {})
    return {"found": bool(facts), "facts": facts}


def list_pending_approvals(authenticated_student_id: str) -> list[dict]:
    actor = (authenticated_student_id or "").strip().upper()
    if not actor:
        return []
    rows = _read(APPROVALS_FILE, {"requests": []})
    return [x for x in rows.get("requests", []) if x.get("student_id") == actor and x.get("status") == "PENDING"]
