"""Provider factory: dry-run mocks vs live APIs, chosen by Config.

Live mode requires BOTH printify and shopify credentials; anything less and we
stay in dry run so a half-configured deployment can never touch real money.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from ..config import Config
from .base import Fulfiller, Notifier, ProductProvider, SocialPoster, Storefront
from .mock import MockProviders


@dataclass
class Providers:
    supplier: ProductProvider
    store: Storefront
    fulfiller: Fulfiller
    notifier: Notifier | None
    social: SocialPoster | None
    sleep: Callable[[float], None]


def build_providers(cfg: Config | None = None) -> Providers:
    cfg = cfg or Config.from_env()
    if cfg.dry_run:
        m = MockProviders.build()
        return Providers(supplier=m.supplier, store=m.store, fulfiller=m.fulfiller,
                         notifier=m.notifier, social=m.social, sleep=m.sleep)

    import time
    from .printify import PrintifyProvider
    from .shopify import ShopifyStorefront
    from .notifications import TelegramNotifier
    from .postiz import PostizSocial

    supplier = PrintifyProvider(token=cfg.printify_token, shop_id=cfg.printify_shop_id)
    store = ShopifyStorefront(domain=cfg.shopify_domain, token=cfg.shopify_token)

    notifier = None
    if cfg.telegram_token and cfg.telegram_chat_id:
        notifier = TelegramNotifier(token=cfg.telegram_token,
                                    chat_id=cfg.telegram_chat_id,
                                    resend_key=cfg.resend_key,
                                    email_from=cfg.email_from)

    social = None
    if cfg.postiz_url and cfg.postiz_key:
        ids = [i for i in os.environ.get("POSTIZ_INTEGRATION_IDS", "").split(",") if i]
        social = PostizSocial(base_url=cfg.postiz_url, api_key=cfg.postiz_key,
                              integration_ids=ids)

    return Providers(supplier=supplier, store=store, fulfiller=supplier,
                     notifier=notifier, social=social, sleep=time.sleep)
