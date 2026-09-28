from fastapi import FastAPI

app = FastAPI(
    title="Clients Follow-up Bot",
    description="Backend service for automated WhatsApp follow-ups",
    version="0.1.0"
)

@app.get("/health")
def health_check():
    return {"status": "ok"}
