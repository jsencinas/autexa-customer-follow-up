import re
from app.config import settings

def normalize_phone_number(phone: str) -> str | None:
    """
    Cleans and normalizes a phone number to E.164 format.
    Uses the default country code from configuration if missing.
    """
    if not phone:
        return None
        
    # Remove all non-numeric characters
    cleaned = re.sub(r'\D', '', phone)
    
    if not cleaned:
        return None
        
    # Simple heuristic: if it's 10 digits, assume it's a local number 
    # and needs the country code prepended.
    if len(cleaned) == 10:
        cc = settings.default_country_code.strip('+')
        cleaned = f"{cc}{cleaned}"
        
    # If the user included the country code in the handwritten form, 
    # it might be > 10 digits (e.g. 12 or 13 for Mexico with +52)
    return f"+{cleaned}"
