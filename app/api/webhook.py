from fastapi import APIRouter, Request, Response, Query, Depends, HTTPException
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

@router.post("", dependencies=[Depends(verify_whatsapp_signature)])
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
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
                            
                    logger.info(f"Received valid message {msg_id} from {from_number}")
                    
    except Exception as e:
        logger.error(f"Error processing webhook payload: {e}")
        
    return {"status": "ok"}
