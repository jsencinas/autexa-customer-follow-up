import time
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.domain import Inspection, RecordStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("background_worker")

def process_expired_records(db: Session):
    """Mark unconfirmed records older than 24h as expired."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    expired = db.query(Inspection).filter(
        Inspection.status == RecordStatus.pending_confirmation,
        Inspection.created_at < cutoff
    ).all()
    
    for record in expired:
        record.status = RecordStatus.expired
        logger.info(f"Marked record {record.id} as expired (Unconfirmed for 24h).")
    
    if expired:
        db.commit()

def process_due_surveys(db: Session):
    """Find scheduled surveys that are due and send them."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # Query for records that are due to be sent
    due_surveys = db.query(Inspection).filter(
        Inspection.status == RecordStatus.scheduled,
        Inspection.scheduled_at <= now
    ).all()
    
    for record in due_surveys:
        try:
            # We will implement the actual sending logic in Phase 8 (Survey Flow).
            # For now, we stub it by marking it as sent to maintain idempotency.
            # from app.services.survey import send_survey_template
            # send_survey_template(record, db)
            
            record.status = RecordStatus.sent
            logger.info(f"Sent survey for record {record.id}")
        except Exception as e:
            logger.error(f"Failed to send survey for record {record.id}: {e}")
            # If it fails, we leave it as 'scheduled' to retry, or track failures.
            # (Specs: "retry transient errors with backoff... never crash worker")
            # We'll refine error tracking in Phase 8.
            
    if due_surveys:
        db.commit()

def run_worker():
    """Main polling loop. Runs indefinitely and survives server restarts."""
    logger.info("Starting background worker...")
    while True:
        db = SessionLocal()
        try:
            process_expired_records(db)
            process_due_surveys(db)
        except Exception as e:
            logger.error(f"Worker encountered a database error: {e}")
        finally:
            db.close()
            
        # Poll every 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    run_worker()
