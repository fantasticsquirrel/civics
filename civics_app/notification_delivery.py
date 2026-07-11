from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from civics_app.db import connect, init_db, utcnow


class SMTPDelivery:
    def __init__(self) -> None:
        self.host = os.environ.get("CIVICS_SMTP_HOST", "")
        try:
            self.port = int(os.environ.get("CIVICS_SMTP_PORT", "587"))
        except ValueError:
            self.port = 0
        self.username = os.environ.get("CIVICS_SMTP_USERNAME", "")
        self.password = os.environ.get("CIVICS_SMTP_PASSWORD", "")
        self.sender = os.environ.get("CIVICS_EMAIL_FROM", "")

    @property
    def ready(self) -> bool:
        return bool(self.host and self.sender and 1 <= self.port <= 65535)

    def send(self, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as client:
            client.ehlo()
            if os.environ.get("CIVICS_SMTP_STARTTLS", "true").lower() == "true":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)


class TelegramDelivery:
    def __init__(self) -> None:
        self.bot_token = os.environ.get("CIVICS_TELEGRAM_BOT_TOKEN", "")

    @property
    def ready(self) -> bool:
        return bool(self.bot_token)

    def send(self, chat_id: str, subject: str, body: str) -> None:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": f"{subject}\n\n{body}"}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage", data=data, method="POST",
            headers={"User-Agent": "CivicsRadar/1.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError("Telegram delivery rejected")


def deliver_notifications(
    limit: int = 50,
    smtp: SMTPDelivery | None = None,
    telegram: TelegramDelivery | None = None,
) -> dict[str, int]:
    init_db()
    smtp = smtp or SMTPDelivery()
    telegram = telegram or TelegramDelivery()
    counts = {"selected": 0, "delivered": 0, "not_configured": 0, "failed": 0}
    now = datetime.now(timezone.utc)
    daily_cutoff = (now - timedelta(days=1)).isoformat(timespec="seconds")
    weekly_cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    with connect() as db:
        rows = db.execute(
            """SELECT n.*,u.notification_email,u.telegram_chat_id FROM notifications n
               JOIN users u ON u.id=n.user_id
               LEFT JOIN notification_preferences p ON p.user_id=n.user_id
               WHERE n.channel IN ('email','telegram')
                 AND n.status IN ('queued','retry','not_configured','digest_pending')
                 AND (n.status != 'digest_pending'
                      OR COALESCE(p.digest_frequency,'instant')='instant'
                      OR (p.digest_frequency='daily' AND n.created_at <= ?)
                      OR (p.digest_frequency='weekly' AND n.created_at <= ?))
               ORDER BY n.id LIMIT ?""", (daily_cutoff, weekly_cutoff, max(1, min(limit, 500)))
        ).fetchall()
        counts["selected"] = len(rows)
        for row in rows:
            destination = row["notification_email"] if row["channel"] == "email" else row["telegram_chat_id"]
            delivery: Any = smtp if row["channel"] == "email" else telegram
            if not delivery.ready or not destination:
                db.execute("UPDATE notifications SET status='not_configured',last_error='' WHERE id=?", (row["id"],))
                counts["not_configured"] += 1
                continue
            try:
                delivery.send(destination, row["title"], row["body"])
                db.execute(
                    "UPDATE notifications SET status='delivered',attempts=attempts+1,last_error='',delivered_at=? WHERE id=?",
                    (utcnow(), row["id"]),
                )
                counts["delivered"] += 1
            except Exception as exc:
                # Provider exception text can contain request URLs or credentials; store only the type.
                db.execute(
                    "UPDATE notifications SET status='retry',attempts=attempts+1,last_error=? WHERE id=?",
                    (f"{type(exc).__name__}: delivery failed", row["id"]),
                )
                counts["failed"] += 1
    return counts
