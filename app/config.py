from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    whatsapp_verify_token: str = ""
    whatsapp_api_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_url: str = "https://graph.facebook.com/v19.0"

    allowed_employee_phones: str = ""
    
    database_url: str = "sqlite:///./app.db"
    ollama_url: str = "http://localhost:11434"
    vision_model_name: str = "llama3.2-vision"
    
    survey_delay_hours: int = 24
    business_hours_start: str = "09:00"
    business_hours_end: str = "18:00"
    business_days: str = "0,1,2,3,4,5"
    timezone: str = "America/Mexico_City"
    
    retention_days: int = 90
    default_country_code: str = "+52"
    api_key: str = "changeme"
    image_storage_path: str = "./uploads"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def employee_phone_list(self) -> List[str]:
        """Returns the allowlist as a parsed list of strings."""
        return [p.strip() for p in self.allowed_employee_phones.split(",") if p.strip()]

settings = Settings()
