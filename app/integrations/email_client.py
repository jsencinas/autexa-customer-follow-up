"""SMTP client for the monthly report."""

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailError(Exception):
    """Sending failed; `retryable` tells whether trying later may help."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class OutgoingEmail:
    """An HTML email with inline images (referenced as `cid:<name>`) and optional attachments."""

    to: list[str]
    subject: str
    html: str
    text: str
    inline_images: dict[str, bytes] = field(default_factory=dict)
    attachments: dict[str, tuple[bytes, str]] = field(default_factory=dict)  # filename -> (data, mime type)


class EmailClient:
    """Sends email through the configured SMTP server."""

    def __init__(
        self, host: str, port: int, username: str, password: str, sender: str, starttls: bool = True, timeout: float = 30
    ) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.starttls, self.timeout = sender, starttls, timeout

    def build(self, email: OutgoingEmail) -> EmailMessage:
        """Assemble the MIME message."""
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(email.to)
        msg["Subject"] = email.subject
        msg.set_content(email.text)
        msg.add_alternative(email.html, subtype="html")
        html_part = msg.get_payload()[1]  # type: ignore[index]
        for cid, data in email.inline_images.items():
            html_part.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>")
        for filename, (data, mime) in email.attachments.items():
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        return msg

    def send(self, email: OutgoingEmail) -> None:
        """Send the email or raise EmailError."""
        if not self.host or not self.sender:
            raise EmailError("SMTP is not configured (SMTP_HOST / SMTP_FROM)", retryable=False)
        message = self.build(email)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(message)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise EmailError(f"SMTP rejected the message: {type(exc).__name__}", retryable=False) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"SMTP error: {type(exc).__name__}", retryable=True) from exc
        logger.info("email '%s' sent to %s recipients", email.subject, len(email.to))
