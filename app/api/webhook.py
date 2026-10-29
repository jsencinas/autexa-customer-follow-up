"""WhatsApp webhook endpoints: verification handshake and event delivery (store and acknowledge)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.whatsapp import verify_signature
from app.models.schemas import WebhookPayload
from app.services import inbound

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/webhook")
def verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Meta's subscription handshake."""
    if mode == "subscribe" and settings.whatsapp_verify_token and token == settings.whatsapp_verify_token:
        return Response(challenge, media_type="text/plain")
    raise HTTPException(403)


@router.post("/webhook")
async def receive(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    """Verify the signature, store the events and return 200 immediately."""
    body = await request.body()
    if not verify_signature(settings.whatsapp_app_secret, body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "invalid signature")
    try:
        payload = WebhookPayload.model_validate_json(body)
    except ValidationError:
        # Acknowledge anyway: Meta would retry a payload we can never parse.
        logger.warning("unparseable webhook payload ignored")
        return {"status": "ignored"}
    await run_in_threadpool(inbound.store_webhook, db, payload, settings.whatsapp_phone_number_id)
    return {"status": "ok"}
