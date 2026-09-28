import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, Index
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class RecordStatus(str, enum.Enum):
    pending_confirmation = "pending_confirmation"
    scheduled = "scheduled"
    sent = "sent"
    answered = "answered"
    expired = "expired"
    failed = "failed"

class Inspection(Base):
    __tablename__ = "inspections"

    id = Column(Integer, primary_key=True, index=True)
    employee_phone = Column(String, index=True, nullable=True)
    
    customer_name = Column(String, index=True, nullable=True)
    customer_phone = Column(String, index=True, nullable=True)
    service_description = Column(String, nullable=True)
    date = Column(String, nullable=True)
    
    status = Column(Enum(RecordStatus), default=RecordStatus.pending_confirmation, nullable=False)
    consent_confirmed = Column(Boolean, default=False)
    
    survey_rating = Column(String, nullable=True)
    survey_feedback = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=utcnow)
    scheduled_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index('ix_duplicate_check', 'customer_name', 'customer_phone', 'service_description', 'date'),
    )

class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"
    
    message_id = Column(String, primary_key=True)
    processed_at = Column(DateTime, default=utcnow)
