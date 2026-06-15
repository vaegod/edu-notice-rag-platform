# AGENTS.md

## Project Goal

This project is an LLM application platform for university public information discovery, structured extraction, and evidence-grounded question answering.

It should present itself as an LLM-driven platform for university public information discovery, structured extraction, and evidence-grounded question answering.

## Core Workflow

The main product workflow is:

`natural language request -> source discovery -> candidate validation -> controlled crawling -> structured extraction -> evidence-grounded QA`

Keep these capabilities central:

- Natural language source discovery
- Candidate source validation
- Controlled crawling for `admissions_notice`, `news_center`, and `school_profile`
- Structured extraction with raw evidence
- Evidence-grounded RAG answers with citations

## Local Commands

Create and install:

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
```

Migrate database:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_db.py
```

Run service:

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Key Directories

- `app/api`: FastAPI route definitions.
- `app/services`: business logic, crawling, LLM calls, RAG, source resolution, tasks.
- `app/models`: SQLAlchemy database models.
- `app/schemas`: Pydantic request and response models.
- `app/static`: built-in management console.
- `scripts`: operational scripts and data preparation entrypoints.
- `tests`: pytest coverage for APIs and services.
- `docs`: architecture, audit, workflow, and project reports.

## Development Constraints

- Do not reintroduce Redis, Celery, vector databases, or a heavy agent framework for the local MVP.
- Keep SQLite as the default local runtime.
- Keep API keys in `.env`; never hardcode keys in source, docs, or tests.
- Prefer existing FastAPI, SQLAlchemy, and service patterns.
- When changing LLM behavior, add mock-mode tests so CI/local tests do not require a real API key.
- Do not commit `.venv`, `*.db`, `*.bak`, `__pycache__`, `.pytest_cache`, or `*.pyc`.

## Agent Working Rules

- Read `README.md` and the main development document before large changes.
- Keep the product story focused on RAG QA, citations, source traceability, and explainable workflow.
- Run `pytest` after code changes.
- Avoid expanding to new collection domains unless the user explicitly asks.
- Keep generated documents and source knowledge files deterministic enough for demos.

## Project Quality Priority

1. Evidence-grounded RAG answers.
2. Citations and raw source traceability.
3. Explainable workflow steps.
4. Clean README and project report.
5. More crawling domains only after the above are solid.
