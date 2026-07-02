# Klon — AI Email Agent

An AI agent that reads incoming email, categorizes it, and writes reply drafts in the company's own voice. A human reviews every draft before it goes out, and the agent learns from the edits.

Built from scratch in Python. This repository runs in **demo mode** with seeded sample emails (`app/cli_seed.py`), so you can explore the full pipeline without connecting a real mailbox.

## What it does

```
fetch → analyze → draft → human review → learn
```

1. **Fetcher** (`app/workers/fetcher.py`) — pulls mail via Gmail API (OAuth) or IMAP (`app/connectors/`)
2. **Analyzer** (`app/workers/analyzer.py`) — categorizes each message (lead, support, invoice, spam...)
3. **Drafter** (`app/workers/drafter.py`) — writes a reply draft using:
   - **Playbooks** (`data/playbooks/playbooks.yaml`) — per-category response patterns with must-include / must-avoid rules
   - **RAG over past replies** (`app/embeddings.py`) — retrieves similar previously-approved answers so drafts sound like the company, not like a generic LLM
4. **Dashboard** (FastAPI + `app/static/`) — human approves, edits or rejects every draft
5. **Self-tuning** (`app/sender_memory.py`, `app/workers/proposer.py`) — learns per-sender context and proposes playbook improvements based on human edits
6. **Auto-reply** (`app/auto_reply.py`) — optional: once a mail category has earned trust, replies can be sent automatically instead of waiting for review (ships with `dry_run` enabled by default — human-in-the-loop is the default mode)

## Anti-hallucination by design

The agent is built around the rule "say I don't know rather than make something up":

- **Verifier** (`app/verifier.py`) — a second LLM pass that checks every draft against source facts before it reaches the dashboard
- **Sanitizer** (`app/sanitizer.py`) — strips prompt-injection attempts from incoming mail before it touches the LLM
- Playbooks forbid inventing prices, dates or promises — unknown facts become placeholders for the human to fill in

## Ops & security

- Secrets via `.env` only (pydantic-settings); IMAP passwords and OAuth refresh tokens encrypted at rest with Fernet (`app/security.py`)
- Hard monthly cost cap on LLM API spend (`api_cost_hard_limit_pln`)
- Dashboard access gated by Cloudflare Access (SSO header check)
- `scripts/backup.sh` (daily SQLite backup) and `scripts/health-check.sh` (auto-restart), designed to run under systemd
- Audit log of agent actions (`app/agent_log.py`)

## Stack

Python 3.12 · FastAPI · SQLAlchemy + SQLite · APScheduler · OpenAI API (LLM + embeddings, Anthropic-ready) · Gmail API / IMAP · Fernet encryption

## Run the demo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your OpenAI API key
python -m app.cli_seed        # init DB + seed ~47 sample emails across 3 inboxes
uvicorn app.main:app --reload # dashboard at http://localhost:8000 (mails show up as "new")
```

Optional — run the worker in a second terminal to process the seeded mail (analyzer + drafter; requires the OpenAI key in `.env`):

```bash
MAILDASH_FETCH_DISABLED=1 python -m app.workers.fetcher
```

`MAILDASH_FETCH_DISABLED=1` skips real-mailbox fetching (demo accounts have no credentials). Without the worker, seeded emails simply stay in the "new" state — the dashboard still works.

## Status

Portfolio / demo project. The system previously ran as a service (systemd workers, daily backups, health checks); this public version ships with synthetic data only — no real mailboxes or client data are included.

## Author

Adam Imielski — AI automation engineer. I build AI agents and process automations for businesses.
Contact: imielski.adam@gmail.com
