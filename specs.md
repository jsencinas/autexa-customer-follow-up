# Project Context
Build a backend service for a service business that automatically follows up with customers after a service is completed.

Flow:
1. Before each service, the business fills out a printed "initial inspection" form (some fields are handwritten). It contains the customer's name, phone number, the service to be performed, and other details.
2. An employee takes a photo of that form and sends it via WhatsApp to the business's WhatsApp number.
3. The server receives the photo, extracts the data from it, and asks the employee to confirm the extracted data.
4. After confirmation, the server waits a configurable delay, then sends the customer a very short satisfaction survey via WhatsApp, only during business hours.
5. The customer's answers are stored and can be reviewed by the business.

# Tech Stack
- Language: Python 3.12
- Web framework: FastAPI (receives WhatsApp webhooks)
- WhatsApp: official WhatsApp Business Platform (Cloud API). Do NOT use unofficial libraries that automate a personal WhatsApp account.
- Data extraction from the photo: Local handwritten image processing model, returning structured JSON.
- Database: SQLite via SQLAlchemy (must be easy to swap for PostgreSQL later)
- Scheduling: a background worker that polls the database for due surveys (no in-memory-only timers, jobs must survive a server restart)
- Testing: pytest
- Dependency management: `uv` or `pip` with a `requirements.txt`
- Configuration: environment variables via `.env` (never hardcode secrets)

# Desired Structure
```
/app
  /api          # FastAPI routes (webhook endpoints only, no logic)
  /services     # business logic (extraction, scheduling, surveys)
  /integrations # WhatsApp client, extraction model client
  /models       # SQLAlchemy models and Pydantic schemas
  /worker       # background job that sends due surveys
/tests
main.py
.env.example
```

# Functional Requirements

## 1. Receiving inspection photos
- Expose a webhook endpoint for incoming WhatsApp messages (including the verification handshake Meta requires).
- Only accept photos from phone numbers on an allowlist of employees (configured in `.env` or a DB table). Ignore or politely reject everyone else.
- Download the image from the WhatsApp media API and store it locally (or in configurable storage).
- Webhook deliveries can be repeated by Meta. Store each processed WhatsApp message ID and ignore any message whose ID was already handled.

## 2. Extracting data
- Send the image to the local extraction model and request JSON with: `customer_name`, `customer_phone`, `service_description`, `date` and any other fields printed on the form. Each field must be nullable.
- Normalize the phone number to international E.164 format (default country code configurable).
- Validate the output with Pydantic. If the phone number is missing or invalid, flag the record for manual correction.

## 3. Duplicate inspections
- The employee may send the same inspection twice, possibly as two different photos, so comparing image files is not enough. Detect duplicates by comparing the extracted data, after normalization.
- A new record is a duplicate only if ALL of these match an existing record: `customer_name` (case-insensitive, trimmed), normalized `customer_phone`, `service_description`, and `date`. If any of them differs, treat it as a new inspection.
- Compare only against records with status `pending_confirmation`, `scheduled`, `sent`, or `answered`. Records that are `expired` or `failed` do not block a resubmission.
- On a duplicate: do not create a record, do not schedule anything, and reply to the employee that this inspection was already registered (mention its current status).
- Enforce this at the database level as well (e.g., a unique constraint or a locked check-then-insert), so two near-simultaneous messages cannot both pass.

## 4. Employee confirmation (Crucial)
- After extraction, reply to the employee on WhatsApp with the extracted name, phone, and service, and ask them to reply "OK" to confirm or send a correction.
- Do NOT schedule any survey until the employee confirms. A wrong phone number would message a stranger.
- Unconfirmed records expire after a configurable time (default: 24 hours) and are marked `expired`.

## 5. Scheduling the survey
- After confirmation, schedule the survey for `confirmed_at + SURVEY_DELAY` (configurable, default 24 hours).
- Business hours are configurable: days of the week, start and end time, and timezone (default: Mon-Sat, 09:00-18:00, timezone in `.env`).
- If the computed send time falls outside business hours, move it to the next opening time.
- The worker checks for due surveys every minute and sends them. Sending must be idempotent: never send the same survey twice, even after a crash or restart.

## 6. The survey
- Keep it very short: a rating question with quick-reply buttons (e.g., Good / Okay / Bad), followed by one optional free-text question ("Anything you'd like to tell us?").
- The first message is business-initiated, so it must use a pre-approved WhatsApp message template. Document the template text and how to register it in the README.
- Match incoming customer replies to the correct survey by phone number and status.
- All customer-facing text lives in one config/messages file so it can be edited or translated without touching logic. Default language: Spanish.

## 7. Storing and viewing results
- Store every survey with its status: `pending_confirmation`, `scheduled`, `sent`, `answered`, `expired`, `failed`.
- Provide a simple protected endpoint (`GET /surveys`) to list results, filterable by date and rating.
- Provide a way to export results as CSV.

# Code Rules (Crucial)
- No business logic inside route handlers. Routes call services.
- All external calls (WhatsApp, the extraction model) go through their own client class in `/integrations`, so they can be mocked in tests.
- Validate all external input (webhook payloads, model output) with Pydantic.
- Verify the webhook signature on every incoming WhatsApp request.
- Never log full phone numbers or message contents at INFO level.
- Use type hints everywhere and docstrings on public functions.
- Handle failures explicitly: retry transient errors with backoff, mark records `failed` after the retry limit, and never crash the worker on a single bad record.
- Every service function needs at least one test. Mock the WhatsApp and extraction model clients in tests.

# Privacy and Compliance
- Only message customers who have agreed to be contacted. Add a note in the README that the inspection form should include a consent line, and store a `consent_confirmed` flag on each record.
- Include an opt-out path: if a customer replies STOP (or equivalent), never message them again.
- Do not store inspection photos longer than a configurable retention period (default: 90 days).

# Out of Scope (for now)
- No web dashboard or frontend.
- No multi-business or multi-tenant support.
- No payment handling or appointment booking.
- No analytics beyond the CSV export.

# Deliverables
- Working code following the structure above.
- `README.md` with setup steps: creating the WhatsApp Business app, webhook configuration, environment variables, template registration, and how to run the server and the worker.
- `.env.example` listing every required variable.
- Passing test suite (`pytest`).

# Working Instructions
- Start by proposing a short implementation plan and wait for confirmation before writing code.
- Build in this order: project skeleton, database models, WhatsApp webhook, extraction, duplicate detection, confirmation flow, scheduler and worker, survey flow, results endpoint. Commit after each step.
