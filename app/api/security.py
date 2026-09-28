import hmac
import hashlib
from fastapi import Request, HTTPException, Header
from app.config import settings

async def verify_whatsapp_signature(request: Request, x_hub_signature_256: str = Header(None)):
    if not settings.whatsapp_app_secret:
        return True
        
    if not x_hub_signature_256:
        raise HTTPException(status_code=403, detail="Missing signature")
        
    body = await request.body()
    expected_signature = hmac.new(
        settings.whatsapp_app_secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(f"sha256={expected_signature}", x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")
        
    return True
