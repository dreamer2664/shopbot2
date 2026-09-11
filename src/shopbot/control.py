"""Telegram control bot — the owner's interface to the running program.

Long-polls getUpdates (no public URL needed for THIS direction), executes
commands against the live pipeline, answers in chat. Security: only the
configured chat_id may issue commands; the bot token itself is the auth
for sendMessage.

Commands:
  /status              mode, uptime, counters from the ledger
  /report [YYYY-MM-DD] revenue / orders / alerts from analytics
  /orders [N]          last N fulfillment events
  /launch              re-run designs.csv (env DESIGNS_CSV or ./designs.csv)
  /pause  /resume      pause/resume automatic order processing (webhook keeps
                       recording; fulfillment holds until resumed)
  /help
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Config
from .brain.decisions import decide, format_advice
from .pipeline.analytics import build_report
from .pipeline.catalog import Catalog
from .pipeline.ledger import Ledger
from .pipeline.orders import process_orders
from .pipeline.products import launch_product
from .providers.base import Design, Notifier, TransientError
from .providers.factory import Providers, build_providers
from .pipeline.state import Checkpoint

log = logging.getLogger("shopbot.control")

PAUSE_FLAG = "data/paused.flag"


@dataclass
class ControlBot:
    cfg: Config
    providers: Providers
    ledger: Ledger = field(default_factory=lambda: Ledger("data/ledger.jsonl"))
    catalog: Catalog = field(default_factory=lambda: Catalog("data/catalog.json"))
    session: object = field(default=None)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
        if not (self.cfg.telegram_token and self.cfg.telegram_chat_id):
            raise ValueError("control bot requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")

    # ---- telegram transport ----

    @property
    def _api(self) -> str:
        base = os.environ.get("TELEGRAM_BASE_URL") or "https://api.telegram.org"
        return f"{base.rstrip('/')}/bot{self.cfg.telegram_token}"

    def _say(self, text: str) -> None:
        try:
            r = self.session.request(
                "POST", f"{self._api}/sendMessage",
                json={"chat_id": self.cfg.telegram_chat_id, "text": text[:4000]},
                timeout=15)
            if r.status_code >= 400:
                log.error("sendMessage failed: %s %s", r.status_code, r.text[:200])
        except Exception as exc:
            log.error("sendMessage error: %s", exc)

    def fetch_updates(self, offset: int) -> list[dict]:
        url = f"{self._api}/getUpdates?offset={offset}&timeout=5"
        try:
            r = self.session.request("GET", url, timeout=35)
            if r.status_code >= 400:
                log.warning("getUpdates %s", r.status_code)
                return []
            data = r.json()
            return data.get("result", []) if data.get("ok") else []
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            return []

    # ---- command handling ----

    def handle_text(self, text: str) -> str:
        """Pure function: command text -> reply text. Fully unit-testable."""
        text = (text or "").strip()
        parts = text.split()
        cmd = parts[0].lower() if parts else ""

        if cmd == "/help" or not cmd.startswith("/"):
            return ("shopbot commands:\n/status /report [day] /orders [n]\n"
                    "/advice /launch /pause /resume /help")

        if cmd == "/status":
            rep = build_report(self.ledger)
            paused = "PAUSED" if os.path.exists(PAUSE_FLAG) else "running"
            mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
            up = datetime.now(timezone.utc) - self.started_at
            return (f"shopbot {mode}, {paused}, up {int(up.total_seconds() // 60)}m\n"
                    f"launches ok/fail: {rep.launches_ok}/{rep.launches_failed}\n"
                    f"orders sent/dup/failed: {rep.orders_sent}/"
                    f"{rep.orders_duplicate}/{rep.orders_failed}\n"
                    f"revenue: {rep.revenue} | profit: {rep.profit} | "
                    f"margin: {f'{rep.margin:.0%}' if rep.margin is not None else 'n/a'}")

        if cmd == "/report":
            day = parts[1] if len(parts) > 1 else None
            rep = build_report(self.ledger)
            s = rep.per_day.get(day, None) if day else None
            lines = [f"report {day or '(all days)'}",
                     f"orders sent: {rep.orders_sent}  failed: {rep.orders_failed}  "
                     f"duplicates absorbed: {rep.orders_duplicate}",
                     f"revenue: {rep.revenue}  cost: {rep.cost}  profit: {rep.profit}"]
            if rep.alerts:
                lines.append("alerts:")
                lines.extend(f" ! {a}" for a in rep.alerts[:5])
            return "\n".join(lines)

        if cmd == "/orders":
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            entries = [e for e in self.ledger.entries() if e.kind == "fulfillment"]
            tail = entries[-n:]
            if not tail:
                return "no order events yet"
            return "\n".join(
                f"{e.ts[5:16].replace('T', ' ')} {e.order_id} {e.status}"
                + (f" ({e.error})" if e.error else "") for e in reversed(tail))

        if cmd == "/advice":
            rep = build_report(self.ledger)
            return format_advice(decide(rep))

        if cmd == "/launch":
            return self._do_launch()

        if cmd == "/pause":
            os.makedirs(os.path.dirname(PAUSE_FLAG) or ".", exist_ok=True)
            with open(PAUSE_FLAG, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
            return ("paused: webhook still records orders to the ledger but "
                    "fulfillment holds. /resume to continue.")

        if cmd == "/resume":
            if os.path.exists(PAUSE_FLAG):
                os.remove(PAUSE_FLAG)
                # catch up on everything collected while paused
                return self._do_poll(catchup=True)
            return "not paused"

        return f"unknown command {cmd}. /help"

    def _do_launch(self) -> str:
        csv_path = os.environ.get("DESIGNS_CSV", "designs.csv")
        if not os.path.exists(csv_path):
            return f"{csv_path} not found — set DESIGNS_CSV or put designs.csv in cwd"
        ok = fail = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                design = Design(slug=row["slug"].strip(),
                                title=row["title"].strip(),
                                description=row.get("description", "").strip(),
                                image_path=row["image_url"].strip(),
                                tags=(row.get("tags") or "").split())
                res = launch_product(
                    design, row["blueprint"].strip(),
                    supplier=self.providers.supplier, store=self.providers.store,
                    social=self.providers.social, cfg=self.cfg,
                    sleep=self.providers.sleep, catalog=self.catalog)
                self.ledger.record_launch(design.slug, res.success,
                                          error=res.error)
                ok, fail = ok + res.success, fail + (not res.success)
        return f"launch done: {ok} ok, {fail} failed (/orders and /status for detail)"

    def _do_poll(self, catchup: bool = False) -> str:
        cp = Checkpoint()
        since = cp.load_last_run() if not catchup else None
        batch = process_orders(store=self.providers.store,
                               fulfiller=self.providers.fulfiller,
                               notifier=self.providers.notifier,
                               since=since, sleep=self.providers.sleep,
                               expected_currency=self.cfg.pricing_currency,
                               catalog=self.catalog)
        for r in batch.results:
            self.ledger.record_fulfillment(r, cost=r.cost)
        cp.save_last_run()
        return (f"resumed & caught up: sent={batch.sent} dup={batch.duplicates} "
                f"failed={batch.failed}")

    # ---- main loop ----

    def run_once(self) -> int:
        """One poll cycle; returns number of updates processed."""
        handled = 0
        for upd in self.fetch_updates(self._offset):
            self._offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if str(chat) != str(self.cfg.telegram_chat_id):
                continue    # ignore anyone who isn't the owner
            text = msg.get("text") or ""
            handled += 1
            try:
                reply = self.handle_text(text)
            except Exception as exc:      # a command must never kill the bot
                log.exception("command failed")
                reply = f"command failed: {exc}"
            self._say(reply)
        return handled

    _offset: int = 0

    def run_forever(self, stop: threading.Event | None = None,
                    poll_seconds: float = 3.0) -> None:
        stop = stop or threading.Event()
        self._say("shopbot control online. /help for commands, /status for state.")
        while not stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("control loop error")
            stop.wait(poll_seconds)


def paused() -> bool:
    return os.path.exists(PAUSE_FLAG)
