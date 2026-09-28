from fastapi import FastAPI
from app.api import webhook, surveys
from app.database import engine, Base

# Automatically create DB tables for simplicity (in a real prod app, use alembic)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Clients Follow-up Bot",
    description="Backend service for automated WhatsApp follow-ups",
    version="0.1.0"
)

app.include_router(webhook.router)
app.include_router(surveys.router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
