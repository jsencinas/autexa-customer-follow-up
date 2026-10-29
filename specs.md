# Project Context
Build a backend service for a service business that automatically follows up with customers after a service is completed.

Flow:
1. Before each service, the business fills out a printed "initial inspection" form (some fields are handwritten). It contains the customer's name, phone number, the service to be performed, and other details.
2. An employee opens the business's web app on the company phone, logs in, and takes (or attaches) a photo of that form.
3. The server extracts the data from the photo and shows it in an editable form next to the photo. The employee corrects anything wrong and presses **Confirm**.
4. After confirmation, the server waits a configurable delay, then sends the customer a very short, personalized satisfaction survey via WhatsApp from the company number, only during the sending hours configured in the admin section.
5. The customer's answers are interpreted and stored. An admin can see live charts of the results in the web app and receives a monthly email report.

## Shared company number
- The business has a single WhatsApp number. Employees keep using it in the **WhatsApp Business app** on the company phone for quotes, prices and everyday customer chat. That part is NOT automated.
- The bot uses the **same number** through the Cloud API, which requires Meta's **Coexistence** mode (Business app and Cloud API on the same number).
- Because a number cannot message itself, employees never talk to the bot over WhatsApp. Intake and confirmation happen in the web app.

## Bot scope
- The bot only sends survey-related messages (the survey template, the follow-up question, a thank-you, and the opt-out confirmation) and only processes replies to those surveys.
- The bot **never** auto-replies to, greets, or rejects any other message. Everything else is handled by employees in the Business app.
- Messages sent by the bot appear in the app's chat history, so employees can see what was sent.

# Tech Stack
- Language: Python 3.12
- Web framework: FastAPI (WhatsApp webhooks, employee web app, admin section)
- Web UI: server-rendered Jinja2 pages, mobile-first. Charts with Chart.js served as a local static file. No SPA framework.
- WhatsApp: official WhatsApp Business Platform (Cloud API), onboarded via **Coexistence** (Embedded Signup with the existing Business app number). Do NOT use unofficial libraries that automate a personal WhatsApp account.
- Data extraction from the photo: local handwritten image processing model, returning structured JSON.
- Reply interpretation: local LLM served by **Ollama**, returning schema-constrained JSON.
- Email: SMTP. Charts for emails rendered server-side with matplotlib.
- Database: SQLite via SQLAlchemy (must be easy to swap for PostgreSQL later)
- Scheduling: a background worker that polls the database for due work (no in-memory-only timers, jobs must survive a server restart)
- Testing: pytest
- Dependency management: `uv` or `pip` with a `requirements.txt`
- Configuration: environment variables via `.env` (never hardcode secrets). Settings editable in the admin section live in the database; `.env` only provides their initial defaults.

# Desired Structure
```
/app
  /api          # WhatsApp webhook routes (no logic)
  /web          # employee and admin web routes (no logic)
  /templates    # Jinja2 templates
  /static       # CSS, Chart.js and other assets
  /services     # business logic (extraction, duplicates, scheduling, surveys,
                #   template_selection, reply_interpretation, opt_out, settings, reports)
  /integrations # WhatsApp client, extraction model client, LLM (Ollama) client, email client
  /models       # SQLAlchemy models and Pydantic schemas
  /worker       # background jobs (send surveys, interpret replies, expirations, monthly report, retention)
/tests
main.py
.env.example
```

# Functional Requirements

## 1. Users and photo intake (web app)
- Users table with username, hashed password (argon2 or bcrypt), and role: `employee` or `admin`. The first admin is created with a CLI command; admins can create employee accounts.
- Session cookies, CSRF protection on every form, and rate limiting on login.
- `GET/POST /inspections/new`: upload form (`<input type="file" accept="image/*" capture="environment">`). Validate file type and size, and store the image locally (or in configurable storage).
- Extraction runs in the background. The page shows "processing" and refreshes until the result is ready, so a slow local model never blocks the request.

## 2. Extracting data
- Send the image to the local extraction model and request JSON with: `customer_name`, `customer_phone`, `service_description`, `date` and any other fields printed on the form. Each field must be nullable.
- For each field the model returns a value and a confidence (`high` / `low`), or `null` when it cannot read the field. The prompt instructs it never to guess, especially phone digits.
- Normalize the phone number to international E.164 format (default country code configurable).
- Validate the output with Pydantic. Store the raw model output on the record for debugging (never log it at INFO level).

