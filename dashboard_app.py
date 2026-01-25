import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
LEADS_TABLE = os.getenv("LEADS_TABLE", "MusicLibraryLeads")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL")

DASHBOARD_USERS = os.getenv("DASHBOARD_USERS", "").strip()
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
DASHBOARD_ROTATE_DAYS = int(os.getenv("DASHBOARD_ROTATE_DAYS", "5"))
DASHBOARD_PAGE_LIMIT = int(os.getenv("DASHBOARD_PAGE_LIMIT", "100"))

if not DASHBOARD_SESSION_SECRET:
    raise RuntimeError("DASHBOARD_SESSION_SECRET is required for the dashboard.")

def parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    if not raw:
        return users
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        user, pw = part.split(":", 1)
        user = user.strip()
        pw = pw.strip()
        if user and pw:
            users[user] = pw
    return users

USERS = parse_users(DASHBOARD_USERS)
if not USERS:
    raise RuntimeError("DASHBOARD_USERS is empty. Set users as 'name:pass,name2:pass2'.")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "dashboard" / "templates"
STATIC_DIR = BASE_DIR / "dashboard" / "static"

dynamodb = boto3.resource(
    "dynamodb",
    region_name=AWS_REGION,
    endpoint_url=DYNAMODB_ENDPOINT_URL or None,
)
leads_table = dynamodb.Table(LEADS_TABLE)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=DASHBOARD_SESSION_SECRET)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return utc_now().isoformat()

def require_user(request: Request) -> str | None:
    return request.session.get("user")

def scan_leads(limit: int, rotate_days: int) -> list[dict[str, Any]]:
    filter_expr = Attr("status").not_exists() | Attr("status").eq("new")

    if rotate_days > 0:
        cutoff = (utc_now() - timedelta(days=rotate_days)).isoformat()
        filter_expr = filter_expr & (Attr("touched_at").not_exists() | Attr("touched_at").lt(cutoff))

    items: list[dict[str, Any]] = []
    start_key = None
    while len(items) < limit:
        scan_kwargs = {"FilterExpression": filter_expr}
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key
        resp = leads_table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    items = items[:limit]
    for item in items:
        if not item.get("company_name"):
            src = item.get("source_url", "")
            try:
                host = urlparse(src).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    item["company_name"] = host
            except Exception:
                pass
    items.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
    return items

def update_lead(lead_id: str, updates: dict[str, Any], user: str):
    updates = {k: v for k, v in updates.items() if v is not None}
    updates["touched_at"] = now_iso()
    updates["touched_by"] = user

    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}
    parts = []
    for k, v in updates.items():
        name_key = f"#{k}"
        val_key = f":{k}"
        expr_names[name_key] = k
        expr_values[val_key] = v
        parts.append(f"{name_key} = {val_key}")

    leads_table.update_item(
        Key={"lead_id": lead_id},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

@app.get("/login")
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error},
    )

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if USERS.get(username) != password:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password"},
        )
    request.session["user"] = username
    return RedirectResponse("/", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

@app.get("/")
def dashboard(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    items = scan_leads(DASHBOARD_PAGE_LIMIT, DASHBOARD_ROTATE_DAYS)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "leads": items,
            "rotate_days": DASHBOARD_ROTATE_DAYS,
            "page_limit": DASHBOARD_PAGE_LIMIT,
        },
    )

@app.post("/lead/{lead_id}/note")
def update_note(request: Request, lead_id: str, notes: str = Form("")):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    update_lead(lead_id, {"notes": notes.strip()}, user)
    return RedirectResponse("/", status_code=302)

@app.post("/lead/{lead_id}/status")
def update_status(request: Request, lead_id: str, status: str = Form(...), notes: str = Form("")):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if status == "bad":
        status = "skipped"
    if status not in ("contacted", "skipped"):
        return RedirectResponse("/", status_code=302)
    updates = {"status": status}
    if status == "skipped":
        updates["skipped_at"] = now_iso()
    if notes is not None and notes.strip():
        updates["notes"] = notes.strip()
    update_lead(lead_id, updates, user)
    return RedirectResponse("/", status_code=302)
