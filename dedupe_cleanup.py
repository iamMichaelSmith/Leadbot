import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.conditions import Attr


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
LEADS_TABLE = os.getenv("LEADS_TABLE", "MusicLibraryLeads")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_netloc(netloc: str) -> str:
    host = (netloc or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_from_item(item: dict) -> str:
    if item.get("lead_domain"):
        return normalize_netloc(item["lead_domain"])
    email = item.get("email") or ""
    if "@" in email:
        return normalize_netloc(email.split("@", 1)[1])
    contact_url = item.get("contact_url") or ""
    if contact_url:
        return normalize_netloc(urlparse(contact_url).netloc)
    source_url = item.get("source_url") or ""
    if source_url:
        return normalize_netloc(urlparse(source_url).netloc)
    return ""


def pick_winner(items: list[dict]) -> dict:
    # Prefer contacted leads as the canonical record.
    contacted = [x for x in items if x.get("status") == "contacted"]
    pool = contacted if contacted else items

    def key_fn(x: dict):
        return (
            x.get("last_seen") or "",
            x.get("first_seen") or "",
            x.get("lead_id") or "",
        )

    return max(pool, key=key_fn)


def main() -> None:
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=AWS_REGION,
        endpoint_url=DYNAMODB_ENDPOINT_URL or None,
    )
    leads_table = dynamodb.Table(LEADS_TABLE)

    # Scan all leads (exclude suppression items).
    filter_expr = Attr("item_type").not_exists() | Attr("item_type").ne("domain_suppression")
    scan_kwargs = {
        "FilterExpression": filter_expr,
        "ProjectionExpression": "lead_id,lead_domain,email,contact_url,source_url,first_seen,last_seen,#s",
        "ExpressionAttributeNames": {"#s": "status"},
    }

    items: list[dict] = []
    start_key = None
    while True:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key
        resp = leads_table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break

    # Group by domain.
    by_domain: dict[str, list[dict]] = {}
    for item in items:
        dom = domain_from_item(item)
        if not dom:
            continue
        by_domain.setdefault(dom, []).append(item)

    now = utc_now_iso()
    skipped = 0
    for dom, group in by_domain.items():
        if len(group) < 2:
            continue
        winner = pick_winner(group)
        winner_id = winner.get("lead_id")
        for item in group:
            if item.get("lead_id") == winner_id:
                continue
            if item.get("status") == "contacted":
                continue
            leads_table.update_item(
                Key={"lead_id": item["lead_id"]},
                UpdateExpression=(
                    "SET #s = :skipped, skipped_at = :now, touched_at = :now, "
                    "touched_by = :user, dedupe_reason = :reason, dedupe_winner = :winner"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":skipped": "skipped",
                    ":now": now,
                    ":user": "dedupe_cleanup",
                    ":reason": "duplicate_domain",
                    ":winner": winner_id or "",
                },
            )
            skipped += 1

    print(f"Dedupe complete. Marked {skipped} duplicates as skipped.")


if __name__ == "__main__":
    main()
