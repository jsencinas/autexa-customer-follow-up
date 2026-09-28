from fastapi import APIRouter, Request, Response, Query, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.models.domain import ProcessedWebhook
from app.api.security import verify_whatsapp_signature
from app.services.flow import handle_employee_image, handle_employee_text
from app.services.survey import handle_customer_reply
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["whatsapp"])

@router.get("")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Handle Meta's verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("", dependencies=[Depends(verify_whatsapp_signature)])
async def receive_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Receive and route all incoming WhatsApp messages."""
    payload = await request.json()
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                
                for msg in messages:
                    msg_id = msg.get("id")
                    from_number = msg.get("from")
                    
                    # 1. Idempotency: Ignore already processed messages
                    if msg_id:
                        exists = db.query(ProcessedWebhook).filter_by(message_id=msg_id).first()
                        if exists:
                            continue
                            
                        db.add(ProcessedWebhook(message_id=msg_id))
                        db.commit()
                        
                    if not from_number:
                        continue
                        
                    normalized_from = f"+{from_number.lstrip('+')}"
                    msg_type = msg.get("type")
                    
                    # 2. Check if this is an employee (allowlisted)
                    if normalized_from in settings.employee_phone_list:
                        if msg_type == "image":
                            media_id = msg.get("image", {}).get("id")
                            if media_id:
                                background_tasks.add_task(handle_employee_image, normalized_from, media_id)
                        elif msg_type == "text":
                            text_body = msg.get("text", {}).get("body", "")
                            if text_body:
                                background_tasks.add_task(handle_employee_text, normalized_from, text_body)
                                
                        logger.info(f"Routed employee {msg_type} message to background task.")
                    else:
                        # 3. Not an employee — treat as a customer reply
                        text_body = ""
                        if msg_type == "text":
                            text_body = msg.get("text", {}).get("body", "")
                        elif msg_type == "button":
                            # Quick reply buttons from template messages
                            text_body = msg.get("button", {}).get("text", "")
                        elif msg_type == "interactive":
                            text_body = msg.get("interactive", {}).get("button_reply", {}).get("title", "")
                            
                        if text_body:
                            background_tasks.add_task(
                                _handle_customer_reply_task, normalized_from, text_body
                            )
                            logger.info("Routed customer reply to background task.")
                    
    except Exception as e:
        logger.error(f"Error processing webhook payload: {e}")
        
    return {"status": "ok"}

async def _handle_customer_reply_task(customer_phone: str, text: str):
    """Wrapper to give handle_customer_reply its own DB session."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await handle_customer_reply(customer_phone, text, db)
    except Exception as e:
        logger.error(f"Error handling customer reply: {e}")
        db.rollback()
    finally:
        db.close()
