import httpx
import base64
import json
import logging
from app.config import settings
from app.models.schemas import ExtractedData

logger = logging.getLogger(__name__)

class ExtractionClient:
    def __init__(self):
        self.ollama_url = settings.ollama_url.rstrip('/')
        self.model = settings.vision_model_name
        
    async def extract_data_from_image(self, image_bytes: bytes) -> ExtractedData:
        """
        Sends the image to the local Ollama vision model, prompts for strict JSON,
        and parses the response into the ExtractedData Pydantic schema.
        """
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = (
            "You are an AI assistant that extracts data from handwritten service inspection forms. "
            "Analyze the provided image and extract the following fields: "
            "customer_name, customer_phone, service_description, date. "
            "Return EXACTLY a valid JSON object with these keys. If a field is illegible or missing, set its value to null. "
            "Do not include any markdown formatting, only the raw JSON object."
        )
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
            "format": "json"  # Forces JSON constraint in Ollama if the model supports it
        }
        
        # Setting a high timeout because Vision models on CPU can take ~60 seconds
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload
                )
                response.raise_for_status()
                
                result_text = response.json().get("response", "{}")
                
                # Cleanup potential markdown formatting if the model leaked it
                if result_text.startswith("```json"):
                    result_text = result_text.replace("```json", "").replace("```", "").strip()
                    
                data = json.loads(result_text)
                return ExtractedData(**data)
                
            except Exception as e:
                logger.error(f"Failed to extract data via Vision Model: {e}")
                # Return an empty schema so the flow continues,
                # letting the employee manually input the data later.
                return ExtractedData()
