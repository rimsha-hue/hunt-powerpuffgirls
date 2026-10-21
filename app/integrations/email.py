"""Sending email behind one interface. Gmail SMTP with an app password is the provider; the
same interface will send applications from the user's own mailbox in Phase 4."""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

import anyio
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger()


def _transient(exc: BaseException) -> bool:
    """Connection drops and network errors are worth retrying; a rejected login or address is
    not. (SMTPException subclasses OSError, so it has to be excluded explicitly.)"""
    if isinstance(exc, smtplib.SMTPServerDisconnected | smtplib.SMTPConnectError):
        return True
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


class EmailError(Exception):
    """Safe to show to the user."""


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str  # "application/pdf"
    data: bytes


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()
    # Replies go to the applicant when the sending mailbox isn't theirs.
    reply_to: str | None = None
    # Extra headers built by Power Hunt itself (List-Unsubscribe), never from user text.
    headers: tuple[tuple[str, str], ...] = ()


class EmailProvider(Protocol):
    @property
    def configured(self) -> bool: ...

    async def send(self, message: OutgoingEmail) -> str:
        """Sends the message and returns its Message-ID."""
        ...


class NullEmailProvider:
    configured = False

    async def send(self, message: OutgoingEmail) -> str:
        raise EmailError("Email isn't set up (SMTP_USER and SMTP_APP_PASSWORD are empty).")


class SmtpEmailProvider:
    configured = True

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        timeout: float,
        sender_name: str = "Power Hunt",
        redirect_to: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.sender_name = sender_name
        self.redirect_to = redirect_to

    def _build(self, message: OutgoingEmail) -> EmailMessage:
        email = EmailMessage()
        email["From"] = f"{self.sender_name} <{self.user}>"
        email["To"] = self.redirect_to or message.to
        email["Subject"] = message.subject
        email["Message-ID"] = make_msgid(domain=self.user.rsplit("@", 1)[-1])
        if message.reply_to and message.reply_to.lower() != self.user.lower():
            email["Reply-To"] = message.reply_to
        for name, value in message.headers:
            email[name] = value
        email.set_content(message.text)
        if message.html:
            email.add_alternative(message.html, subtype="html")
        for item in message.attachments:
            maintype, _, subtype = item.content_type.partition("/")
            email.add_attachment(
                item.data,
                maintype=maintype,
                subtype=subtype or "octet-stream",
                filename=item.filename,
            )
        return email

    def _send_sync(self, email: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context) as s:
                s.login(self.user, self.password)
                s.send_message(email)
            return
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
            s.starttls(context=context)
            s.login(self.user, self.password)
            s.send_message(email)

    async def send(self, message: OutgoingEmail) -> str:
        email = self._build(message)
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, max=8),
                retry=retry_if_exception(_transient),
                reraise=True,
            ):
                with attempt:
                    await anyio.to_thread.run_sync(self._send_sync, email)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError("The mail server rejected the SMTP login.") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailError("The mail server refused the recipient address.") from exc
        except OSError as exc:  # SMTPException included
            logger.warning("email_send_failed", error_type=type(exc).__name__)
            raise EmailError("The email couldn't be sent. Try again shortly.") from exc
        message_id = str(email["Message-ID"])
        logger.info("email_sent", message_id=message_id)
        return message_id
