# Implementation Plan: Clients Follow-up Bot

## Context & Constraints
- **Goal:** Backend service for automated WhatsApp follow-ups after services.
- **Hardware Target:** Office computer, ~16GB RAM, CPU only.
- **Extraction Strategy:** Local Vision-Language Model (VLM) via Ollama (e.g., Llama 3.2 Vision 11B quantized). We are prioritizing reliability and accurate structured JSON extraction over processing speed, accepting that image processing may take ~1 minute on a CPU.

## Phases

### Phase 1: Project Skeleton & Setup
- Set up directory structure (`/app/api`, `/app/services`, `/app/integrations`, `/app/models`, `/app/worker`, `/tests`).
- Initialize `FastAPI` app in `main.py`.
- Create `requirements.txt` and `.env.example`.

### Phase 2: Database Models & Schemas
- Configure SQLAlchemy with SQLite.
- Create DB models (Inspections, Surveys).
- Implement Pydantic schemas for validation and API types.

### Phase 3: WhatsApp Webhook Foundation
- Implement Meta verification handshake.
- Set up webhook receiving endpoint.
- Add security: signature verification, allowlist filtering, and idempotency (tracking message IDs).

### Phase 4: Data Extraction Integration
- Build `ExtractionClient` to interface with the local Ollama vision model.
- Implement prompt construction and JSON output validation.
- Phone number normalization (E.164).

### Phase 5: Duplicate Detection Logic
- Detect duplicates based on matching normalized fields: name, phone, service, date.
- Enforce database-level safety to prevent race conditions on duplicate webhooks.

### Phase 6: Employee Confirmation Flow
- Send extraction results back to the employee for "OK" or correction.
- Handle unconfirmed records (expire after 24 hours).

### Phase 7: Scheduler & Background Worker
- Implement scheduling logic factoring in Survey Delay + Business Hours configuration.
- Create a background worker that safely polls for and processes due surveys.

### Phase 8: Customer Survey Flow
- Send pre-approved WhatsApp templates to customers.
- Track incoming replies, handle opt-outs ("STOP"), update record statuses.
- Centralize all customer-facing text in a configuration file.

### Phase 9: Results & Export Endpoints
- Build `GET /surveys` with date and rating filters.
- Implement CSV export functionality.
- Implement scheduled deletion of inspection photos (90 days retention).

### Phase 10: Polish & Documentation
- Ensure robust test coverage using `pytest` and mocked external clients.
- Finalize `README.md` with Meta app setup, template registration, and local deployment instructions.
