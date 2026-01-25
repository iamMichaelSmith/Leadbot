# Music Library Lead Finder

Music Library Lead Finder is a local-first crawler and review dashboard for discovering music library leads. It finds licensing catalogs, extracts contact signals (emails or forms), scores library relevance, and stores structured leads in DynamoDB for review. It does not send outreach.

## Requirements
- Python 3.10+
- AWS account (DynamoDB) or DynamoDB Local

## Quickstart (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python run.py
```

## Quickstart (Linux / Raspberry Pi)
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
./.venv/bin/python run.py
```

## Configure .env
Start from `.env.example` and set at least:
```
AWS_REGION=us-east-1
LEADS_TABLE=MusicLibraryLeads
PAGES_TABLE=MusicLibraryPages
DASHBOARD_USERS=admin:changeme
DASHBOARD_SESSION_SECRET=change_this_secret
```

Optional (discovery):
```
DISCOVERY_ENABLED=1
DISCOVERY_PROVIDERS=brave,serper
BRAVE_API_KEY=your_key
SERPER_API_KEY=your_key
```

Optional (local DynamoDB):
```
DYNAMODB_ENDPOINT_URL=http://localhost:8000
```

## Dashboard
Windows:
```powershell
.\.venv\Scripts\pip install -r requirements.txt
.\run-dashboard.ps1
```

Linux / Raspberry Pi:
```bash
./.venv/bin/python -m uvicorn dashboard_app:app --host 0.0.0.0 --port 8001
```

Open `http://localhost:8001` (or the port in `DASHBOARD_PORT`).

## AWS Setup (DynamoDB)
Create the two tables:
- `MusicLibraryLeads` (hash key: `lead_id` string)
- `MusicLibraryPages` (hash key: `page_url` string)

Example (AWS CLI):
```bash
aws dynamodb create-table \
  --region us-east-1 \
  --table-name MusicLibraryLeads \
  --attribute-definitions AttributeName=lead_id,AttributeType=S \
  --key-schema AttributeName=lead_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

aws dynamodb create-table \
  --region us-east-1 \
  --table-name MusicLibraryPages \
  --attribute-definitions AttributeName=page_url,AttributeType=S \
  --key-schema AttributeName=page_url,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

Make sure your AWS credentials are set (either `aws configure` or environment variables).

## Seed Validation (optional)
Validate and clean seed URLs:
```bash
python validate_seeds.py
```

Outputs:
- `seeds_working.txt`
- `seeds_failed.txt`

## Files and Outputs
- `leads_export.jsonl` (optional export if enabled)
- `discovery_state.json` (discovery progress)
- `dashboard/` (templates and static assets)

## Testing (optional)
```bash
pip install -r requirements-dev.txt
pytest
```
