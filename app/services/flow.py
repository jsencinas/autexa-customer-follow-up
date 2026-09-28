import logging
from app.database import SessionLocal
from app.integrations.whatsapp import WhatsAppClient
from app.integrations.extraction import ExtractionClient
from app.services.inspections import check_and_create_inspection
from app.models.domain import Inspection, RecordStatus
from app.models.schemas import ExtractedData
from app.services.utils import normalize_phone_number
from app.messages import MESSAGES

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
            await wa.send_text_message(employee_phone, MESSAGES["download_failed"])
            return

        # 2. Extract Data
        await wa.send_text_message(employee_phone, MESSAGES["image_received"])
        extracted_data = await extractor.extract_data_from_image(image_bytes)
        
        # 3. Duplicate check and create
        is_dup, record = check_and_create_inspection(db, extracted_data, employee_phone)
        
        if is_dup:
            await wa.send_text_message(
                employee_phone, 
                MESSAGES["duplicate_found"].format(status=record.status.value)
            )
            return
            
        # 4. Ask for confirmation
        msg = MESSAGES["extraction_complete"].format(
            customer_name=record.customer_name,
            customer_phone=record.customer_phone,
            service_description=record.service_description,
            date=record.date
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
            await wa.send_text_message(employee_phone, MESSAGES["no_pending"])
            return
            
        text_upper = text.strip().upper()
        
        if text_upper == "OK":
            from datetime import datetime, timezone
            from app.services.scheduler import calculate_schedule_time
            
            # Confirm and schedule
            pending.status = RecordStatus.scheduled
            pending.consent_confirmed = True
            pending.scheduled_at = calculate_schedule_time(datetime.now(timezone.utc).replace(tzinfo=None))
            db.commit()
            
            await wa.send_text_message(employee_phone, MESSAGES["confirmed"])
        else:
            # Handle correction
            await wa.send_text_message(employee_phone, MESSAGES["correction_processing"])
            
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
            
            msg = MESSAGES["correction_applied"].format(
                customer_name=pending.customer_name,
                customer_phone=pending.customer_phone,
                service_description=pending.service_description,
                date=pending.date
            )
            await wa.send_text_message(employee_phone, msg)
            
    except Exception as e:
        logger.error(f"Error in handle_employee_text: {e}")
        db.rollback()
    finally:
        db.close()
