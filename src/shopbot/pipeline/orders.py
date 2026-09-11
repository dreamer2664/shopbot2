"""Order handling: webhook/poll intake -> validation -> fulfillment -> alerts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..providers.base import (
    Fulfiller, FulfillmentResult, Notifier, Order, PermanentError,
    Storefront, TransientError,
)
from .retry import retry


@dataclass
class OrderBatchResult:
    results: list[FulfillmentResult] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(1 for r in self.results if r.status == "sent")

    @property
    def duplicates(self) -> int:
        return sum(1 for r in self.results if r.status == "duplicate")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


def validate_order(order: Order) -> str | None:
    """Returns an error string if the order is not fulfillable, else None."""
    if not order.order_id:
        return "missing order_id"
    if not order.lines:
        return "no line items"
    if any(ln.quantity <= 0 for ln in order.lines):
        return "non-positive quantity"
    for i, ln in enumerate(order.lines):
        # Empty ids mean the webhook referenced a deleted/custom line item —
        # Printify cannot fulfill it, so reject here with a clear reason.
        if not (ln.product_id or "").strip():
            return f"line {i}: missing product_id"
        if not (ln.variant_id or "").strip():
            return f"line {i}: missing variant_id"
    a = order.ship_to
    for name, value in [("country", a.country), ("zip", a.zip),
                        ("address1", a.address1), ("city", a.city)]:
        if not (value or "").strip():
            return f"ship_to.{name} missing"
    if len(a.country) != 2:
        return "ship_to.country must be ISO-3166 alpha-2"
    return None


def handle_order(
    order: Order,
    *,
    fulfiller: Fulfiller,
    notifier: Notifier | None = None,
    sleep: Callable[[float], None] = lambda _s: None,
    attempts: int = 3,
    expected_currency: str | None = None,
    alerts_seen: set | None = None,
    notify_on_sent: bool = False,
) -> FulfillmentResult:
    """Fulfill a single order with retries, idempotency and owner alerts.

    Idempotency: the fulfiller keys on order.order_id, so replaying the same
    webhook twice (which platforms do routinely) can never double-print.

    expected_currency: prices are computed in one currency (default USD via
    Printify costs). If an order arrives in another currency we still fulfill
    (the customer already paid) but alert the owner — silent FX drift eats
    margins without ever raising an error.

    alerts_seen: dedupe set for *recurring* advisories (currency mismatch).
    Lesson from the year-long training run: alerting per-order produced 20k
    notifications and total alert fatigue. Systemic issues alert once per key;
    per-order failures (which need action) always alert.
    """
    if (expected_currency and notifier
            and order.currency
            and order.currency.upper() != expected_currency.upper()):
        key = f"currency:{order.currency.upper()}:{expected_currency.upper()}"
        if alerts_seen is None or key not in alerts_seen:
            if alerts_seen is not None:
                alerts_seen.add(key)
            notifier.notify_owner(
                f"[pricing] orders are arriving in {order.currency} but pricing "
                f"assumes {expected_currency} (e.g. order {order.order_id}). "
                f"Check store currency settings / margins. "
                f"(You'll only be told once per currency pair.)")
    error = validate_order(order)
    if error:
        if notifier:
            notifier.notify_owner(f"[order {order.order_id or '?'}] rejected: {error}")
        return FulfillmentResult(order.order_id, "failed", error=error)

    try:
        result = retry(lambda: fulfiller.submit(order), attempts=attempts,
                       sleep=sleep)
    except TransientError as exc:
        if notifier:
            notifier.notify_owner(
                f"[order {order.order_id}] fulfillment FAILED after {attempts} "
                f"attempts: {exc}. Manual action needed in Printify dashboard.")
        return FulfillmentResult(order.order_id, "failed", error=str(exc),
                                 attempts=attempts)
    except PermanentError as exc:
        if notifier:
            notifier.notify_owner(
                f"[order {order.order_id}] rejected by supplier: {exc}")
        return FulfillmentResult(order.order_id, "failed", error=str(exc))

    # Notification policy (lesson from the training year: 10k sales = 10k
    # pings = muted chat = missed real emergencies). Sales are summarized by
    # the daily `report`; only problems page the owner in real time.
    if notifier and result.status == "sent" and notify_on_sent:
        notifier.notify_owner(
            f"[order {result.order_id}] sent to production "
            f"({result.provider_order_id}).")
    return result


def process_orders(
    *,
    store: Storefront,
    fulfiller: Fulfiller,
    notifier: Notifier | None = None,
    since=None,
    sleep: Callable[[float], None] = lambda _s: None,
    expected_currency: str | None = None,
    alerts_seen: set | None = None,
    notify_on_sent: bool = False,
) -> OrderBatchResult:
    """Poll the storefront for new orders and fulfill each one.

    In production this is the fallback path; the primary path is a webhook
    (orders/create) hitting the receiver, which calls handle_order directly.
    Pass a long-lived `alerts_seen` set to dedupe advisories across batches.
    """
    from datetime import datetime, timezone
    since = since or datetime(2000, 1, 1, tzinfo=timezone.utc)
    batch = OrderBatchResult()
    for order in store.fetch_orders_since(since):
        batch.results.append(
            handle_order(order, fulfiller=fulfiller, notifier=notifier,
                         sleep=sleep, expected_currency=expected_currency,
                         alerts_seen=alerts_seen))
    return batch
