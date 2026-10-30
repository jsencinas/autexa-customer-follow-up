# Autexa customer follow-up

Backend service that sends a short WhatsApp satisfaction survey to each customer after a service,
from the company's own WhatsApp number, and shows the results to the business.

## How it works

1. An employee opens the web app on the company phone, logs in and photographs the printed
   "initial inspection" form.
2. A local vision model (Ollama) reads the form. The employee reviews the data next to the photo,
   corrects anything wrong (or types it by hand if the photo is unreadable) and presses **Confirmar**.
3. After a delay (default 24 h), and only inside the sending hours set by the admin, the worker sends
   a personalised survey template (customer's first name + service) from the company number.
   Templates rotate so a repeat customer does not get the same wording twice.
4. Replies are stored by the webhook and interpreted by the worker: obvious answers by rules, the rest
   by a local LLM (Ollama). Unsure ratings go to an admin review queue; opt-outs in any wording put
   the number on a permanent opt-out list.
5. The admin section shows live charts, a review queue, the opt-out list and settings, and a
   monthly report is emailed to the configured recipients.

**The company number stays a human channel.** Employees keep using the WhatsApp Business app for
quotes, prices and chat (Meta's *Coexistence* mode). The bot only sends survey messages and only
processes replies to them. It never answers anything else, and it stops interpreting a conversation
as soon as an employee joins it (except for opt-out detection).

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/) (`uv` installs Python 3.12 for you)
- [Ollama](https://ollama.com) with a vision model and a text model
- A Meta Business account with the company number on the WhatsApp Business app
- An SMTP account for the monthly report
- A public HTTPS URL for the webhook (e.g. behind nginx/Caddy, or a tunnel during development)

## Setup

```bash
uv sync                      # creates .venv with Python 3.12 and all dependencies
cp .env.example .env         # then fill in the values (see below)
uv run python -m app.cli create-user --username jefe --role admin   # first admin (asks for a password)
uv run python -m app.cli set-password --username jefe               # reset a forgotten password
```

Employees' accounts are created afterwards by the admin at **Usuarios** (`/admin/users`), or with
the same CLI command and `--role employee`.

### Environment variables

Every variable is listed with its default in `.env.example`. The ones you must set:

| Variable | What it is |
|---|---|
| `APP_SECRET_KEY` | Long random string used to sign session cookies |
| `WHATSAPP_ACCESS_TOKEN` | Permanent token of a system user with `whatsapp_business_messaging` permission |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone number ID of the company number (WhatsApp Manager → API Setup) |
| `WHATSAPP_APP_SECRET` | Meta app secret, used to verify the `X-Hub-Signature-256` of every webhook call |
| `WHATSAPP_VERIFY_TOKEN` | Any random string; you type the same value in the webhook configuration |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Email account for the monthly report |
| `BUSINESS_TIMEZONE`, `DEFAULT_COUNTRY_CODE` | Default `America/Mexico_City`, `MX` (country used for numbers written without a prefix) |

Sending hours (`DEFAULT_SENDING_*`) and the report day/time (`DEFAULT_REPORT_*`) are only initial
defaults: once the admin saves them in **Ajustes**, the database values are used.
`SESSION_COOKIE_SECURE=false` is only for local development over plain HTTP.

## WhatsApp setup

### 1. Create the Meta app

1. In [Meta for Developers](https://developers.facebook.com/apps) create an app of type **Business**
   and add the **WhatsApp** product, linked to the company's Meta Business portfolio.
2. In Business Settings create a **system user**, give it access to the app and the WhatsApp
   Business Account, and generate a permanent token with `whatsapp_business_messaging` and
   `whatsapp_business_management` → `WHATSAPP_ACCESS_TOKEN`.
3. App settings → Basic → **App secret** → `WHATSAPP_APP_SECRET`.

### 2. Connect the existing number with Coexistence

The company number must keep working in the WhatsApp Business app, so it is onboarded to the
Cloud API through **Coexistence** (Embedded Signup for WhatsApp Business app users), not by
migrating it. Follow Meta's current guide ("Onboarding WhatsApp Business app users"), and check
its prerequisites before you start. In particular:

- the number must be active on an up-to-date WhatsApp Business app;
- **the company phone must keep the Business app in use** (Meta requires the app to be opened
  regularly for Coexistence to stay active);
- Coexistence has a lower throughput than a regular Cloud API number. Keep
  `SEND_RATE_PER_SECOND` low (default 1 message/second).

After onboarding, copy the number's **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`.

### 3. Configure the webhook

App dashboard → WhatsApp → Configuration → Webhook:

- **Callback URL:** `https://<your-domain>/webhook`
- **Verify token:** the value of `WHATSAPP_VERIFY_TOKEN`
- Subscribe to the fields **`messages`** (customer messages and delivery statuses) and
  **`smb_message_echoes`** (messages employees send from the Business app; the bot stores only
  their timestamp, to know that a person has joined the conversation).

The server must be running when you press *Verify and save*. Every webhook request is checked
against `WHATSAPP_APP_SECRET`; unsigned or wrongly signed requests get `403`.

### 4. Register the survey templates

The first message to a customer is business-initiated, so it must be a pre-approved template.
Register **each** template below in WhatsApp Manager → Message templates with exactly this name,
language **Spanish (es)**, body text and buttons (in this order). The templates are listed in
`app/messages.yaml`; the bot only uses the ones listed there, so you can remove a variant from that
file until Meta approves it.

| Name | Body |
|---|---|
| `encuesta_satisfaccion_v1` | Hola {{1}}, gracias por confiar en nosotros para {{2}}. ¿Qué tal fue tu experiencia? |
| `encuesta_satisfaccion_v2` | ¡Hola {{1}}! Hace poco nos encargamos de {{2}}. ¿Nos cuentas qué tal fue? |
| `encuesta_satisfaccion_v3` | {{1}}, queremos mejorar cada día. ¿Cómo valorarías nuestro trabajo en {{2}}? |
| `encuesta_satisfaccion_v4` | Hola {{1}} 👋 Esperamos que todo vaya bien con {{2}}. ¿Cómo valorarías nuestro servicio? |

- **Variables:** `{{1}}` = customer's first name, `{{2}}` = service as shown to the customer
  (sample values for the review, e.g. `María`, `el cambio de aceite`). When the form has no service
  written, the bot pre-fills the vehicle instead (`tu Chevrolet Aveo`); the employee can edit it.
- **Buttons:** four *Quick reply* buttons, in this order: `Bien`, `Regular`, `Mal`,
  `No enviar más mensajes`. The bot attaches a payload to each button
  (`survey:<id>:good|okay|bad|opt_out`), so a tap is matched to the right survey without
  interpretation.
- **Category:** Meta may classify satisfaction surveys as **Marketing** rather than Utility.
  That affects the price per conversation, and for marketing templates Meta may add its own
  "stop promotions" option. The opt-out button above works either way.

The follow-up question, thank-you and opt-out confirmation are sent inside the 24-hour customer
service window, so they are free-form text and need no approval. All customer-facing text,
including the phrases recognised without the LLM, lives in `app/messages.yaml`. Edit or translate
it there, without touching the code.

## Ollama

```bash
ollama serve                    # if it is not already running as a service
ollama pull qwen2.5vl:7b        # EXTRACTION_MODEL: reads the handwritten forms
ollama pull qwen2.5:7b          # REPLY_MODEL: interprets free-text replies
```

If Ollama was installed as a system service, start it with `sudo systemctl enable --now ollama`
instead of `ollama serve`: the service keeps its models in its own folder, so a manual `ollama serve`
started by your user will not see them (the extraction then fails with "model not found").

Any Ollama model can be configured through `EXTRACTION_MODEL` / `REPLY_MODEL`. A GPU helps but is
not required: both run in the background worker, never inside a web request. If Ollama is down,
photos and replies wait and are retried. After repeated failures, photos fall back to manual entry
and replies go to the admin review queue.

To measure how well the reply model works on Spanish replies (ratings with extra text, unrelated
messages, opt-outs in many wordings), run:

```bash
uv run python scripts/eval_replies.py   # uses tests/fixtures/replies_es.jsonl and the real model
```

Missed opt-outs are the costly error. The script exits non-zero if any labelled opt-out is missed.

## Running

Two processes, both reading the same `.env`:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000   # web app + webhook (put HTTPS in front)
uv run python -m app.worker                             # background worker
```

**Both must be running.** The web app only stores the photo; the worker is what reads it. If the
worker is stopped, the review page says so instead of loading forever.

The worker polls the database. Extraction and reply interpretation run every few seconds; due
surveys, expirations and the monthly report are checked every minute; photo retention runs every
hour. All state is in the database, so either process can be restarted at any time:

- a survey is claimed before it is sent and is **never sent twice**. If the outcome of a send is
  unknown (crash, timeout), it is marked `failed` instead of being retried;
- transient errors (WhatsApp, Ollama, SMTP) retry with exponential backoff and are marked `failed`
  after the retry limit;
- an error in one record is logged and never stops the worker.

### Web app

| Page | Who | Purpose |
|---|---|---|
| `/inspections/new` | employees | Photograph/upload a form |
| `/inspections/{id}/confirm` | employees | Review, correct, confirm or discard; retake photo; manual entry. After confirming, data can still be corrected (or the survey cancelled) until the survey is sent |
| `/inspections` | employees | Status window: every inspection by status, with search; updates itself |
| `/admin` | admin | Live dashboard (refreshes every 30 s), CSV download |
| `/admin/review` | admin | Replies the bot was unsure about |
| `/admin/opt-outs` | admin | Opt-out list (removal requires a reason) |
| `/admin/settings` | admin | Sending hours, report recipients/schedule, report history, audit log |
| `/admin/users` | admin | Accounts |
| `GET /surveys`, `GET /surveys.csv` | admin | Results as JSON / CSV, filters `date_from`, `date_to` (YYYY-MM-DD), `rating`, `service` |

Admin sessions expire after 30 minutes of inactivity, and changing settings asks for the password again.

## Survey statuses

`pending_confirmation` → `scheduled` → `sent` → `answered`, or `expired` (not confirmed in time,
default 24 h), `failed`, `discarded` (by the employee, or a duplicate of an existing inspection),
`opted_out` (the customer is on the opt-out list).

## Privacy

- Phone numbers and message contents are never logged at INFO level (numbers are masked).
- Photos are deleted after `IMAGE_RETENTION_DAYS` (default 90). The inspection record is kept.
- The opt-out list is permanent and never touched by the retention cleanup. Opting out only stops
  bot messages: employees can still write to the customer by hand.
- The monthly email contains no phone numbers unless the admin enables the CSV attachment.

## Tests

```bash
uv run pytest
```

WhatsApp, Ollama and SMTP are replaced by fakes in the tests (see `tests/fakes.py`).
