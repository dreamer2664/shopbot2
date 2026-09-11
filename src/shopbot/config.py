"""Configuration loaded from environment / .env. Never hardcode secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no external dependency)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Config:
    dry_run: bool = True  # True = mock providers only, no network calls

    printify_token: str = ""
    printify_shop_id: str = ""

    shopify_domain: str = ""
    shopify_token: str = ""

    telegram_token: str = ""
    telegram_chat_id: str = ""

    postiz_url: str = ""
    postiz_key: str = ""

    resend_key: str = ""
    email_from: str = ""

    # Business parameters
    price_multiplier: float = 2.5   # retail = cost * multiplier (floor)
    min_margin: float = 8.0         # ...but never less than cost + min_margin (USD)
    pricing_currency: str = "USD"   # alerts if a storefront order arrives in another

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        cfg = cls(
            printify_token=os.environ.get("PRINTIFY_API_TOKEN", ""),
            printify_shop_id=os.environ.get("PRINTIFY_SHOP_ID", ""),
            shopify_domain=os.environ.get("SHOPIFY_STORE_DOMAIN", ""),
            shopify_token=os.environ.get("SHOPIFY_ADMIN_TOKEN", ""),
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            postiz_url=os.environ.get("POSTIZ_URL", ""),
            postiz_key=os.environ.get("POSTIZ_API_KEY", ""),
            resend_key=os.environ.get("RESEND_API_KEY", ""),
            email_from=os.environ.get("EMAIL_FROM", ""),
        )
        # Dry run unless every critical credential is present.
        cfg.dry_run = not (cfg.printify_token and cfg.shopify_token)
        return cfg

    @classmethod
    def mock(cls) -> "Config":
        return cls(dry_run=True)
