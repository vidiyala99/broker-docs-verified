"""Per-run token budget and usage ledger (D3, M4).

Accounting rule (M4): every model call (extract, vision, compose, chat,
suggest) reserves its estimated input plus its full reserved output before it
is sent. If the reservation does not fit in what remains, the call raises
BudgetExhausted and nothing is sent. After the call, the reservation is
settled with the provider-reported prompt + completion tokens, so unused
reserved output returns to the pool.

The budget counts input and output together against one limit (600k by
default, D3). Exhaustion is not sticky: a later, smaller call that fits still
runs. `exhausted` reports whether any reservation was ever denied, so the
runner can mark the run partial.

Thread-safe: parse and verify run in a thread pool (M6), model calls run on
the event loop; a plain lock covers both.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable

from .config import RUN_TOKEN_BUDGET
from .errors import BudgetExhausted
from .types import CallRecord, RoleTotals, UsageTotals

log = logging.getLogger("brokerdocs.providers")


def estimate_tokens(text: str, chars_per_token: float = 3.0) -> int:
    """Conservative token estimate for a pre-call check. No tokenizer dependency."""
    if not text:
        return 0
    return math.ceil(len(text) / chars_per_token)


@dataclass
class Reservation:
    id: int
    tokens: int
    label: str | None
    open: bool = True


class RunBudget:
    def __init__(self, limit: int = RUN_TOKEN_BUDGET, on_record: Callable[[CallRecord], None] | None = None):
        if limit <= 0:
            raise ValueError("budget limit must be positive")
        self.limit = limit
        self._on_record = on_record
        self._lock = threading.Lock()
        self._used = 0
        self._reserved = 0
        self._next_id = 0
        self._records: list[CallRecord] = []
        self.exhausted = False

    # ------------------------------------------------------------ reservations

    @property
    def used(self) -> int:
        return self._used

    @property
    def reserved(self) -> int:
        return self._reserved

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._used - self._reserved)

    def can_afford(self, tokens: int) -> bool:
        return tokens <= self.remaining

    def reserve(self, tokens: int, *, label: str | None = None) -> Reservation:
        if tokens < 0:
            raise ValueError("reservation must be non-negative")
        with self._lock:
            remaining = max(0, self.limit - self._used - self._reserved)
            if tokens > remaining:
                self.exhausted = True
                raise BudgetExhausted(requested=tokens, remaining=remaining, label=label)
            self._next_id += 1
            self._reserved += tokens
            return Reservation(self._next_id, tokens, label)

    def settle(self, reservation: Reservation, actual_tokens: int) -> None:
        """Close a reservation, charging the actual tokens (may exceed the reservation)."""
        with self._lock:
            self._close(reservation)
            self._used += max(0, actual_tokens)

    def release(self, reservation: Reservation) -> None:
        """Close a reservation without charging (the request was never processed)."""
        with self._lock:
            self._close(reservation)

    def _close(self, reservation: Reservation) -> None:
        if not reservation.open:
            raise ValueError("reservation already closed")
        reservation.open = False
        self._reserved -= reservation.tokens

    # ------------------------------------------------------------ ledger

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            self._records.append(rec)
        if self._on_record is not None:
            try:
                self._on_record(rec)
            except Exception:  # a failed persistence hook must not lose the call's accounting
                log.exception("budget on_record hook failed")

    @property
    def records(self) -> list[CallRecord]:
        with self._lock:
            return list(self._records)

    def totals(self) -> UsageTotals:
        totals = UsageTotals()
        for r in self.records:
            for bucket in (totals, totals.by_role.setdefault(r.role, RoleTotals())):
                bucket.calls += 1
                bucket.prompt_tokens += r.prompt_tokens
                bucket.completion_tokens += r.completion_tokens
                bucket.cached_tokens += r.cached_tokens
                bucket.reasoning_tokens += r.reasoning_tokens
                bucket.cost_usd += r.cost_usd
        return totals
