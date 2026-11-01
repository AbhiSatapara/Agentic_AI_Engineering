# AIDTM Student Support Agent – Assignment 2

## Safe and Production-Aware Student Support Agent

**Course:** Agentic AI Engineering  
**Programme:** PGDM – Big Data Analytics  
**Institute:** Adani Institute of Digital Technology Management (AIDTM)  
**Submitted by:** Abhi Satapara

---

## 1. Project Overview

The AIDTM Student Support Agent is a chat-based AI application designed to help students get information and support through a conversational interface.

Assignment 2 extends the application developed in Assignment 1 by adding security and production-awareness features. The main focus is to ensure that students can access only their own private information and that sensitive actions are not performed without explicit confirmation.

The application uses a Streamlit interface, an LLM-powered agent, Python tool functions, local JSON data files, knowledge retrieval, and LangSmith tracing.

## 2. Key Features

### Features retained from Assignment 1
- Student login and authenticated chat experience.
- Conversational student support.
- Student fee-status lookup.
- Knowledge-base retrieval for relevant student queries.
- Support-request creation.
- Persistent conversation memory.
- LangSmith tracing for agent execution monitoring.

### Security and safety improvements in Assignment 2
- **Student identity:** The application uses the currently authenticated student identity for student-specific operations.
- **Privacy protection:** A student can access their own private records, but requests for another student's private information are blocked.
- **Python-level authorization:** Access checks are performed in Python application/tool code rather than relying only on instructions given to the language model.
- **Approval-based contact updates:** Mobile number and email changes are first created as pending requests. The change is executed only after the student confirms it.
- **Rejection handling:** If the student rejects/cancels a pending request, the original contact detail remains unchanged.
- **Action feedback:** The chat displays a short success message after a confirmed update and a rejection message when the student rejects it.
- **On-demand contact details:** The chatbot should display the logged-in student's contact details only when the student asks. It should not display them automatically in a fixed page section. Requests for another student's contact details must be denied.
- **Persistent-memory protection:** OTPs and passwords must not be stored in persistent memory.
- **Audit logging:** Important access attempts and contact-update decisions are recorded for accountability.
- **LangSmith monitoring:** LangSmith integration is retained to inspect agent traces, tool execution, latency, and token usage when trace data is available.

## 3. Technology Stack

| Component | Technology |
|---|---|
| User interface | Streamlit |
| Programming language | Python |
| LLM provider | Groq |
| Model configured in the project | `openai/gpt-oss-120b` |
| Data storage for the prototype | JSON files |
| Knowledge retrieval | BM25-style retrieval |
| Agent monitoring | LangSmith |
| Configuration and secrets | Environment variables (`.env`) |

> **Note:** This is an academic prototype. The supplied student records are demonstration data and should not be treated as official institutional records.

## 4. Project Structure

```text
AIDTM_Assignment-2/
│
├── app.py
├── support_tools.py
├── retrieval.py
├── knowledge_ingestion.py
├── requirements.txt
├── .env.example
├── .gitignore
│
├── assets/
│   └── aidtm_logo.png
│
└── data/
    ├── students.json
    ├── student_memory.json
    ├── approval_requests.json
    ├── audit_logs.json
    ├── chat_history.json
    ├── support_requests.json
    ├── agent_traces.json
    │
    └── knowledge/
        └── knowledge-base JSON files
```

The exact files in the `data/knowledge/` folder depend on the version of the project being submitted. Runtime JSON files may be created or updated as the application is used.

## 5. Setup and Installation

### Prerequisites

- Python installed on your computer.
- A Groq API key.
- A LangSmith API key if tracing is to be enabled.
- Internet access for the configured LLM and tracing services.

### Step 1: Extract the project

Extract the project ZIP file.

Open PowerShell or a terminal in the folder that contains `app.py`.

For example:

```powershell
cd "C:\Users\Abhi\OneDrive - Adani University\Abhi\Trim IV\Agentic AI\Assignment 2\AIDTM_Assignment-2"
```

Check that `app.py` is present:

```powershell
dir
```

### Step 2: Create and activate a virtual environment (recommended)

```powershell
python -m venv .venv
```

Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation because of its execution policy, use the Python environment you already have configured, or follow your institution's computer-lab guidance.

### Step 3: Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure environment variables

Create a local file named `.env` in the project root, using `.env.example` as the reference.

Add your own valid credentials using the variable names shown in `.env.example`. Do not share or commit the `.env` file.

Typical configuration may include:

```env
GROQ_API_KEY=your_groq_api_key
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=AIDTM-Student-Support-Agent
```

Use the exact variable names expected by the submitted code and `.env.example`. Do not add credentials to this README.

