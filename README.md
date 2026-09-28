# Autexa Customer Follow-Up Bot

Automated WhatsApp survey system for service businesses. After a service is completed, an employee photographs the inspection form, the system extracts the data using a local AI vision model, and schedules an automatic satisfaction survey to the customer via WhatsApp.

## Architecture

```
Employee takes photo → Sends to Bot WhatsApp number
  → Server extracts data (Ollama Vision Model)
  → Employee confirms via WhatsApp ("OK")
  → Survey scheduled after configurable delay
  → Background worker sends survey during business hours
  → Customer replies with rating + optional feedback
  → Results viewable via API or CSV export
```

## Prerequisites

- **Python 3.12+**
- **Ollama** installed and running locally ([ollama.com](https://ollama.com))
- A **Meta Business Account** with WhatsApp Business Platform access
- A **dedicated phone number** for the bot (separate from the main business phone)

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd autexa-customer-follow-up
pip install -r requirements.txt
```

### 2. Install and configure Ollama

```bash
# Install Ollama (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull the vision model (this may take a while)
ollama pull llama3.2-vision
```

The model runs entirely on CPU. Extraction may take 30-60 seconds per image, which is fine for this workflow.

### 3. Create the WhatsApp Business App

1. Go to [Meta for Developers](https://developers.facebook.com/) and create a new app (type: **Business**).
2. Add the **WhatsApp** product to the app.
3. In **WhatsApp > Getting Started**, note down:
   - **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - **Temporary Access Token** (or generate a permanent System User Token) → `WHATSAPP_API_TOKEN`
4. In **App Settings > Basic**, note down:
   - **App Secret** → `WHATSAPP_APP_SECRET`

### 4. Register the WhatsApp Message Template

Go to **WhatsApp > Message Templates** in the Meta Business Manager and create a new template:

- **Template name:** `satisfaction_survey`
- **Category:** Marketing or Utility
- **Language:** Spanish (es)
- **Body text:**
  ```
  Hola {{1}}, gracias por utilizar nuestros servicios de {{2}}.
  Nos encantaría conocer tu opinión. ¿Cómo calificarías tu experiencia?
  ```
- **Buttons (Quick Reply):**
  - `Bueno`
  - `Regular`
  - `Malo`

Wait for Meta to approve the template before sending surveys.

### 5. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your actual values. Key variables:

| Variable | Description |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | Any random string you choose for webhook verification |
| `WHATSAPP_API_TOKEN` | Your Meta API token (permanent System User token recommended) |
| `WHATSAPP_APP_SECRET` | App secret from Meta dashboard (used to verify webhook signatures) |
| `WHATSAPP_PHONE_NUMBER_ID` | The phone number ID from Meta dashboard |
| `ALLOWED_EMPLOYEE_PHONES` | Comma-separated E.164 phone numbers of employees (e.g., `+521234567890`) |
| `OLLAMA_URL` | URL of your local Ollama instance (default: `http://localhost:11434`) |
| `VISION_MODEL_NAME` | The Ollama model to use (default: `llama3.2-vision`) |
| `SURVEY_DELAY_HOURS` | Hours to wait after confirmation before sending survey (default: `24`) |
| `TIMEZONE` | Your local timezone (default: `America/Mexico_City`) |
| `API_KEY` | Secret key to protect the `/surveys` endpoint |
| `DEFAULT_COUNTRY_CODE` | Country code for normalizing local phone numbers (default: `+52`) |

### 6. Configure the webhook

Your server must be publicly reachable. Use a tool like [ngrok](https://ngrok.com) for development:

```bash
ngrok http 8000
```

Then in Meta's WhatsApp dashboard:

1. Go to **WhatsApp > Configuration > Webhook**
2. Set **Callback URL** to `https://your-domain.com/webhook`
3. Set **Verify Token** to the same value as your `WHATSAPP_VERIFY_TOKEN`
4. Subscribe to the **messages** field

## Running

### Start the web server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Start the background worker (separate terminal)

```bash
python -m app.worker.main
```

The worker polls every 60 seconds for:
- Surveys that are due to be sent
- Unconfirmed inspections to expire (after 24h)
- Old inspection photos to delete (after 90 days)

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Health check |
| `GET` | `/webhook` | None | Meta webhook verification handshake |
| `POST` | `/webhook` | Signature | Receives WhatsApp messages |
| `GET` | `/surveys` | `X-API-Key` | List surveys (filterable by date, rating, status) |
| `GET` | `/surveys/export` | `X-API-Key` | Download surveys as CSV |

### Query parameters for `/surveys`

- `date_from` — Filter from date (YYYY-MM-DD)
- `date_to` — Filter to date (YYYY-MM-DD)
- `rating` — Filter by rating (`bueno`, `regular`, `malo`)
- `status` — Filter by status (`pending_confirmation`, `scheduled`, `sent`, `answered`, `expired`, `failed`)
- `limit` — Results per page (default: 100, max: 500)
- `offset` — Pagination offset

## Running Tests

```bash
pytest tests/ -v
```

## Phone Number Setup (Important)

This system uses **Option 1: Dedicated Bot Number**. You need **two** phone numbers:

1. **Main business phone** — The employee keeps using this phone normally with the WhatsApp Business app to chat with customers.
2. **Dedicated bot number** — A separate number registered with the WhatsApp Cloud API. The employee sends inspection photos *to this number*. The bot replies to the employee for confirmation and sends surveys to customers from this number.

> ⚠️ **A phone number cannot be active on both the WhatsApp app and the Cloud API simultaneously.** Never migrate your main business number to the Cloud API unless you're prepared to lose manual WhatsApp access on that phone.

## Privacy & Compliance

- The inspection form should include a **consent line** where the customer agrees to be contacted for feedback.
- Customers can reply **STOP** (or equivalent keywords in Spanish) to opt out permanently.
- Inspection photos are automatically deleted after the configured retention period (default: 90 days).
- Phone numbers and message contents are never logged at INFO level.

## Project Structure

```
├── main.py                     # FastAPI application entry point
├── app/
│   ├── config.py               # Pydantic Settings (loads .env)
│   ├── database.py             # SQLAlchemy engine and session
│   ├── messages.py             # All user-facing text (Spanish)
│   ├── api/
│   │   ├── webhook.py          # WhatsApp webhook endpoints
│   │   ├── surveys.py          # Survey results & CSV export
│   │   └── security.py         # Webhook signature verification
│   ├── services/
│   │   ├── flow.py             # Main message routing orchestrator
│   │   ├── inspections.py      # Duplicate detection & record creation
│   │   ├── scheduler.py        # Business hours & schedule calculation
│   │   ├── survey.py           # Survey sending & customer replies
│   │   └── utils.py            # Phone number normalization
│   ├── integrations/
│   │   ├── whatsapp.py         # WhatsApp Cloud API client
│   │   └── extraction.py       # Ollama Vision Model client
│   ├── models/
│   │   ├── domain.py           # SQLAlchemy models
│   │   └── schemas.py          # Pydantic validation schemas
│   └── worker/
│       └── main.py             # Background polling worker
├── tests/
│   ├── test_api.py             # Endpoint integration tests
│   ├── test_inspections.py     # Duplicate detection tests
│   ├── test_scheduler.py       # Business hours tests
│   ├── test_survey.py          # Survey flow tests (mocked)
│   └── test_utils.py           # Phone normalization tests
├── .env.example                # Environment variable template
├── requirements.txt            # Python dependencies
└── IMPLEMENTATION_PLAN.md      # Development roadmap
```