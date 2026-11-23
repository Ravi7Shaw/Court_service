from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import Settings
from app.events import mark_outbox_dirty
from app.models import Notification, NotificationOutbox, User

log = logging.getLogger(__name__)


def notify_and_queue_email(
    session: Session, user: User, kind: str, title: str, message: str, subject: str, body: str,
    dedupe_key: str | None = None,
) -> None:
    if dedupe_key:
        inserted = session.execute(
            pg_insert(NotificationOutbox)
            .values(user_id=user.id, recipient=user.email, subject=subject, body=body, dedupe_key=dedupe_key)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(NotificationOutbox.id)
        ).first()
        if inserted is None:
            return
    else:
        session.add(NotificationOutbox(user_id=user.id, recipient=user.email, subject=subject, body=body))
    session.add(Notification(user_id=user.id, kind=kind, title=title, message=message))
    # The worker is woken only after this transaction commits.
    mark_outbox_dirty(session)


def deliver_email(settings: Settings, recipient: str, subject: str, body: str) -> None:
    if settings.email_backend == "console":
        log.info("SIMULATED_EMAIL recipient=%s subject=%s", recipient, subject)
        return
    if settings.email_backend != "smtp" or not settings.smtp_host:
        raise RuntimeError("SMTP delivery requires a configured SMTP host")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


def notify_admins(session: Session, kind: str, title: str, message: str, event_key: str) -> None:
    from sqlalchemy import select
    admins = session.scalars(select(User).where(User.role == "admin", User.is_active.is_(True),
                                               User.is_verified.is_(True))).all()
    for admin in admins:
        notify_and_queue_email(session, admin, kind, title, message, title, message,
                               dedupe_key=f"{event_key}:{admin.id}")
