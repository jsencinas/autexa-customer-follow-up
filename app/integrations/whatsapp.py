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