### Illegible or unreadable forms
- The confirm page highlights fields that are missing, low-confidence, or invalid, and shows the photo (zoomable) next to the form so the employee can read the handwriting.
- **Whole photo unreadable** (blurry, dark, cut off, not a form, or most fields null): show "We couldn't read this photo" with short retake tips, a **Retake photo** button (replaces the image on the same record), and an **Enter manually** option.
- **Model failure or timeout:** retry with backoff; after the retry limit, open the same form empty for manual entry. A broken model must never block an inspection.
- **Discard:** the employee can discard a record (status `discarded`).

## 3. Duplicate inspections
- The employee may upload the same inspection twice, possibly as two different photos, so comparing image files is not enough. Detect duplicates by comparing the extracted data, after normalization.
- A new record is a duplicate only if ALL of these match an existing record: `customer_name` (case-insensitive, trimmed), normalized `customer_phone`, `service_description`, and `date`. If any of them differs, treat it as a new inspection.
- Compare only against records with status `pending_confirmation`, `scheduled`, `sent`, or `answered`. Records that are `expired`, `failed`, `discarded`, or `opted_out` do not block a resubmission.
- Check after extraction and again on Confirm (the employee's corrections can turn a record into a duplicate).
- On a duplicate: do not create (or confirm) the record, do not schedule anything, and show on the page that this inspection was already registered, with its current status.
- Enforce this at the database level as well (e.g., a unique constraint or a locked check-then-insert), so two near-simultaneous uploads cannot both pass.

## 4. Employee confirmation (Crucial)
- `GET/POST /inspections/{id}/confirm`: editable form with the extracted name, phone, service and date. Edits go through the same normalization and validation.
- Required before Confirm: `customer_name` and a valid E.164 `customer_phone`. Other fields are optional; `date` defaults to the upload date. The Confirm button stays disabled until the required fields are valid.
- Field **"Service as shown to the customer"**: prefilled from `service_description`, sanitized (single line, no tabs or newlines, max 60 characters), editable. This is the value used in survey messages, so messy handwriting never reaches the customer.
- If the phone number is on the opt-out list, show "This customer opted out, so no survey will be sent" and store the record with status `opted_out`.
- Do NOT schedule any survey until the employee confirms. A wrong phone number would message a stranger.
- `GET /inspections?status=pending_confirmation` lists unfinished records so they can be completed later.
- Unconfirmed records expire after a configurable time (default: 24 hours) and are marked `expired`.

## 5. Scheduling the survey
- After confirmation, schedule the survey for `confirmed_at + SURVEY_DELAY` (configurable, default 24 hours).
- **Sending hours are configured in the admin section** and stored in the database (`.env` only provides the initial defaults): per weekday on/off with start and end time, plus the timezone. Default: Mon-Sat, 09:00-18:00.
- The worker checks the sending window **at send time** against the current settings. If it is closed, it moves `scheduled_at` to the next opening time. This way, changes to the hours apply immediately to already-scheduled surveys.
- **Quiet period:** before sending, check whether the customer or an employee exchanged messages with that number in the last `SURVEY_QUIET_PERIOD` (configurable, default 2 hours, based on webhook timestamps). If so, postpone to the next allowed time so the survey doesn't interrupt a live conversation.
- Respect Coexistence throughput limits with a configurable send rate.
- The worker checks for due surveys every minute and sends them. Sending must be idempotent: never send the same survey twice, even after a crash or restart.

## 6. The survey
- Keep it very short: a rating question with quick-reply buttons (e.g., Good / Okay / Bad), followed by one optional free-text question ("Anything you'd like to tell us?").
- All customer-facing text lives in one config/messages file so it can be edited or translated without touching logic. Default language: Spanish.

### Personalized, rotating templates
- The first message is business-initiated, so it must use a pre-approved WhatsApp message template. Register **several template variants** (e.g., `survey_v1` … `survey_v4`), each with parameters `{{1}}` = customer first name and `{{2}}` = service as shown to the customer, plus the rating quick-reply buttons and an opt-out button.
- The variants (template name, language, parameter order) are listed in the messages file. Document each variant's text and how to register it in the README, including that Meta may categorize survey templates as *marketing* (affects pricing and opt-out button behavior).
- **Selection:** pick a random variant among those not used in that phone number's last N surveys (N = number of variants − 1), so a customer with several services receives different wording each time. Store `template_name` on the survey record. If only one variant is approved, use it.
- Follow-up messages (free-text question, thank-you, opt-out confirmation) are sent inside the 24h customer service window, so they are free-form text. Each has several variants in the messages file and rotates the same way.

## 7. Interpreting customer replies
Customers don't stick to the buttons: they add text to the rating, write things unrelated to the survey, and ask to stop in their own words. Replies must be interpreted, not matched against exact commands.

1. **Store and acknowledge:** the webhook verifies the signature, validates the payload, stores the message in `inbound_messages` (deduplicated by WhatsApp message ID), and returns 200 immediately. No interpretation happens inside the request.
2. **Asynchronous processing:** the worker interprets stored messages. If Ollama is unavailable, messages remain queued and are retried with backoff. Nothing is lost.
3. **Gate:** only messages from numbers that received a survey in the last `INTERPRETATION_WINDOW` (default 7 days) are interpreted. Everything else is ordinary chat for the employees and never reaches the LLM.
4. **Rules layer (deterministic, first):** button payloads (rating or opt-out, carrying the survey ID); plain ratings ("bien", "regular", "mal", digits, 👍/👎); obvious opt-out phrases. Phrase lists live in the messages file.
5. **LLM layer (only when the rules can't decide):** a local model on Ollama, called with a JSON schema (Ollama's `format` parameter), output validated with Pydantic:
   - `intents`: one or more of `rating`, `comment`, `opt_out`, `unrelated` (a message can have several, e.g. "Bien, pero no me escribáis más")
   - `rating`: `good` / `okay` / `bad` / null
   - `comment`: string or null
   - `confidence`: 0-1

   The prompt includes the bot's last message and the customer's reply, clearly marked as data, not instructions.
6. **Decision policy** (deliberately asymmetric: missing an opt-out is far worse than a false one):
   - `opt_out` with confidence ≥ `OPT_OUT_THRESHOLD` (default 0.5) → apply the opt-out.
   - `rating` with confidence ≥ `RATING_THRESHOLD` (default 0.8) → store it; below that → mark the message `needs_review`.
   - `comment` → stored only while the survey is waiting for free text and the free-text window is open (default 2 hours after the question). The window closes as soon as an employee manually messages that customer (detected via `smb_message_echoes` webhooks).
   - `unrelated` → ignored: no reply, nothing stored.
   - Once an employee has joined the conversation, rating/comment interpretation stops, but opt-out detection keeps running.
7. The LLM **never writes messages**. Every bot reply comes from the messages file.
- Store the interpretation result on each inbound message: source (`rule` / `llm` / `admin`), intents, confidence, model name.
- Model configurable via `OLLAMA_URL` and `REPLY_MODEL` (suggested: a 7-8B instruct model with good Spanish, e.g. Qwen 2.5/3 7B or Llama 3.1 8B). The same Ollama server may host the vision extraction model.
- Evaluation: `tests/fixtures/replies_es.jsonl` with labelled Spanish replies (ratings with extra text, unrelated messages, opt-outs in many wordings, mixed intents) and a separate eval script that measures accuracy against the real model. Unit tests mock the client.

## 8. Opt-out list
- `opt_outs` table: normalized E.164 phone (unique), `opted_out_at`, source (`button` / `interpreted` / `admin`), and the message or survey that triggered it.
- Entry paths: the template's opt-out button, a reply interpreted as opt-out (section 7), the admin review queue, or an admin adding a number manually.
- Effects: cancel any `scheduled` surveys for that number (status `opted_out`) and send one opt-out confirmation (from the messages file). **The worker re-checks the list right before every send**, so a survey scheduled before the opt-out is never sent.
- The list is stored separately from survey data and is **never** deleted by retention cleanup.
- Admins can view the list and remove an entry only with a recorded reason (e.g., the customer asked to opt back in).
- Opt-out only stops bot messages. Employees can still chat with the customer manually.

## 9. Storing results and admin section
- Store every survey with its status: `pending_confirmation`, `scheduled`, `sent`, `answered`, `expired`, `failed`, `discarded`, `opted_out`.
- Admin pages are restricted to the `admin` role, with a shorter session timeout and password re-entry before changing settings.
- **Dashboard** (`/admin`), filterable by date range and service: rating distribution, ratings over time (weekly), response rate (sent vs answered), ratings by service, latest free-text comments.
- **Real-time updates:** the dashboard polls a JSON endpoint (`/admin/api/stats`) every 30 seconds and updates the charts.
- **Review queue:** lists `needs_review` replies. The admin can set the rating, mark the reply unrelated, or apply an opt-out. Each decision is stored with who made it and when.
- **Settings:** sending hours (section 5), monthly report recipients, and the day/time the report is sent. Every change is recorded in an audit log (who, when, old → new value).
- `GET /surveys` (JSON, admin-only, filterable by date and rating) and CSV export, both accessible from the dashboard.

## 10. Monthly email report
- Sent on day 1 of each month at a configurable time (business timezone), covering the previous month. Triggered by the worker.
- Content: HTML email with the dashboard charts rendered as PNG images (matplotlib), key figures, and comparison with the previous month. Comments are included without phone numbers. An optional CSV attachment is off by default, to limit personal data in email.
- Recipients are managed in the admin settings. SMTP credentials live in `.env`.
- `monthly_reports` table with a unique `(year, month)` so a restart never sends the report twice. Retry with backoff, then mark `failed`. The admin can resend it manually from the dashboard.

# Code Rules (Crucial)
- No business logic inside route handlers. Routes call services.
- All external calls (WhatsApp, the extraction model, the Ollama LLM, SMTP) go through their own client class in `/integrations`, so they can be mocked in tests.
- Validate all external input (webhook payloads, form submissions, model and LLM output) with Pydantic.
- Verify the webhook signature on every incoming WhatsApp request.
- Never log full phone numbers or message contents at INFO level.
- Use type hints everywhere and docstrings on public functions.
- Handle failures explicitly: retry transient errors with backoff, mark records `failed` after the retry limit, and never crash the worker on a single bad record.
- Every service function needs at least one test. Mock the WhatsApp, extraction model, LLM and email clients in tests.
- Required tests include:
  - Confirm is blocked when the name or phone is missing; manual entry works when the extraction model fails.
  - A non-survey message gets no reply.
  - Free text after an employee's manual message is not stored as a comment.
  - The quiet period postpones a survey.
  - An opted-out number is never messaged, including a survey scheduled before the opt-out.
  - Template rotation never repeats the previous variant for the same customer.
  - Changed sending hours apply to already-scheduled surveys.
  - The monthly report is not sent twice after a restart.
  - Admin routes reject the `employee` role.
  - Reply interpretation: a rating with extra text is stored; an unrelated message is ignored; an opt-out in free wording is applied; a low-confidence rating goes to `needs_review`; a message from a number without a recent survey never reaches the LLM; messages are retried while the LLM is down.

# Privacy and Compliance
- Include an opt-out path (section 8): once a customer asks to stop, in any wording, the bot never messages them again.
- Do not store inspection photos longer than a configurable retention period (default: 90 days). Retention cleanup never touches the opt-out list.

# Out of Scope (for now)
- No frontend beyond the employee intake pages and the admin section described above.
- No automation of regular customer conversations (quotes, prices, etc.); those stay manual in the WhatsApp Business app.
- No multi-business or multi-tenant support.
- No payment handling or appointment booking.
- No analytics beyond the admin dashboard, the monthly report, and the CSV export.

# Deliverables
- Working code following the structure above.
- `README.md` with setup steps: creating the WhatsApp Business app, **Coexistence onboarding and its prerequisites** (the company phone must keep the Business app active), webhook configuration, environment variables, registering the template variants, setting up Ollama and the models, SMTP setup, creating the first admin, and how to run the server and the worker.
- `.env.example` listing every required variable.
- Passing test suite (`pytest`).

# Working Instructions
- Start by proposing a short implementation plan and wait for confirmation before writing code.
- Build in this order: project skeleton, database models, auth and roles, web upload, extraction (including illegible handling), duplicate detection, web confirmation flow, settings (sending hours), scheduler and worker, WhatsApp webhook (store-and-ack) and survey flow with template rotation, reply interpretation (rules + Ollama) and opt-out, admin review queue, admin dashboard with live stats, monthly email report. Commit after each step.
