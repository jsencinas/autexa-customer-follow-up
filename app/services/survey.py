import asyncio
import logging
from sqlalchemy.orm import Session
from app.models.domain import Inspection, RecordStatus, OptedOutPhone
from app.integrations.whatsapp import WhatsAppClient
from app.messages import MESSAGES

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

async def send_survey_to_customer(record: Inspection, db: Session):
    """
    Sends the pre-approved template survey to the customer.
    Checks opt-out list before sending. Handles retries with backoff.
    """
    wa = WhatsAppClient()
    
    # 1. Check opt-out list
    opted_out = db.query(OptedOutPhone).filter_by(phone=record.customer_phone).first()
    if opted_out:
        logger.info(f"Record {record.id}: customer {record.customer_phone} has opted out. Skipping.")
        record.status = RecordStatus.failed
        db.commit()
        return
    
    # 2. Check consent
    if not record.consent_confirmed:
        logger.warning(f"Record {record.id}: consent not confirmed. Skipping.")
        record.status = RecordStatus.failed
        db.commit()
        return
    
    # 3. Send template message with retries
    template_name = MESSAGES["survey_template_name"]
    language = MESSAGES["survey_template_language"]
    parameters = [
        record.customer_name or "Cliente",
        record.service_description or "servicio"
    ]
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await wa.send_template_message(
                to_phone=record.customer_phone,
                template_name=template_name,
                language=language,
                parameters=parameters
            )
            record.status = RecordStatus.sent
            db.commit()
            logger.info(f"Record {record.id}: survey sent successfully.")
            return
        except Exception as e:
            logger.warning(f"Record {record.id}: send attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s, 8s
            else:
                logger.error(f"Record {record.id}: all retries exhausted. Marking as failed.")
                record.status = RecordStatus.failed
                db.commit()

async def handle_customer_reply(customer_phone: str, text: str, db: Session):
    """
    Handles an incoming text reply from a customer.
    Maps it to their active survey by phone number and status.
    """
    wa = WhatsAppClient()
    
    # 1. Check for opt-out keywords
    text_lower = text.strip().lower()
    if text_lower in MESSAGES["opt_out_keywords"]:
        # Register opt-out
        existing = db.query(OptedOutPhone).filter_by(phone=customer_phone).first()
        if not existing:
            db.add(OptedOutPhone(phone=customer_phone))
            db.commit()
        
        await wa.send_text_message(customer_phone, MESSAGES["opt_out_confirmed"])
        logger.info(f"Customer {customer_phone} opted out.")
        return
    
    # 2. Find matching survey
    survey = db.query(Inspection).filter(
        Inspection.customer_phone == customer_phone,
        Inspection.status == RecordStatus.sent
    ).order_by(Inspection.scheduled_at.desc()).first()
    
    if not survey:
        # No active survey for this customer, ignore gracefully
        logger.info(f"No active survey found for customer {customer_phone}. Ignoring reply.")
        return
    
    # 3. Process response
    rating_keywords = {
        "bueno": "bueno",
        "bien": "bueno",
        "regular": "regular",
        "malo": "malo",
        "mal": "malo",
    }
    
    if not survey.survey_rating:
        # Expecting the rating answer
        matched_rating = rating_keywords.get(text_lower)
        
        if matched_rating:
            survey.survey_rating = matched_rating
            db.commit()
            # Ask the optional follow-up question
            await wa.send_text_message(customer_phone, MESSAGES["survey_followup"])
        else:
            # Treat it as a free-text rating if not a keyword
            survey.survey_rating = text.strip()
            db.commit()
            await wa.send_text_message(customer_phone, MESSAGES["survey_followup"])
    else:
        # Already have rating — this is the optional feedback
        survey.survey_feedback = text.strip()
        survey.status = RecordStatus.answered
        db.commit()
        await wa.send_text_message(customer_phone, MESSAGES["survey_thanks"])
        logger.info(f"Record {survey.id}: survey fully answered.")
