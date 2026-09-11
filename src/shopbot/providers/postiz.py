"""Live social posting via a self-hosted Postiz instance.

Why Postiz and not raw platform APIs: Meta (IG/FB) needs App Review and TikTok
needs an audit before public posting — weeks of friction. Postiz is open-source,
holds those approvals once you connect your accounts through its UI, and exposes
a simple REST API we can drive. See docs/RESEARCH.md section 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .base import PermanentError, SocialPoster
from .http import api_request


@dataclass
class PostizSocial(SocialPoster):
    base_url: str                 # e.g. https://post.example.com
    api_key: str
    integration_ids: list[str] = field(default_factory=list)
    session: object = field(default=None)

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()

    def post(self, text: str, image_urls: list[str], networks: list[str]) -> str:
        if not self.integration_ids:
            raise PermanentError(
                "postiz: no integration ids configured — connect accounts in "
                "the Postiz UI first, then set POSTIZ_INTEGRATION_IDS")
        value = [{"content": text}]
        if image_urls:
            value[0]["image"] = image_urls
        payload = {
            "type": "schedule",
            # small offset so the scheduler picks it up immediately
            "date": (datetime.now(timezone.utc) + timedelta(minutes=1))
                    .strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "posts": [{"integration": {"id": iid}, "value": value}
                      for iid in self.integration_ids],
        }
        data = api_request(
            self.session, "POST",
            f"{self.base_url.rstrip('/')}/public/v1/posts",
            headers={"Authorization": self.api_key,
                     "Content-Type": "application/json"},
            json=payload, label="postiz.create_post")
        # Postiz returns a list of created post records
        if isinstance(data, list) and data:
            return str(data[0].get("id", "postiz_ok"))
        return "postiz_ok"
