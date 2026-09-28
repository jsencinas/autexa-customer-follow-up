import logging
from app.database import SessionLocal
from app.integrations.whatsapp import WhatsAppClient
from app.integrations.extraction import ExtractionClient
from app.services.inspections import check_and_create_inspection
from app.models.domain import Inspection, RecordStatus
from app.models.schemas import ExtractedData
from app.services.utils import normalize_phone_number

logger = logging.getLogger(__name__)

async def handle_employee_image(employee_phone: str, media_id: str):
    """
    Background task: Downloads image, extracts data, checks duplicates, 
    and asks the employee for confirmation.
    """
    db = SessionLocal()
    wa = WhatsAppClient()
    extractor = ExtractionClient()
    
    try:
        # 1. Download image
        try:
            image_bytes = await wa.download_media(media_id)
        except Exception as e:
            logger.error(f"Failed to download image {media_id}: {e}")
            await wa.send_text_message(employee_phone, "Sorry, I couldn't download the image from WhatsApp.")
            return

        # 2. Extract Data
        await wa.send_text_message(employee_phone, "Image received! Extracting data, this might take a minute...")
        extracted_data = await extractor.extract_data_from_image(image_bytes)
        
        # 3. Duplicate check and create
        is_dup, record = check_and_create_inspection(db, extracted_data, employee_phone)
        
        if is_dup:
            await wa.send_text_message(
                employee_phone, 
                f"⚠️ This inspection is already registered.\nCurrent status: {record.status.value}"
            )
            return
            
        # 4. Ask for confirmation
        msg = (
            "✅ *Extraction Complete*\n\n"
            f"Name: {record.customer_name}\n"
            f"Phone: {record.customer_phone}\n"
            f"Service: {record.service_description}\n"
            f"Date: {record.date}\n\n"
            "Reply *OK* to confirm and schedule the survey, or reply with corrections (e.g., 'Name is actually John')."
        )
        await wa.send_text_message(employee_phone, msg)
        
    except Exception as e:
        logger.error(f"Error in handle_employee_image: {e}")
    finally:
        db.close()

async def handle_employee_text(employee_phone: str, text: str):
    """
    Background task: Handles confirmation ('OK') or corrections for pending records.
    """
    db = SessionLocal()
    wa = WhatsAppClient()
    
    try:
        # 1. Find pending inspection for this employee
        pending = db.query(Inspection).filter(
            Inspection.employee_phone == employee_phone,
            Inspection.status == RecordStatus.pending_confirmation
        ).order_by(Inspection.created_at.desc()).first()
        
        if not pending:
            await wa.send_text_message(employee_phone, "I don't see any pending inspections waiting for your confirmation right now.")
            return
            
        text_upper = text.strip().upper()
        
        if text_upper == "OK":
            from datetime import datetime, timezone
            from app.services.scheduler import calculate_schedule_time
            
            # Confirm and schedule
            pending.status = RecordStatus.scheduled
            pending.scheduled_at = calculate_schedule_time(datetime.now(timezone.utc).replace(tzinfo=None))
            db.commit()
            
            await wa.send_text_message(
                employee_phone,
                "✅ Confirmed! The survey has been scheduled."
            )
        else:
            # Handle correction
            await wa.send_text_message(employee_phone, "Processing correction...")
            
            extractor = ExtractionClient()
            current_data = ExtractedData(
                customer_name=pending.customer_name,
                customer_phone=pending.customer_phone,
                service_description=pending.service_description,
                date=pending.date
            )
            
            updated_data = await extractor.apply_correction(current_data, text)
            
            # Apply updates
            pending.customer_name = updated_data.customer_name
            pending.customer_phone = normalize_phone_number(updated_data.customer_phone) if updated_data.customer_phone else None
            pending.service_description = updated_data.service_description
            pending.date = updated_data.date
            
            db.commit()
            
            msg = (
                "🔄 *Updated Data*\n\n"
                f"Name: {pending.customer_name}\n"
                f"Phone: {pending.customer_phone}\n"
                f"Service: {pending.service_description}\n"
                f"Date: {pending.date}\n\n"
                "Reply *OK* to confirm, or send another correction."
            )
            await wa.send_text_message(employee_phone, msg)
            
    except Exception as e:
        logger.error(f"Error in handle_employee_text: {e}")
        db.rollback()
    finally:
        db.close()
