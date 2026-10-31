"""AIDTM Assistant – student support chatbot with saved chats and local knowledge retrieval."""

import json
import os
import tempfile
import threading
import uuid
import time
import base64
import hashlib
import hmac
import html
import logging
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from groq import APIConnectionError, AuthenticationError, Groq, RateLimitError

# LangSmith is optional at runtime: the chatbot continues working if tracing is disabled.
try:
    from langsmith import traceable
except ImportError:  # pragma: no cover - handled gracefully when dependency is absent
    traceable = None
from retrieval import retrieve, format_context
from support_tools import (check_student_fee_status, retrieve_payment_policy, create_support_request,
                            remember_student_fact, get_student_memory, request_contact_update,
                            list_pending_approvals, decide_contact_update)

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "").strip()
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "AIDTM-Student-Support-Agent").strip()
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"true", "1", "yes"}
MAX_CONTEXT_MESSAGES = 20  # recent messages sent to the model for follow-ups

SYSTEM_PROMPT = """You are AIDTM Assistant, the student support assistant for the \
Adani Institute of Digital Technology Management (AIDTM).

Rules:
- The full name is "Adani Institute of Digital Technology Management". Never give any other expansion of AIDTM.
- Talk like a friendly, professional student-support person. Be natural and human.
- Answer exactly what was asked. No long introductions, no repeated disclaimers, no filler.
- Keep answers short and simple. Use bullet points only when listing several items.
- Use the conversation history to understand follow-up questions.
- Only state facts supported by the connected knowledge base, student record, or scenario policy.
- Reply in the same language the student uses.
- For support-ticket creation, present a concise confirmation with Ticket ID, category, and status. Say "Your support request has been recorded". Do not call it a demo/local ticket, do not say it was not sent, and do not claim that institute staff received, reviewed, or will respond to it.
- For payment-portal failure questions, use the payment-policy tool and explain the applicable steps directly. Do not say that information is unavailable when the scenario policy provides a rule. Never describe the illustrative assignment policy as an officially approved institute policy.
- If a student reports that money was debited but the payment status is failed or pending, advise them not to retry until the transaction reference has been checked.
- Keep the tone empathetic, practical, and reassuring. Avoid repeating the same offer to create a ticket after a ticket has already been created."""

SUGGESTIONS = [
    ("🏛️", "Tell me about AIDTM."),
    ("📚", "What courses does AIDTM offer?"),
    ("📊", "Tell me about PGDM in Big Data Analytics."),
    ("🤖", "What is PGDM in AI & Data Science?"),
    ("💳", "What is my fee status?"),
    ("🧾", "My payment failed. What should I do?"),
]

# Show the signed-in student identity and logout control at the bottom of the sidebar.
SHOW_ACCOUNT_PANEL = True

LOGO_FILE = Path(__file__).resolve().parent / "assets" / "aidtm_logo.png"


@st.cache_resource(show_spinner=False)
def _logo_src() -> str:
    """Logo as an inline data URI (works inside HTML blocks). Empty string if the file is missing."""
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO_FILE.read_bytes()).decode("ascii")
    except OSError:
        return ""


def logo_img(css_class: str, fallback: str = "") -> str:
    src = _logo_src()
    return f'<img class="{css_class}" src="{src}" alt="Adani Institute of Digital Technology Management">' if src else fallback


log = logging.getLogger("aidtm")  # technical details go to the server console, never to students

st.set_page_config(
    page_title="AIDTM Assistant",
    page_icon="🎓",
    layout="wide",
    # "expanded" makes the sidebar open on first load. The default ("auto") collapses it on
    # narrow windows, which is one reason it sometimes looked "missing" after login.
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
# NOTE ON THE SIDEBAR: never hide [data-testid="stToolbar"] or the whole header.
# Streamlit renders the "open sidebar" (>>) button inside that header/toolbar area, so
# hiding it removes the only way to reopen a collapsed sidebar. Only the unwanted
# children (deploy button, main menu, footer) are hidden below.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

:root{
  --ink:#1B2238; --muted:#5B6478; --line:#E3E7F0; --bg:#F6F7FB;
  --accent:#2F4BC8; --accent-hover:#2640B0; --accent-soft:#EDF0FD;
  --navy:#0F1830; --navy-2:#1A2750; --navy-line:#26345F;
  --side-text:#E6EAF7; --side-muted:#97A3C7;
}
html, body, .stApp{font-family:'Plus Jakarta Sans',-apple-system,'Segoe UI',sans-serif;}
.stApp{background:var(--bg); color:var(--ink);}

/* Hide only the Streamlit chrome we do not want. Header + sidebar toggles stay available. */
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"]{display:none !important;}
header[data-testid="stHeader"]{background:transparent;}
[data-testid="stMain"], section.main{scroll-behavior:smooth;}
.block-container{max-width:820px; padding-top:2.2rem; padding-bottom:7rem;}

/* ---------- Sidebar open / close controls (always visible, clearly styled) ---------- */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="collapsedControl"] button{
  background:#fff !important; border:1px solid var(--line) !important; border-radius:10px !important;
  box-shadow:0 2px 8px rgba(20,30,70,.10) !important; opacity:1 !important; visibility:visible !important;}
[data-testid="stExpandSidebarButton"] *,
[data-testid="stSidebarCollapsedControl"] button *,
[data-testid="collapsedControl"] button *{color:var(--ink) !important;}
/* Streamlit fades the collapse (<<) button in only on hover; keep it visible. */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button{opacity:1 !important; visibility:visible !important;}

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"]{background:var(--navy); border-right:none;}
section[data-testid="stSidebar"] > div{padding-top:.4rem;}
section[data-testid="stSidebar"] *{color:var(--side-text);}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.35rem;}
section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]{gap:.2rem; align-items:center; flex-wrap:nowrap;}
section[data-testid="stSidebar"] [data-testid="stColumn"]{min-width:0;}

