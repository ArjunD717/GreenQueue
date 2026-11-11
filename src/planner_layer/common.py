"""Small helpers shared by the GreenQueue Lambda functions."""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    """Render a datetime as a stable UTC ISO-8601 string."""
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_utc(value: str) -> datetime:
    """Parse ISO-8601 input, treating a missing offset as UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, separators=(",", ":"), default=_json_default),
    }


def error_response(
    status_code: int,
    code: str,
    message: str,
    fields: dict | None = None,
) -> dict:
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return json_response(status_code, {"error": error})
