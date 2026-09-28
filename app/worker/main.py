import time
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.domain import Inspection, RecordStatus
from app.services.survey import send_survey_to_customer

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
    
    due_surveys = db.query(Inspection).filter(
        Inspection.status == RecordStatus.scheduled,
        Inspection.scheduled_at <= now
    ).all()
    
    for record in due_surveys:
        try:
            # Run the async send function from this synchronous worker
            asyncio.run(send_survey_to_customer(record, db))
        except Exception as e:
            logger.error(f"Failed to send survey for record {record.id}: {e}")
            # Leave as 'scheduled' so it retries on next poll cycle.
            # send_survey_to_customer already handles its own retry logic
            # and will mark as 'failed' after MAX_RETRIES.

def run_worker():
    """Main polling loop. Runs independently and survives server restarts."""
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
