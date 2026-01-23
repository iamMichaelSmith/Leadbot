# Leadbot

## Summary
Leadbot is a Raspberry Pi-based Python crawler that discovers music library companies, extracts contact signals (emails or contact forms), scores library relevance, and stores structured leads in DynamoDB for review. It uses Brave/Serper search discovery, respects crawl limits, and keeps a human-in-the-loop workflow with no automated outreach.

## Quickstart (Local)
```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python run.py
```

## Results (example)
- 30�60 library-focused leads per run (depending on thresholds)
- Library confidence gating to reduce non-library noise
- JSONL export for local review

## Demo data
- `samples/sample_leads.jsonl`
- `samples/sample_run.log`

## Architecture (current)
```mermaid
flowchart LR
  Q[queries.txt] --> D[Discovery: Brave/Serper]
  S[seeds.txt] --> C[Crawler]
  D --> C
  C --> F[Filters + Scoring\n(library_confidence)]
  F --> DB[(DynamoDB: LeadbotLeads)]
  F --> P[(DynamoDB: LeadbotPages)]
  F --> X[JSONL Export]
```



## Testing (optional)
```bash
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pytest
```

---

## Raspberry Pi Based Lead Discovery and Outreach System

### Project Type

Local automation system for lead discovery, qualification, and outreach review

Built for a professional recording studio environment

### Primary Goals

- Discover **music libraries / production music catalogs** and licensing contacts
- Use **public websites and directories only**
- Respect platform rules and avoid aggressive automation
- Centralize leads in a reviewable dashboard
- Allow human approval before any outreach
- Support multi engineer workflows in a studio environment

---
