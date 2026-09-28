from fastapi import APIRouter, Request, Response, Query, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.models.domain import ProcessedWebhook
from app.api.security import verify_whatsapp_signature
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["whatsapp"])

@router.get("")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

from app.services.flow import handle_employee_image, handle_employee_text

@router.post("", dependencies=[Depends(verify_whatsapp_signature)])
async def receive_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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
                    
                    if msg_id:
                        exists = db.query(ProcessedWebhook).filter_by(message_id=msg_id).first()
                        if exists:
                            continue
                            
                        db.add(ProcessedWebhook(message_id=msg_id))
                        db.commit()
                        
                    if from_number:
                        normalized_from = f"+{from_number.lstrip('+')}"
                        if normalized_from not in settings.employee_phone_list:
                            continue
                            
                        # Route to background tasks based on message type
                        msg_type = msg.get("type")
                        if msg_type == "image":
                            media_id = msg.get("image", {}).get("id")
                            if media_id:
                                background_tasks.add_task(handle_employee_image, normalized_from, media_id)
                        elif msg_type == "text":
                            text_body = msg.get("text", {}).get("body", "")
                            if text_body:
                                background_tasks.add_task(handle_employee_text, normalized_from, text_body)
                                
                        logger.info(f"Routed valid {msg_type} message from {normalized_from} to background tasks.")
                    
    except Exception as e:
        logger.error(f"Error processing webhook payload: {e}")
        
    return {"status": "ok"}
