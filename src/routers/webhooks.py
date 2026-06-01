import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.errors import UniqueViolation
from svix.webhooks import Webhook, WebhookVerificationError

from core.database import get_db

router = APIRouter()
log = logging.getLogger(__name__)


def _primary_email(data: Dict[str, Any]) -> Optional[str]:
    email_addresses = data.get("email_addresses")
    if not isinstance(email_addresses, list):
        return None

    primary_email_id = data.get("primary_email_address_id")
    for email_entry in email_addresses:
        if email_entry.get("id") == primary_email_id:
            return email_entry.get("email_address")

    if email_addresses:
        return email_addresses[0].get("email_address")

    return data.get("email_address")


def _username(data: Dict[str, Any], email: str) -> str:
    username = data.get("username")
    if username:
        return username

    return email.split("@", 1)[0]


@router.post("/clerk")
async def clerk_webhook(request: Request, conn=Depends(get_db)):
    webhook_secret = os.getenv("CLERK_WEBHOOK_SECRET")
    if not webhook_secret:
        log.error("CLERK_WEBHOOK_SECRET environment variable is not set")
        raise HTTPException(status_code=500, detail="Webhook secret is not configured")

    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    try:
        event = Webhook(webhook_secret).verify(payload, headers)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid Clerk webhook signature") from exc

    if event.get("type") != "user.created":
        return {"status": "ignored"}

    data = event.get("data", {})
    clerk_id = data.get("id")
    email = _primary_email(data)
    if not clerk_id or not email:
        raise HTTPException(status_code=400, detail="Missing Clerk user id or email")

    username = _username(data, email)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (clerk_id, username, email)
                VALUES (%s, %s, %s)
                ON CONFLICT (clerk_id) DO UPDATE
                SET username = EXCLUDED.username,
                    email = EXCLUDED.email
                """,
                (clerk_id, username, email),
            )
        conn.commit()
    except UniqueViolation as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail="User email or username already exists") from exc
    except Exception:
        conn.rollback()
        log.exception("Failed to persist Clerk user")
        raise

    return {"status": "ok"}
