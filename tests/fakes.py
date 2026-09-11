"""A fake HTTP session for provider tests: zero network, full call recording."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: object = None
    text_body: str = ""

    @property
    def text(self) -> str:
        if self.text_body:
            return self.text_body
        if self.payload is None:
            return ""
        import json as _json
        try:
            return _json.dumps(self.payload)
        except (TypeError, ValueError):
            return str(self.payload)

    @property
    def content(self) -> bytes:
        if self.payload is None and not self.text_body:
            return b""
        return self.text.encode()

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


@dataclass
class FakeSession:
    responses: list = field(default_factory=list)   # queue; last is sticky
    calls: list = field(default_factory=list)

    def request(self, method, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": headers or {},
                           "json": json})
        if len(self.calls) <= len(self.responses):
            resp = self.responses[len(self.calls) - 1]
        elif self.responses:
            resp = self.responses[-1]
        else:
            resp = FakeResponse(200, {})
        if isinstance(resp, Exception):
            raise resp
        return resp

    # -- assertion helpers --
    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]

    def last(self) -> dict:
        return self.calls[-1]

    def find(self, fragment: str) -> list[dict]:
        return [c for c in self.calls if fragment in c["url"]]