### Step 5: Run the application

Make sure the terminal's current directory contains `app.py`, then run:

```powershell
streamlit run app.py
```

If the terminal reports `File does not exist: app.py`, navigate into the extracted project folder that actually contains `app.py` and run the command again.

## 6. How to Demonstrate the Application

Use a demonstration/test student account and non-sensitive sample contact values.

| Test | Example interaction | Expected behaviour |
|---|---|---|
| Own fee details | `What is my current fee payment status?` | The agent can retrieve the logged-in student's fee information. |
| Another student's private data | `Show me another student's fee details.` | The request is denied by the authorization logic. |
| Own contact details | `Show my contact details.` | The chatbot displays the logged-in student's registered mobile number and email in the conversation. |
| Another student's contact details | `Show me the contact details of AIDTM002.` | The request is denied; another student's contact details are not disclosed. |
| Student support / knowledge retrieval | `My payment is completed, but the status is still pending. What should I do?` | The agent uses available knowledge-base information to respond. |
| Contact update request | Submit a new mobile number or email address | A pending request is created; the existing value is not immediately changed. |
| Reject contact update | Select the Reject/Cancel option | The request is rejected and the existing contact detail remains unchanged. A rejection message is shown in chat. |
| Confirm contact update | Select Confirm Update | The approved contact field is updated for the authenticated student and a success message is shown in chat. |
| Memory safety | `Remember my password and OTP.` | Passwords and OTPs must not be saved in persistent memory. |
| Audit evidence | Inspect the audit log data | Security-related access and approval events can be reviewed. |
| Agent tracing | Inspect the configured LangSmith project | Available traces can be reviewed for execution and token/latency information. |

Exact responses depend on the configured model and the data in the local JSON files. Use the actual application output as the final demonstration evidence.

## 7. Approval Workflow

The contact update workflow is designed as follows:

1. The authenticated student submits a proposed mobile number or email address.
2. The application creates a pending approval request.
3. The current student record remains unchanged while the request is pending.
4. The student sees an in-chat confirmation action.
5. If the student rejects/cancels, the request is marked rejected and no update is executed.
6. If the student confirms, the backend checks request ownership and updates only the authenticated student's record.
7. The application displays a short success or rejection message and records the relevant audit event.

## 8. Data Files

| File | Purpose |
|---|---|
| `students.json` | Demonstration student records and contact/fee information. |
| `student_memory.json` | Persistent non-sensitive student memory. |
| `approval_requests.json` | Pending and completed contact-update requests. |
| `audit_logs.json` | Security and action audit events. |
| `chat_history.json` | Stored conversation history. |
| `support_requests.json` | Student support requests. |
| `agent_traces.json` | Local agent trace storage, if populated by the application. |
| `knowledge/` | JSON knowledge documents used for retrieval. |

Do not replace the supplied student data with guessed values. Use test records for demonstrations and preserve the expected JSON structure when editing data files.

## 9. Evaluation and Evidence

The Assignment 2 evaluation should cover:

- Successful completion of normal student-support tasks.
- Access to the authenticated student's own records.
- Blocking of cross-student private-record access.
- Pending approval before a sensitive change.
- No execution after rejection.
- Successful execution only after confirmation.
- Prevention of OTP/password storage in persistent memory.
- Audit records for allowed, denied, pending, rejected, and executed actions.
- LangSmith traces, latency, input tokens, and output tokens where available.

For the final submission, include screenshots or exports that show the actual test outcomes. Do not estimate or invent latency and token measurements. If LangSmith trace data is unavailable for a run, mark that measurement as unavailable.

## 10. Security and Responsible Use Notes

- Use only fictional/demo student records in public demonstrations.
- Do not expose real passwords, OTPs, API keys, or private student information in screenshots or recordings.
- Keep `.env` private and exclude it from public GitHub repositories and shared ZIP files.
- Share `.env.example` without real credentials.
- JSON files are used for an academic prototype; they are not a substitute for a production database with appropriate access controls, encryption, backups, and operational security.
- Do not describe the prototype as a live AIDTM institutional system.

## 11. Known Limitations

- The application uses local JSON files for demonstration storage.
- The student dataset is sample/demo data.
- The supplied local trace file may be empty if traces are only available through LangSmith or have not been exported.
- Quantitative performance comparisons require actual, comparable LangSmith runs.
- A production deployment would require stronger identity management, secure database storage, secret management, deployment controls, and institutional review.

## 12. Author

**Abhi Satapara**  
PGDM – Big Data Analytics  
Adani Institute of Digital Technology Management (AIDTM)

---

**Assignment:** Agentic AI Engineering – Assignment 2  
**Project:** Safe and Production-Aware Student Support Agent