.brand{display:flex; align-items:center; gap:.7rem; padding:.2rem .2rem .9rem;}
.brand .logo{width:38px; height:38px; flex:0 0 38px; border-radius:10px; display:grid; place-items:center;
  font-size:.95rem; font-weight:700; letter-spacing:.02em; color:#fff; background:var(--accent);}
.brand .name{font-weight:700; font-size:1.02rem; line-height:1.15; color:#fff;}
.brand .sub{font-size:.72rem; color:var(--side-muted); font-weight:500;}
.hist-label{font-size:.74rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  color:var(--side-muted); margin:1.1rem 0 .7rem .2rem; padding-top:.2rem; line-height:1.2;}
.hist-empty{font-size:.84rem; color:#7C88AD; padding:.4rem .3rem;}
.logo-card{background:#fff; border-radius:12px; padding:.6rem .8rem; margin:.1rem 0 .8rem;}
.logo-card img{display:block; width:100%; height:auto;}
.brand-text{padding:0 .2rem .6rem;}
.brand-text .name{font-weight:700; font-size:1rem; color:#fff; line-height:1.2;}
.brand-text .sub{font-size:.74rem; color:var(--side-muted); font-weight:500;}
.hero-logo{display:block; width:min(320px,70%); height:auto; margin:0 auto 1.3rem;}
.login-logo{display:block; width:min(300px,80%); height:auto; margin:0 auto 1.1rem;}
.side-divider{height:1px; background:var(--navy-line); margin:.6rem 0 .4rem;}
.account-row{display:flex; align-items:center; gap:.65rem; padding:.55rem .35rem; border-radius:12px; background:rgba(255,255,255,.045); min-width:0;}
.account-avatar{width:36px; height:36px; flex:0 0 36px; display:grid; place-items:center; border-radius:50%;
  background:#34477F; color:#fff; font-weight:700; font-size:.9rem;}
.account-details{min-width:0; flex:1; line-height:1.25; overflow:hidden;}
.account-details b{display:block; color:#fff; font-size:.83rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.account-details span{display:block; color:var(--side-muted); font-size:.72rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.st-key-logout button{width:36px; height:36px; min-height:36px; padding:0; border:1px solid var(--navy-line);
  border-radius:10px; background:transparent; color:#fff;}
.st-key-logout button:hover{background:var(--navy-2); border-color:#5266A5;}
.st-key-logout button p{font-size:.78rem; font-weight:600;}

.st-key-new_chat button{background:var(--accent); border:none; color:#fff; font-weight:600;
  border-radius:10px; padding:.6rem 1rem; width:100%; transition:background .15s;}
.st-key-new_chat button:hover{background:#4663E6;}
.st-key-new_chat button p{color:#fff !important; font-weight:600;}

.st-key-logout button{background:transparent; border:1px solid var(--navy-line); border-radius:10px;
  padding:.5rem 1rem; width:100%; transition:background .15s;}
.st-key-logout button:hover{background:var(--navy-2); border-color:#3A4B86;}
.st-key-logout button p{font-weight:600; font-size:.88rem;}

/* Chat rows: title is truncated with an ellipsis, delete icon sits in its own column. */
[class*="st-key-hist_"], [class*="st-key-active_"]{min-width:0;}
[class*="st-key-hist_"] button, [class*="st-key-active_"] button{
  background:transparent; border:none; border-radius:10px; width:100%; min-height:2.35rem;
  justify-content:flex-start; text-align:left; padding:.45rem .7rem; box-shadow:none; overflow:hidden;}
[class*="st-key-hist_"] button > div, [class*="st-key-active_"] button > div,
[class*="st-key-hist_"] [data-testid="stMarkdownContainer"], [class*="st-key-active_"] [data-testid="stMarkdownContainer"]{
  min-width:0; width:100%; overflow:hidden;}
[class*="st-key-hist_"] button, [class*="st-key-active_"] button,
[class*="st-key-hist_"] button *, [class*="st-key-active_"] button *{justify-content:flex-start !important; text-align:left !important;}
[class*="st-key-hist_"] button:hover{background:var(--navy-2);}
[class*="st-key-active_"] button{background:var(--navy-2); box-shadow:inset 3px 0 0 #6F86FF;}
[class*="st-key-hist_"] button p, [class*="st-key-active_"] button p{
  font-size:.88rem; font-weight:500; margin:0; text-align:left;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
[class*="st-key-active_"] button p{font-weight:600; color:#fff;}

section[data-testid="stSidebar"] [class*="st-key-del_"]{min-width:0;}
section[data-testid="stSidebar"] [class*="st-key-del_"] button{
  background:transparent; border:none; box-shadow:none; min-height:0; padding:.45rem .4rem; width:100%;
  justify-content:center; border-radius:10px; opacity:.55; transition:opacity .15s, background .15s;}
section[data-testid="stSidebar"] [class*="st-key-del_"] button:hover{opacity:1; background:#3A2230;}
section[data-testid="stSidebar"] [class*="st-key-del_"] button *{color:#FF9AA8;}
.confirm-text{font-size:.82rem; color:#C5CDE8; padding:.15rem .25rem 0;}
section[data-testid="stSidebar"] [class*="st-key-confirm_"] button{
  background:#C0392B; border:none; border-radius:10px; padding:.4rem .6rem; min-height:0;}
section[data-testid="stSidebar"] [class*="st-key-confirm_"] button *{color:#fff; font-weight:600; font-size:.85rem;}
section[data-testid="stSidebar"] [class*="st-key-cancel_"] button{
  background:transparent; border:1px solid #33427A; border-radius:10px; padding:.4rem .6rem; min-height:0;}
section[data-testid="stSidebar"] [class*="st-key-cancel_"] button *{font-size:.85rem;}

/* ---------- Welcome ---------- */
.hero{text-align:center; padding:2.8rem 0 1.6rem;}
.hero .mark{width:56px; height:56px; margin:0 auto 1.1rem; border-radius:16px; display:grid; place-items:center;
  font-size:1.15rem; font-weight:700; color:#fff; background:var(--accent);}
.hero h1{font-size:2rem; font-weight:700; letter-spacing:-.02em; margin:0 0 .35rem; color:var(--ink);}
.hero p{font-size:1.05rem; color:var(--muted); margin:0; font-weight:500;}
.sugg-label{font-size:.78rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); margin:.4rem 0 .6rem .2rem;}

[class*="st-key-sugg_"] button{height:auto; min-height:76px; width:100%; text-align:left; justify-content:flex-start;
  align-items:flex-start; padding:.9rem 1.05rem; background:#fff; border:1px solid var(--line); border-radius:14px;
  color:var(--ink); box-shadow:0 1px 2px rgba(20,30,70,.04); transition:border-color .15s, box-shadow .15s;}
[class*="st-key-sugg_"] button:hover{border-color:#B9C4F5; box-shadow:0 4px 14px rgba(47,75,200,.10);}
[class*="st-key-sugg_"] button p{font-size:.93rem; font-weight:600; line-height:1.45; text-align:left; color:var(--ink);}

/* ---------- Messages ---------- */
[data-testid="stChatMessage"]{background:transparent; padding:.45rem 0; gap:.7rem; align-items:flex-start;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"]{
  background:#fff; border:1px solid var(--line); border-radius:16px 16px 16px 4px;
  padding:.8rem 1.1rem; width:fit-content; max-width:92%; font-size:.97rem; line-height:1.65;
  overflow-wrap:anywhere; box-shadow:0 1px 2px rgba(20,30,70,.04);}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] p{margin:0 0 .55rem;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] p:last-child{margin-bottom:0;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] ul,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] ol{margin:.2rem 0 .55rem; padding-left:1.3rem;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] li{margin:.15rem 0;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h1,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h2,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h3,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h4{
  font-weight:700; letter-spacing:-.01em; margin:.8rem 0 .35rem; padding:0; line-height:1.3;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h1{font-size:1.25rem;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h2{font-size:1.12rem;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h3,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h4{font-size:1.02rem;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h1:first-child,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h2:first-child,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] h3:first-child{margin-top:0;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] table{
  display:block; max-width:100%; overflow-x:auto; border-collapse:collapse; font-size:.9rem; margin:.4rem 0;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] th,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] td{
  border:1px solid var(--line); padding:.45rem .7rem; text-align:left; vertical-align:top;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] th{background:var(--accent-soft); font-weight:600;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] pre{overflow-x:auto; max-width:100%;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] *{color:var(--ink);}

/* Streamlit gives message text a negative bottom margin, which clips short bubbles. */
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"]{margin-bottom:0 !important;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"]{flex:0 1 auto !important; height:auto !important; overflow:visible !important; box-sizing:border-box;}
.msg-user{display:none;}
[data-testid="stElementContainer"]:has(.msg-user), .element-container:has(.msg-user){display:none !important;}
[data-testid="stChatMessage"]:has(.msg-user) [data-testid^="stChatMessageAvatar"]{display:none !important;}

/* Keep avatar and bubble together (no big gap): pack them at the start of the row. */
[data-testid="stChatMessage"]{justify-content:flex-start !important;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"]{margin:0 !important;}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] ul:last-child,
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] ol:last-child{margin-bottom:0;}
[data-testid^="chatAvatarIcon"]{flex:0 0 auto;}
[data-testid="stChatMessage"]:has(.msg-user) [data-testid^="chatAvatarIcon"]{display:none !important;}

/* Student: right aligned */
[data-testid="stChatMessage"]:has(.msg-user){flex-direction:row-reverse;}
[data-testid="stChatMessage"]:has(.msg-user) [data-testid="stChatMessageContent"]{
  background:var(--accent); border-color:var(--accent); border-radius:16px 16px 4px 16px;
  max-width:82%; box-shadow:none;}
[data-testid="stChatMessage"]:has(.msg-user) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has(.msg-user) [data-testid="stChatMessageContent"] *{color:#fff;}

[data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"]{
  background:var(--accent-soft) !important; color:var(--accent); border-radius:10px;}

/* Typing indicator */
.typing{display:inline-flex; gap:5px; padding:.35rem 0;}
.typing span{width:7px; height:7px; border-radius:50%; background:#9AA6D8; animation:blink 1.2s infinite ease-in-out;}
.typing span:nth-child(2){animation-delay:.18s;} .typing span:nth-child(3){animation-delay:.36s;}
@keyframes blink{0%,80%,100%{opacity:.25; transform:scale(.85);} 40%{opacity:1; transform:scale(1);}}

/* ---------- Chat input (pinned to the bottom by Streamlit) ---------- */
[data-testid="stBottom"], [data-testid="stBottom"] > div{background:var(--bg);}
[data-testid="stBottomBlockContainer"]{max-width:820px; margin:0 auto; padding-bottom:1.4rem;}
[data-testid="stChatInput"]{background:#fff !important; border:1px solid var(--line); border-radius:16px;
  box-shadow:0 2px 10px rgba(20,30,70,.06);}
[data-testid="stChatInput"]:focus-within{border-color:#9FB0F2; box-shadow:0 2px 14px rgba(47,75,200,.12);}
/* remove the nested grey/white boxes inside the input */
[data-testid="stChatInput"] div, [data-testid="stChatInput"] textarea{
  background:transparent !important; border:none !important; box-shadow:none !important; outline:none !important;}
[data-testid="stChatInput"] textarea{color:var(--ink); font-family:inherit; font-size:.97rem;}
[data-testid="stChatInput"] textarea::placeholder{color:#8A91A3;}
[data-testid="stChatInput"] button{background:var(--accent) !important; color:#fff; border-radius:10px;}

/* ---------- Login ---------- */
[data-testid="stForm"]{background:#fff; border:1px solid var(--line); border-radius:16px; padding:1.5rem 1.4rem 1.3rem;
  box-shadow:0 8px 28px rgba(20,30,70,.07);}
[data-testid="stForm"] label p{font-size:.85rem; font-weight:600; color:var(--ink);}
[data-testid="stForm"] input{font-family:inherit; font-size:.95rem;}
[data-testid="stForm"] [data-baseweb="input"]{border-radius:10px;}
[data-testid="stFormSubmitButton"] button{background:var(--accent); border:none; border-radius:10px; padding:.65rem 1rem;
  font-weight:600; transition:background .15s;}
[data-testid="stFormSubmitButton"] button:hover{background:var(--accent-hover);}
[data-testid="stFormSubmitButton"] button p{color:#fff; font-weight:600;}
.login-brand{text-align:center; padding:0 0 1.4rem;}
.login-brand .mark{width:56px; height:56px; margin:0 auto 1rem; border-radius:16px; display:grid; place-items:center;
  font-size:1.15rem; font-weight:700; color:#fff; background:var(--accent);}
.login-brand .inst{font-size:.8rem; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--accent);}
.login-brand h1{font-size:1.6rem; font-weight:700; letter-spacing:-.02em; margin:.35rem 0 .3rem; padding:0; color:var(--ink);}
.login-brand p{font-size:.95rem; color:var(--muted); margin:0;}
.login-foot{text-align:center; font-size:.8rem; color:var(--muted); margin-top:1rem;}

/* ---------- Small screens ---------- */
@media (max-width:640px){
  .block-container{padding-left:1rem; padding-right:1rem; padding-top:1.6rem;}
  .hero{padding-top:1rem;} .hero h1{font-size:1.5rem;}
  [data-testid="stChatMessage"] [data-testid="stChatMessageContent"]{max-width:100%;}
  [data-testid="stChatMessage"]:has(.msg-user) [data-testid="stChatMessageContent"]{max-width:92%;}
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Demo student login (JSON-backed; passwords are PBKDF2 hashed)
# --------------------------------------------------------------------------- #
STUDENTS_FILE = Path(__file__).resolve().parent / "data" / "students.json"

def _password_matches(password: str, stored: dict) -> bool:
    try:
        salt = bytes.fromhex(stored["password_salt"])
        expected = bytes.fromhex(stored["password_hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False

def _student_record(student_id: str):
    try:
        data = json.loads(STUDENTS_FILE.read_text(encoding="utf-8"))
        return next((x for x in data.get("students", [])
                     if x.get("student_id", "").upper() == student_id.upper()), None)
    except (OSError, json.JSONDecodeError):
        return None

def _render_login():
    # Narrow, centred card on every screen size (no empty side columns to stack on mobile).
    st.markdown(
        "<style>.block-container{max-width:460px; padding-top:6vh;}</style>"
        '<div class="login-brand">'
        + logo_img("login-logo", '<div class="mark">AI</div>') +
        '<div class="inst">Adani Institute of Digital Technology Management</div>'
        "<h1>Student Support Assistant</h1>"
        "<p>Sign in with your Student ID to continue.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    with st.form("student_login", clear_on_submit=False):
        # type="password" gives Streamlit's built-in show/hide (eye) toggle.
        sid = st.text_input("Student ID", placeholder="e.g. AIDTM001", autocomplete="username")
        password = st.text_input("Password", type="password", placeholder="Enter your password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    error = None
    if submitted:
        if not sid.strip() and not password:
            error = "Please enter your Student ID and password."
        elif not sid.strip():
            error = "Please enter your Student ID."
        elif not password:
            error = "Please enter your password."
        else:
            with st.spinner("Signing you in…"):
                student = _student_record(sid.strip())
                ok = bool(student) and _password_matches(password, student)
            if ok:
                st.session_state.authenticated_student = student["student_id"].upper()
                st.session_state.student_name = student.get("name", "Student")
                st.rerun()
            error = "Invalid Student ID or password. Please try again."
    if error:
        st.error(error)
    st.markdown('<div class="login-foot">Use the Student ID and password issued by the institute.</div>', unsafe_allow_html=True)

if not st.session_state.get("authenticated_student"):
    _render_login()
    st.stop()

CURRENT_STUDENT_ID = st.session_state.authenticated_student
CURRENT_STUDENT = _student_record(CURRENT_STUDENT_ID) or {}



# --------------------------------------------------------------------------- #
# JSON storage  (data/chat_history.json)
# --------------------------------------------------------------------------- #
DATA_DIR = Path(__file__).resolve().parent / "data"
HISTORY_FILE = DATA_DIR / "chat_history.json"


@st.cache_resource(show_spinner=False)
def _file_lock() -> threading.Lock:
    return threading.Lock()


def now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _clean_conversation(cid: str, raw) -> dict | None:
    """Validate one stored conversation; return None if it is unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list):
        return None
    messages = [
        {
            "role": m["role"],
            "content": m["content"],
            "timestamp": str(m.get("timestamp") or now()),
        }
        for m in raw["messages"]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    if not messages:
        return None
    return {
        "id": cid,
        # Keep the owner when loading a conversation. Without this field,
        # the per-student filter below drops every saved chat after st.rerun().
        "student_id": str(raw.get("student_id") or "").strip().upper(),
        "title": str(raw.get("title") or "New chat")[:80],
        "created_at": str(raw.get("created_at") or messages[0]["timestamp"]),
        "updated_at": str(raw.get("updated_at") or messages[-1]["timestamp"]),
        "messages": messages,
    }


def _read_raw() -> dict:
    """Read the JSON file. Creates it if missing; sets aside a corrupted file."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not HISTORY_FILE.exists():
            HISTORY_FILE.write_text("{}", encoding="utf-8")
            return {}
        text = HISTORY_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("history file must contain a JSON object")
        return data
    except ValueError:  # invalid JSON / wrong shape / bad encoding
        try:
            HISTORY_FILE.replace(HISTORY_FILE.with_name(f"chat_history.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json"))
        except OSError:
            pass
        return {}
    except OSError:
        return {}


def _write_raw(data: dict) -> bool:
    """Atomic write: temp file in the same folder, then replace."""
    tmp_path = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".chat_history-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, HISTORY_FILE)
        return True
    except OSError:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def load_history() -> dict:
    with _file_lock():
        raw = _read_raw()
    conversations = {}
    for cid, item in raw.items():
        conv = _clean_conversation(str(cid), item)
        if conv:
            conversations[conv["id"]] = conv
    return conversations


def save_conversation(conv: dict) -> bool:
    """Insert/update one conversation without touching the others."""
    conv["student_id"] = CURRENT_STUDENT_ID
    messages = [
        {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp") or now()}
        for m in conv["messages"]
    ]
    if not messages:
        return True
    payload = {
        "id": conv["id"],
        "student_id": CURRENT_STUDENT_ID,
        "title": conv["title"],
        "created_at": conv["created_at"],
        "updated_at": conv["updated_at"],
        "messages": messages,
    }
    with _file_lock():
        raw = _read_raw()
        raw[conv["id"]] = payload
        ok = _write_raw(raw)
    if not ok:
        st.toast("Your latest message couldn't be saved. Please try again.", icon="⚠️")
    return ok


def delete_conversation(cid: str) -> bool:
    with _file_lock():
        raw = _read_raw()
        raw.pop(cid, None)
        return _write_raw(raw)


# --------------------------------------------------------------------------- #
# Session state helpers
# --------------------------------------------------------------------------- #
def make_title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 38 else text[:36].rstrip() + "…"


def _set_url_chat(cid: str | None):
    """Keep the open chat in the URL so a browser refresh returns to it."""
    if cid:
        st.query_params["chat"] = cid
    elif "chat" in st.query_params:
        del st.query_params["chat"]


def start_draft():
    """Open a fresh, empty conversation. It is saved to disk with its first message."""
    cid = uuid.uuid4().hex[:12]
    st.session_state.active_chat = cid
    st.session_state.draft_id = cid
    st.session_state.error_notice = None
    _set_url_chat(None)


def start_new_chat():
    st.session_state.delete_confirmation = None
    if st.session_state.active_chat != st.session_state.get("draft_id"):
        start_draft()  # already on an empty draft? then nothing to do


def open_chat(cid: str):
    st.session_state.delete_confirmation = None
    st.session_state.active_chat = cid
    st.session_state.error_notice = None
    _set_url_chat(cid)


def ask_delete(cid: str):
    st.session_state.delete_confirmation = cid


def cancel_delete():
    st.session_state.delete_confirmation = None


def confirm_delete(cid: str):
    delete_conversation(cid)
    st.session_state.delete_confirmation = None
    if st.session_state.active_chat == cid:
        start_draft()


if "active_chat" not in st.session_state:
    st.session_state.setdefault("delete_confirmation", None)
    st.session_state.setdefault("pending", None)
    st.session_state.setdefault("draft_id", None)
    st.session_state.setdefault("error_notice", None)
    # Only this student's chats can be restored from the URL (data isolation).
    _saved = {k: v for k, v in load_history().items() if v.get("student_id") == CURRENT_STUDENT_ID}
    _wanted = st.query_params.get("chat")
    if _wanted in _saved:
        st.session_state.active_chat = _wanted
    else:
        start_draft()

# The JSON file is the source of truth; reload it on every run.
st.session_state.conversations = {
    cid: conv for cid, conv in load_history().items()
    if conv.get("student_id") == CURRENT_STUDENT_ID
}
conversations = st.session_state.conversations
active = st.session_state.active_chat
if active not in conversations:
    if active != st.session_state.draft_id:  # e.g. deleted from another tab
        start_draft()
        active = st.session_state.active_chat
    conversations[active] = {"id": active, "student_id": CURRENT_STUDENT_ID, "title": "New chat", "created_at": now(), "updated_at": now(), "messages": []}
chat = conversations[active]


@st.cache_resource(show_spinner=False)
def get_client(api_key: str) -> Groq:
    return Groq(api_key=api_key)


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "check_student_fee_status", "description": "Look up the authenticated student's own fee status. If the user names another student ID, pass that ID so the application can deny cross-student access.", "parameters": {"type": "object", "properties": {"student_id": {"type": "string"}}, "required": ["student_id"]}}},
    {"type": "function", "function": {"name": "retrieve_payment_policy", "description": "Retrieve the assignment's illustrative payment-failure scenario policy.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
    {"type": "function", "function": {"name": "create_support_request", "description": "Create a support request for the currently authenticated student only.", "parameters": {"type": "object", "properties": {"student_id": {"type": "string"}, "issue": {"type": "string"}}, "required": ["student_id", "issue"]}}},
    {"type": "function", "function": {"name": "remember_student_fact", "description": "Save only an explicitly requested, durable, non-sensitive fact for the authenticated student. Never save passwords, OTPs, PINs, or secrets.", "parameters": {"type": "object", "properties": {"student_id": {"type": "string"}, "fact_type": {"type": "string", "enum": ["programme", "learning_preference", "communication_preference", "career_interest"]}, "value": {"type": "string"}}, "required": ["student_id", "fact_type", "value"]}}},
    {"type": "function", "function": {"name": "get_student_memory", "description": "Retrieve saved non-sensitive memory for the authenticated student only.", "parameters": {"type": "object", "properties": {"student_id": {"type": "string"}}, "required": ["student_id"]}}},
    {"type": "function", "function": {"name": "request_contact_update", "description": "Request a mobile number or email update for the authenticated student. This only creates a pending approval; it MUST NOT update the record until the student explicitly clicks Confirm update in the confirmation card.", "parameters": {"type": "object", "properties": {"student_id": {"type": "string"}, "field": {"type": "string", "enum": ["mobile_number", "email"]}, "new_value": {"type": "string"}}, "required": ["student_id", "field", "new_value"]}}},
]

TOOL_FUNCTIONS = {
    "check_student_fee_status": check_student_fee_status,
    "retrieve_payment_policy": retrieve_payment_policy,
    "create_support_request": create_support_request,
    "remember_student_fact": remember_student_fact,
    "get_student_memory": get_student_memory,
    "request_contact_update": request_contact_update,
}



def stream_reply(history: list[dict], student_id: str):
    """Agent loop: retrieve relevant context, let Groq choose tools, execute tools, then answer."""
    if not history:
        return
    client = get_client(GROQ_API_KEY)
    latest_question = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
    prior_history = history[:-1]
    result = retrieve(latest_question, prior_history)
    evidence = format_context(result.docs) if result.found else ""
    memory = get_student_memory(student_id) if student_id else {"found": False}

    system = SYSTEM_PROMPT + "\n\nYou are an agent. The authenticated Student ID is " + (student_id or "NOT PROVIDED") + ". Treat this identity as authoritative. Never reveal or modify another student's private data. If the user explicitly asks for another student's record, pass the requested ID to the tool so application-level authorization can deny it; do not substitute the current student's data. Use the authenticated identity for normal own-record requests. For contact changes, call request_contact_update to create a pending request only; clearly tell the student that an on-screen confirmation card will appear with the proposed value and Confirm update / Cancel request buttons; no change occurs until Confirm update is clicked. Never claim a contact detail has changed merely because a request was created. For support tickets, report only the ticket ID, category, and status returned by the tool. Payment-failure guidance is based on an illustrative assignment scenario, not verified official AIDTM policy. Store memory only when explicitly requested and only non-sensitive durable facts; never store OTPs, passwords, PINs, or secrets."
    if evidence:
        system += "\n\nRELEVANT VERIFIED AIDTM KNOWLEDGE:\n" + evidence
    if memory.get("found"):
        system += "\n\nSAVED NON-SENSITIVE STUDENT MEMORY:\n" + json.dumps(memory.get("facts", {}), ensure_ascii=False)

    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-MAX_CONTEXT_MESSAGES:]]
    tool_trace = []
    started_at = time.perf_counter()
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    # One decision call + at most three tool rounds prevents loops.
    for _ in range(3):
        decision = client.chat.completions.create(
            model=GROQ_MODEL, messages=messages, tools=TOOL_DEFINITIONS,
            tool_choice="auto", temperature=0.1, max_completion_tokens=1800,
        )
        if decision.usage:
            token_usage["prompt_tokens"] += decision.usage.prompt_tokens or 0
            token_usage["completion_tokens"] += decision.usage.completion_tokens or 0
        choice = decision.choices[0].message
        if not choice.tool_calls:
            final_text = choice.content or "I couldn't generate a response. Please try again."
            _write_agent_trace(latest_question, tool_trace, token_usage, started_at)
            _record_langsmith_trace(latest_question, final_text, tool_trace, token_usage, started_at)
            yield final_text
            return
        messages.append({"role": "assistant", "content": choice.content or "", "tool_calls": [tc.model_dump() for tc in choice.tool_calls]})
        for call in choice.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
                if name in {"check_student_fee_status", "create_support_request", "remember_student_fact", "get_student_memory", "request_contact_update"}:
                    args["authenticated_student_id"] = student_id
                    args.setdefault("student_id", student_id)
                if name not in TOOL_FUNCTIONS:
                    output = {"ok": False, "message": "Unknown tool."}
                else:
                    output = TOOL_FUNCTIONS[name](**args)
                tool_trace.append({"tool": name, "ok": output.get("ok", output.get("found", True))})
            except Exception as exc:
                output = {"ok": False, "message": f"Tool failed safely: {type(exc).__name__}. Please try again or contact student support."}
                tool_trace.append({"tool": name, "ok": False})
            messages.append({"role": "tool", "tool_call_id": call.id, "name": name, "content": json.dumps(output, ensure_ascii=False)})
    # Final answer after tool results; no tools offered in this final call.
    final = client.chat.completions.create(
        model=GROQ_MODEL, messages=messages, temperature=0.2,
        max_completion_tokens=1800, stream=True, stream_options={"include_usage": True},
    )
    final_answer = ""
    for chunk in final:
        if getattr(chunk, "usage", None):
            token_usage["prompt_tokens"] += chunk.usage.prompt_tokens or 0
            token_usage["completion_tokens"] += chunk.usage.completion_tokens or 0
        if chunk.choices and chunk.choices[0].delta.content:
            piece = chunk.choices[0].delta.content
            final_answer += piece
            yield piece
    _write_agent_trace(latest_question, tool_trace, token_usage, started_at)
    _record_langsmith_trace(latest_question, final_answer, tool_trace, token_usage, started_at)


def _write_agent_trace(question: str, tools_used: list[dict], tokens: dict, started_at: float):
    """Internal assignment trace; never rendered in the student UI."""
    trace_file = DATA_DIR / "agent_traces.json"
    try:
        rows = json.loads(trace_file.read_text(encoding="utf-8")) if trace_file.exists() else []
        rows.append({"timestamp": now(), "question": question[:300], "tools": tools_used,
                     "latency_seconds": round(time.perf_counter() - started_at, 3),
                     "prompt_tokens": tokens.get("prompt_tokens", 0),
                     "completion_tokens": tokens.get("completion_tokens", 0)})
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        trace_file.write_text(json.dumps(rows[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


# LangSmith trace: records the student question, final answer, tool names/statuses,
# token usage and latency. Student ID, password and raw tool arguments are never sent.
if traceable is not None:
    @traceable(name="AIDTM Student Agent", run_type="chain")
    def _langsmith_record_run(question: str, answer: str, tools_used: list[dict],
                              token_usage: dict, latency_seconds: float, model: str):
        return {
            "answer": answer,
            "tools_used": tools_used,
            "token_usage": token_usage,
            "latency_seconds": latency_seconds,
            "model": model,
        }
else:
    _langsmith_record_run = None


def _record_langsmith_trace(question: str, answer: str, tools_used: list[dict],
                            tokens: dict, started_at: float):
    """Send one internal run to LangSmith when explicitly enabled."""
    if not (LANGSMITH_TRACING and LANGSMITH_API_KEY and _langsmith_record_run):
        return
    try:
        _langsmith_record_run(
            question=question[:1000],
            answer=answer[:4000],
            tools_used=tools_used,
            token_usage={
                "prompt_tokens": int(tokens.get("prompt_tokens", 0)),
                "completion_tokens": int(tokens.get("completion_tokens", 0)),
            },
            latency_seconds=round(time.perf_counter() - started_at, 3),
            model=GROQ_MODEL,
        )
    except Exception:
        # Observability must never break the student-facing chatbot.
        pass


def friendly_error(exc: Exception) -> str:
    """Student-facing message only. Technical details are logged to the server console."""
    log.error("Assistant error: %s: %s", type(exc).__name__, exc)
    if isinstance(exc, AuthenticationError):
        return "⚠️ The assistant is temporarily unavailable. Please try again later or contact student support."
    if isinstance(exc, RateLimitError):
        return "⚠️ Too many requests right now. Please wait a few seconds and try again."
    if isinstance(exc, APIConnectionError):
        return "⚠️ I couldn't reach the AI service. Please check your internet connection and try again."
    return "⚠️ Something went wrong while generating the answer. Please try again."


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    # 1) Branding
    st.markdown(
        (f'<div class="logo-card">{logo_img("sb-logo")}</div>'
         '<div class="brand-text"><div class="name">AIDTM Assistant</div>'
         '<div class="sub">Student support</div></div>') if _logo_src() else
        ('<div class="brand"><div class="logo">AI</div>'
         '<div><div class="name">AIDTM Assistant</div>'
         '<div class="sub">Student support</div></div></div>'),
        unsafe_allow_html=True,
    )
    # 2) New chat
    st.button("＋  New Chat", key="new_chat", on_click=start_new_chat, use_container_width=True)

    # 3) Chat history: a fixed-height scrollable container, so a long list never pushes
    #    the logout button off-screen.
    st.markdown('<div class="hist-label">Your Chats</div>', unsafe_allow_html=True)
    saved = sorted(
        (c for c in conversations.values() if c["messages"]),
        key=lambda c: c["updated_at"],
        reverse=True,
    )
    with st.container(height=360, border=False):
        if not saved:
            st.markdown('<div class="hist-empty">No conversations yet</div>', unsafe_allow_html=True)
        for c in saved:
            cid = c["id"]
            title_col, del_col = st.columns([5, 1], gap="small", vertical_alignment="center")
            title_col.button(
                c["title"],
                key=f"{'active' if cid == active else 'hist'}_{cid}",
                on_click=open_chat,
                args=(cid,),
                use_container_width=True,
            )
            del_col.button("", icon=":material/delete:", key=f"del_{cid}", on_click=ask_delete, args=(cid,), help="Delete chat")
            if st.session_state.delete_confirmation == cid:
                st.markdown('<div class="confirm-text">Delete this chat?</div>', unsafe_allow_html=True)
                yes_col, no_col = st.columns(2, gap="small")
                yes_col.button("Delete", key=f"confirm_{cid}", on_click=confirm_delete, args=(cid,), use_container_width=True)
                no_col.button("Cancel", key=f"cancel_{cid}", on_click=cancel_delete, use_container_width=True)

    # 4) Account + logout at the bottom (hidden while SHOW_ACCOUNT_PANEL is False)
    if SHOW_ACCOUNT_PANEL:
        st.markdown('<div class="side-divider"></div>', unsafe_allow_html=True)
        display_name = str(CURRENT_STUDENT.get("name", "Student"))
        initials = "".join(part[0] for part in display_name.split()[:2] if part).upper() or "S"
        account_col, logout_col = st.columns([5, 1], gap="small", vertical_alignment="center")
        with account_col:
            st.markdown(
                f'<div class="account-row"><div class="account-avatar">{html.escape(initials)}</div>'
                f'<div class="account-details"><b>{html.escape(display_name)}</b>'
                f'<span>{html.escape(CURRENT_STUDENT_ID)}</span></div></div>',
                unsafe_allow_html=True,
            )
        with logout_col:
            logout_clicked = st.button("", key="logout", help="Log out", icon=":material/logout:", use_container_width=True)
        if logout_clicked:
            for key in ("authenticated_student", "student_name", "active_chat", "draft_id", "pending", "conversations", "delete_confirmation", "error_notice"):
                st.session_state.pop(key, None)
            st.query_params.clear()
            st.rerun()

# --------------------------------------------------------------------------- #
# Student contact details: submit a change request for confirmation.
# --------------------------------------------------------------------------- #
with st.expander("My Contact Details", expanded=False):
    st.caption("View or request a change to your registered mobile number and email. Changes are saved only after you confirm the approval card below.")
    mobile_current = str(CURRENT_STUDENT.get("mobile_number", "") or "")
    email_current = str(CURRENT_STUDENT.get("email", "") or "")
    st.markdown(f"**Current mobile number:** {html.escape(mobile_current) if mobile_current else 'Not provided'}")
    with st.form("mobile_update_form"):
        mobile_new = st.text_input("New mobile number", placeholder="Enter mobile number", key="contact_mobile_input")
        mobile_submit = st.form_submit_button("Request mobile number change", use_container_width=True)
    if mobile_submit:
        result = request_contact_update(CURRENT_STUDENT_ID, "mobile_number", mobile_new, CURRENT_STUDENT_ID)
        if result.get("ok"):
            st.success("Mobile number change request submitted. Review and confirm it below.")
            st.rerun()
        else:
            st.error(result.get("message", "Could not submit mobile number request."))

    st.markdown(f"**Current email address:** {html.escape(email_current) if email_current else 'Not provided'}")
    with st.form("email_update_form"):
        email_new = st.text_input("New email address", placeholder="Enter email address", key="contact_email_input")
        email_submit = st.form_submit_button("Request email change", use_container_width=True)
    if email_submit:
        result = request_contact_update(CURRENT_STUDENT_ID, "email", email_new, CURRENT_STUDENT_ID)
        if result.get("ok"):
            st.success("Email change request submitted. Review and confirm it below.")
            st.rerun()
        else:
            st.error(result.get("message", "Could not submit email request."))

# --------------------------------------------------------------------------- #
# Main area
# --------------------------------------------------------------------------- #
if not chat["messages"]:
    _first_name = (html.escape(str(CURRENT_STUDENT.get("name", "")).split(" ")[0])
                   if SHOW_ACCOUNT_PANEL and CURRENT_STUDENT.get("name") else "")
    st.markdown(
        '<div class="hero">' + logo_img("hero-logo", '<div class="mark">AI</div>') +
        f"<h1>Hello{', ' + _first_name if _first_name else ''}! Welcome to AIDTM Assistant.</h1>"
        "<p>How can I help you today?</p></div>"
        '<div class="sugg-label">Try asking</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2, gap="medium")
    for i, (icon, question) in enumerate(SUGGESTIONS):
        with cols[i % 2]:
            if st.button(f"{icon}  {question}", key=f"sugg_{i}", use_container_width=True):
                st.session_state.pending = question
                st.rerun()
else:
    for m in chat["messages"]:
        with st.chat_message(m["role"], avatar="🎓" if m["role"] == "assistant" else "🧑‍🎓"):
            if m["role"] == "user":
                st.markdown('<span class="msg-user"></span>', unsafe_allow_html=True)  # styling hook only
            st.markdown(m["content"])
    notice = st.session_state.error_notice
    if notice and notice["chat"] == active:  # temporary, never stored
        with st.chat_message("assistant", avatar="🎓"):
            st.markdown(notice["text"])

# --------------------------------------------------------------------------- #
# Student confirmation panel: only the signed-in student's own requests.
# --------------------------------------------------------------------------- #
pending_approvals = list_pending_approvals(CURRENT_STUDENT_ID)
if pending_approvals:
    st.markdown("### Confirm your contact-detail change")
    st.info("Please review the new detail carefully. It will be saved to your student record only after you click **Confirm update**. Choose **Cancel request** to leave your existing details unchanged.")
    for approval in pending_approvals:
        label = "Mobile number" if approval.get("field") == "mobile_number" else "Email address"
        current_value = CURRENT_STUDENT.get(approval.get("field"), "") or "Not provided"
        proposed_value = str(approval.get("proposed_value", ""))
        with st.container(border=True):
            st.markdown(f"**{label} update**")
            st.write(f"Current: {current_value}")
            st.write(f"New: {proposed_value}")
            st.caption(f"Request ID: {approval.get('request_id')} · Status: Awaiting your confirmation")
            yes_col, no_col = st.columns(2)
            if yes_col.button("Confirm update", key=f"approve_{approval['request_id']}", type="primary", use_container_width=True):
                result = decide_contact_update(approval["request_id"], CURRENT_STUDENT_ID, True)
                if result.get("ok"):
                    st.success(result.get("message", "Your contact detail has been updated."))
                else:
                    st.error(result.get("message", "Update could not be completed."))
                st.rerun()
            if no_col.button("Cancel request", key=f"reject_{approval['request_id']}", use_container_width=True):
                result = decide_contact_update(approval["request_id"], CURRENT_STUDENT_ID, False)
                st.warning(result.get("message", "Request cancelled. Your existing detail remains unchanged."))
                st.rerun()


prompt = st.chat_input("Ask me anything about AIDTM…")
if st.session_state.pending:
    prompt, st.session_state.pending = st.session_state.pending, None

if prompt and prompt.strip():
    prompt = prompt.strip()
    if not GROQ_API_KEY:
        log.error("GROQ_API_KEY is missing. Copy .env.example to .env, add your key and restart the app.")
        st.error("The assistant is temporarily unavailable. Please try again later or contact student support.")
        st.stop()

    # 1) Save the student's message immediately
    st.session_state.error_notice = None
    if not chat["messages"]:
        chat["title"] = make_title(prompt)
        chat["created_at"] = now()
    chat["messages"].append({"role": "user", "content": prompt, "timestamp": now()})
    chat["updated_at"] = now()
    save_conversation(chat)
    st.session_state.draft_id = None  # the draft is now a saved conversation
    _set_url_chat(chat["id"])

    with st.chat_message("user", avatar="🧑‍🎓"):
        st.markdown('<span class="msg-user"></span>', unsafe_allow_html=True)  # styling hook only
        st.markdown(prompt)

    # 2) Generate and stream the answer
    with st.chat_message("assistant", avatar="🎓"):
        box = st.empty()
        box.markdown('<div class="typing"><span></span><span></span><span></span></div>', unsafe_allow_html=True)
        reply, failed = "", False
        try:
            for token in stream_reply(chat["messages"], CURRENT_STUDENT_ID):
                reply += token
                box.markdown(reply + " ▌")
            if not reply.strip():
                raise ValueError("Empty response")
            box.markdown(reply)
        except Exception as exc:  # noqa: BLE001 – show a friendly message instead of a traceback
            reply, failed = friendly_error(exc), True
            box.markdown(reply)

    # 3) Save the answer (error notices are shown once, never stored)
    if failed:
        st.session_state.error_notice = {"chat": chat["id"], "text": reply}
    else:
        chat["messages"].append({"role": "assistant", "content": reply, "timestamp": now()})
        chat["updated_at"] = now()
        save_conversation(chat)
    st.rerun()  # refresh the sidebar order/highlight