"""Live notification providers: Telegram (owner alerts) + Resend (customer email).

Both are instant-signup, no approval, free tier:
  Telegram: unlimited bot messages via BotFather token.
  Resend:   100 emails/day free; requires a verified domain for EMAIL_FROM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import Notifier
from .http import api_request


@dataclass
class TelegramNotifier(Notifier):
    token: str
    chat_id: str
    resend_key: str = ""
    email_from: str = ""
    session: object = field(default=None)

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()

    def notify_owner(self, message: str) -> None:
        api_request(self.session, "POST",
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": message},
                    label="telegram.sendMessage")

    def email_customer(self, to: str, subject: str, body: str) -> None:
        if not self.resend_key:
            return  # email optional; Printify/Shopify already send confirmations
        api_request(self.session, "POST", "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.resend_key}",
                             "Content-Type": "application/json"},
                    json={"from": self.email_from, "to": [to],
                          "subject": subject, "text": body},
                    label="resend.email")
