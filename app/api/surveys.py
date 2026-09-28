import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.models.domain import Inspection

router = APIRouter(prefix="/surveys", tags=["surveys"])

def verify_api_key(x_api_key: str = Header(None)):
    """Simple API key authentication for the results endpoints."""
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

@router.get("", dependencies=[Depends(verify_api_key)])
def list_surveys(
    db: Session = Depends(get_db),
    date_from: str = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: str = Query(None, description="Filter to date (YYYY-MM-DD)"),
    rating: str = Query(None, description="Filter by rating (bueno, regular, malo)"),
    status: str = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List survey results with optional filters."""
    query = db.query(Inspection)
    
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Inspection.created_at >= dt_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use YYYY-MM-DD.")
    
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            # Include the full day
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            query = query.filter(Inspection.created_at <= dt_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use YYYY-MM-DD.")
    
    if rating:
        query = query.filter(Inspection.survey_rating == rating.strip().lower())
    
    if status:
        query = query.filter(Inspection.status == status.strip())
    
    total = query.count()
    results = query.order_by(Inspection.created_at.desc()).offset(offset).limit(limit).all()
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [
            {
                "id": r.id,
                "customer_name": r.customer_name,
                "customer_phone": r.customer_phone,
                "service_description": r.service_description,
                "date": r.date,
                "status": r.status.value if r.status else None,
                "survey_rating": r.survey_rating,
                "survey_feedback": r.survey_feedback,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
            }
            for r in results
        ]
    }

@router.get("/export", dependencies=[Depends(verify_api_key)])
def export_surveys_csv(
    db: Session = Depends(get_db),
    date_from: str = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: str = Query(None, description="Filter to date (YYYY-MM-DD)"),
    rating: str = Query(None, description="Filter by rating"),
):
    """Export survey results as a downloadable CSV file."""
    query = db.query(Inspection)
    
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Inspection.created_at >= dt_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format.")
    
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(Inspection.created_at <= dt_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format.")
    
    if rating:
        query = query.filter(Inspection.survey_rating == rating.strip().lower())
    
    results = query.order_by(Inspection.created_at.desc()).all()
    
    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Customer Name", "Customer Phone", "Service", "Date",
        "Status", "Rating", "Feedback", "Created At", "Scheduled At"
    ])
    
    for r in results:
        writer.writerow([
            r.id,
            r.customer_name,
            r.customer_phone,
            r.service_description,
            r.date,
            r.status.value if r.status else "",
            r.survey_rating or "",
            r.survey_feedback or "",
            r.created_at.isoformat() if r.created_at else "",
            r.scheduled_at.isoformat() if r.scheduled_at else "",
        ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=surveys_export.csv"}
    )
