"""Email alerting for unattended worker failures.

Sends via Gmail SMTP using an App Password (never a regular account
password). Configured entirely through environment variables so no
credentials ever live in code or config.yaml:

    SMTP_USER       - sending Gmail address (e.g. librian577@gmail.com)
    SMTP_PASSWORD   - Gmail App Password (myaccount.google.com/apppasswords)
    ALERT_EMAIL_TO  - recipient address (defaults to SMTP_USER)

If SMTP_USER/SMTP_PASSWORD aren't set, alerts are silently skipped (logged
only) so local/dev runs without email configured don't fail.
"""
from __future__ import annotations

import os
import smtplib
import traceback
from email.mime.text import MIMEText

from .logging_setup import setup_logging

logger = setup_logging()


def send_crash_alert(subject: str, body: str) -> None:
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("ALERT_EMAIL_TO", smtp_user)

    if not smtp_user or not smtp_password:
        logger.warning("SMTP_USER/SMTP_PASSWORD not set — skipping crash email alert.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [to_addr], msg.as_string())
        logger.info("Crash alert emailed to %s", to_addr)
    except Exception:
        logger.exception("Failed to send crash alert email.")


def send_crash_alert_for_exception(context: str, exc: BaseException) -> None:
    subject = f"[AutomatedInvesting] Bot crashed: {context}"
    body = (
        f"The trading worker crashed while: {context}\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"{traceback.format_exc()}"
    )
    send_crash_alert(subject, body)
