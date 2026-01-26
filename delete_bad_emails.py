import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
LEADS_TABLE = os.getenv("LEADS_TABLE", "MusicLibraryLeads")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL")


BAD_SUBSTRINGS = (".wav", ".aif", ".mp3")


def main() -> None:
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=AWS_REGION,
        endpoint_url=DYNAMODB_ENDPOINT_URL or None,
    )
    leads_table = dynamodb.Table(LEADS_TABLE)

    filter_expr = (
        Attr("email").contains(".wav")
        | Attr("email").contains(".aif")
        | Attr("email").contains(".mp3")
    )
    scan_kwargs = {
        "FilterExpression": filter_expr,
        "ProjectionExpression": "lead_id,email",
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

    skipped = 0
    for item in items:
        lead_id = item.get("lead_id")
        email = (item.get("email") or "").lower()
        if not lead_id:
            continue
        if not any(s in email for s in BAD_SUBSTRINGS):
            continue
        leads_table.update_item(
            Key={"lead_id": lead_id},
            UpdateExpression="SET #s = :skipped, skipped_at = :now, touched_at = :now, touched_by = :user, dedupe_reason = :reason",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":skipped": "skipped",
                ":now": datetime.now(timezone.utc).isoformat(),
                ":user": "invalid_email_cleanup",
                ":reason": "invalid_email_extension",
            },
        )
        skipped += 1

    print(f"Marked {skipped} leads as skipped due to invalid email extensions.")


if __name__ == "__main__":
    main()
