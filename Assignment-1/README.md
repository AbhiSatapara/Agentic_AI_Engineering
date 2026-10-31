# AIDTM Student Support Agent

A chat-style student support prototype developed for **Agentic AI
Engineering -- Assignment 1**.

## Overview

The application demonstrates an agent that can answer student questions
directly, call tools, retrieve relevant knowledge, use persistent
memory, and report uncertainty when information is unavailable.

## Features

-   Streamlit chat interface with conversation history
-   Groq model integration (`openai/gpt-oss-120b`)
-   Tool calling for fee-status lookup, payment-policy retrieval,
    support-request creation, and student memory
-   Local JSON knowledge base and BM25 keyword retrieval
-   Persistent non-sensitive student memory
-   LangSmith tracing and local execution trace logging

## Architecture

`Student → Streamlit UI → Agent / Groq model → Tool or BM25 retrieval or memory → Final response`

LangSmith captures internal execution traces, including latency, token
usage and tool information where available.

## Requirements

-   Python 3.10+
-   Groq API key
-   Optional LangSmith API key for tracing

## Setup

1.  Extract the project folder.

2.  Open a terminal in the folder containing `app.py`.

3.  Create and activate a virtual environment:

    ``` bash
    python -m venv .venv
    # Windows PowerShell
    .venv\Scripts\Activate.ps1
    # macOS/Linux
    source .venv/bin/activate
    ```

4.  Install dependencies:

    ``` bash
    pip install -r requirements.txt
    ```

5.  Copy `.env.example` to `.env` and add your own credentials. Do not
    submit `.env`.

    ``` env
    GROQ_API_KEY=your_groq_api_key
    GROQ_MODEL=openai/gpt-oss-120b
    LANGSMITH_API_KEY=your_langsmith_api_key
    LANGSMITH_TRACING=true
    LANGSMITH_PROJECT=AIDTM-Student-Support-Agent
    ```

    Use only the variables supported by the current application
    configuration. Keep all keys private.

6.  Run:

    ``` bash
    streamlit run app.py
    ```

## Project structure

``` text
AIDTM_Assignment1_Chatbot/
├── app.py
├── support_tools.py
├── retrieval.py
├── knowledge_ingestion.py
├── requirements.txt
├── .env.example
└── data/
    ├── students.json
    ├── student_memory.json
    ├── support_requests.json
    ├── chat_history.json
    ├── agent_traces.json
    └── knowledge/
        ├── about_aidtm.json
        ├── admissions.json
        ├── curriculum.json
        ├── faculty.json
        ├── fees.json
        ├── placements.json
        ├── programmes.json
        └── student_facilities.json
```

## Demonstration scenarios

1.  Ask: `What is my current fee status?`
2.  Ask: `The payment portal failed. Can I pay on the next working day?`
3.  Ask the agent to remember a programme fact, start a new chat, and
    ask what it remembers.
4.  Ask about an unsupported scholarship eligibility rule and deadline.
5.  Ask it to create a fee-payment support request.
6.  Ask a general question such as the difference between AI, ML and DL.

## Evaluation

See `AIDTM_Assignment1_Evaluation_Table.xlsx` for screenshot-supported
test cases and available LangSmith measurements. Missing or unreadable
measurements are marked as not captured; no values are estimated.

## Important limitations and responsible use

-   The student records are fictional demonstration records. The student
    ID selector is not institutional authentication.
-   Support requests are stored locally in JSON and are not sent to
    AIDTM.
-   The payment-failure policy used in the demonstration is
    illustrative/sample content, not an officially verified AIDTM
    policy.
-   Local JSON storage is not suitable for production student records.
-   Verify fee, admission, scholarship and policy details with AIDTM
    before relying on them.
-   Never commit `.env`, API keys or real student data.
