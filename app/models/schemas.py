from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.models.domain import RecordStatus

class ExtractedData(BaseModel):
    customer_name: Optional[str] = Field(None, description="The name of the customer")
    customer_phone: Optional[str] = Field(None, description="The phone number of the customer")
    service_description: Optional[str] = Field(None, description="Description of the service performed")
    date: Optional[str] = Field(None, description="The date written on the form, e.g., YYYY-MM-DD")

class InspectionCreate(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    service_description: Optional[str] = None
    date: Optional[str] = None
    consent_confirmed: bool = False

class InspectionUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    service_description: Optional[str] = None
    date: Optional[str] = None
    status: Optional[RecordStatus] = None
    scheduled_at: Optional[datetime] = None

class InspectionRead(InspectionCreate):
    id: int
    status: RecordStatus
    survey_rating: Optional[str] = None
    survey_feedback: Optional[str] = None
    created_at: datetime
    scheduled_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
