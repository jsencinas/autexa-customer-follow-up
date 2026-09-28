import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class WhatsAppClient:
    def __init__(self):
        self.base_url = settings.whatsapp_api_url
        self.token = settings.whatsapp_api_token
        self.phone_id = settings.whatsapp_phone_number_id
        self.headers = {
            "Authorization": f"Bearer {self.token}"
        }
    
    async def download_media(self, media_id: str) -> bytes:
        """
        Fetches the media URL from Meta and downloads the raw bytes.
        """
        async with httpx.AsyncClient() as client:
            # 1. Get Media URL
            url_resp = await client.get(
                f"{self.base_url}/{media_id}",
                headers=self.headers
            )
            url_resp.raise_for_status()
            media_url = url_resp.json().get("url")
            
            if not media_url:
                raise ValueError(f"Failed to get media URL for {media_id}")
                
            # 2. Download Media bytes
            media_resp = await client.get(
                media_url,
                headers=self.headers
            )
            media_resp.raise_for_status()
            return media_resp.content

    async def send_text_message(self, to_phone: str, text: str) -> dict:
        """
        Sends a standard text message back to the employee or customer.
        Meta API expects the phone number without the leading '+'.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone.strip("+"),
            "type": "text",
            "text": {"body": text}
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/{self.phone_id}/messages",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json()

    async def send_template_message(
        self, to_phone: str, template_name: str, language: str, parameters: list[str]
    ) -> dict:
        """
        Sends a pre-approved WhatsApp template message (business-initiated).
        Parameters are positional: {{1}}, {{2}}, etc.
        Includes quick reply buttons: Bueno / Regular / Malo.
        """
        components = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in parameters]
            }
        ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone.strip("+"),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components
            }
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/{self.phone_id}/messages",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json()
