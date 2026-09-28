import logging
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.domain import Inspection, RecordStatus
from app.models.schemas import ExtractedData
from app.services.utils import normalize_phone_number

logger = logging.getLogger(__name__)

def check_and_create_inspection(db: Session, data: ExtractedData) -> tuple[bool, Inspection]:
    """
    Evaluates extraction results for duplicates against active inspections.
    Returns a tuple (is_duplicate, record).
    """
    # 1. Normalize fields exactly as defined in the spec
    norm_name = data.customer_name.strip().lower() if data.customer_name else ""
    norm_phone = normalize_phone_number(data.customer_phone) if data.customer_phone else ""
    norm_service = data.service_description.strip() if data.service_description else ""
    norm_date = data.date.strip() if data.date else ""
    
    blocking_statuses = [
        RecordStatus.pending_confirmation,
        RecordStatus.scheduled,
        RecordStatus.sent,
        RecordStatus.answered
    ]
    
    try:
        # 2. Database lock and check
        # In SQLite, transactions naturally serialize writes.
        duplicate = db.query(Inspection).filter(
            func.lower(func.trim(Inspection.customer_name)) == norm_name,
            Inspection.customer_phone == norm_phone,
            func.trim(Inspection.service_description) == norm_service,
            func.trim(Inspection.date) == norm_date,
            Inspection.status.in_(blocking_statuses)
        ).first()
        
        if duplicate:
            logger.info(f"Duplicate inspection found: ID {duplicate.id} (Status: {duplicate.status})")
            return True, duplicate
            
        # 3. Create if unique
        new_record = Inspection(
            customer_name=data.customer_name.strip() if data.customer_name else None,
            customer_phone=norm_phone,
            service_description=data.service_description.strip() if data.service_description else None,
            date=data.date.strip() if data.date else None,
            status=RecordStatus.pending_confirmation
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        
        return False, new_record
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error in check_and_create_inspection: {e}")
        raise
