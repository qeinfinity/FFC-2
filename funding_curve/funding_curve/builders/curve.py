# =============================================================
# FILE: funding_curve/builders/curve.py  (patched v1.1, 2025‑05‑05)
# =============================================================
"""Funding‑curve builder — accumulates *predicted* 8‑hour funding prints
into a rolling 8‑bucket (0‑64 h) forward curve.  The patched version adds:

1. **emit_on_roll** throttling (default *True*) → emits a snapshot only on
   the *first* print after the funding window rolls, drastically reducing
   I/O pressure without losing curve‑shape information.
2. **Gap guard**: drops prints with missing/NaN `predicted_rate` to prevent
   NaNs propagating into downstream PCA.
3. Exponential reconnect helper removed from here — now handled in
   collectors — so no dependency changes.

Usage (unchanged API):

```python
builder = FundingCurveBuilder(emit_on_roll=True)
...
snap = builder.update(fp)
```
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional

import pandas as pd

from funding_curve.funding_collectors import FundingPrint

__all__ = ["FundingCurveBuilder"]


class FundingCurveBuilder:
    """Accumulates FundingPrints into an 8‑bucket forward curve (0‑64 h)."""

    BUCKETS_H: List[int] = list(range(0, 64, 8))  # [0, 8, …, 56]
    _BUCKET_SECONDS: int = 8 * 60 * 60           # 8 h in seconds

    # ------------------------------------------------------------------
    # ctor
    # ------------------------------------------------------------------
    def __init__(self, *, emit_on_roll: bool = True) -> None:
        """Parameters
        ----------
        emit_on_roll
            If *True* (default) the builder emits **one** snapshot per
            funding roll (≈ every 8 h) per exchange.  Set *False* to emit a
            snapshot every time *bucket 0* updates (approx 1 Hz on Binance).
        """
        self.emit_on_roll = emit_on_roll
        # per‑exchange deque of length 8, ordered by funding_time
        self._buffers: Dict[str, Deque[FundingPrint]] = defaultdict(
            lambda: deque(maxlen=len(self.BUCKETS_H))
        )
        # Tracks last funding_time we emitted for each exchange
        self._last_roll: Dict[str, pd.Timestamp] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update(self, fp: FundingPrint) -> Optional[pd.DataFrame]:
        """Push a FundingPrint; return a tidy 8‑row DataFrame **or** None.

        A DataFrame is returned only when the forward curve is complete and
        — if *emit_on_roll* — the funding window has just rolled.
        """
        # ------------------------------------------------------------------
        # Basic sanity / gap guard
        # ------------------------------------------------------------------
        if fp.predicted_rate is None or pd.isna(fp.predicted_rate):
            # Skip malformed prints early.
            return None

        buf = self._buffers[fp.exchange]

        # Maintain strict chronological order per exchange.
        if buf and fp.funding_time <= buf[-1].funding_time:
            # Duplicate or out‑of‑order → ignore.
            return None

        buf.append(fp)

        # Need full 8 buckets before emitting anything.
        if len(buf) < len(self.BUCKETS_H):
            return None

        # ------------------------------------------------------------------
        # Throttle – emit only on first print after the funding roll.
        # ------------------------------------------------------------------
        if self.emit_on_roll:
            last_roll = self._last_roll.get(fp.exchange)
            if last_roll is not None and fp.funding_time == last_roll:
                return None  # same window as last emission
            self._last_roll[fp.exchange] = fp.funding_time

        # ------------------------------------------------------------------
        # Build snapshot (latest ts_snap sets snapshot timestamp)
        # ------------------------------------------------------------------
        rows = []
        for idx, item in enumerate(buf):
            annualised = (1 + item.predicted_rate) ** (24 * 365 / 8) - 1
            rows.append(
                {
                    "exchange": item.exchange,
                    "ts_snap": item.ts_snap,
                    "bucket_start_h": idx * 8,
                    "bucket_end_h": (idx + 1) * 8,
                    "fwd_rate_ann": annualised,
                    "raw_rate": item.predicted_rate,
                    "funding_time": item.funding_time,
                }
            )

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Helper for historical ingest / testing
    # ------------------------------------------------------------------
    def reset(self, exchange: str | None = None) -> None:
        """Clear internal buffers (useful for unit tests / history replay)."""
        if exchange is None:
            self._buffers.clear()
            self._last_roll.clear()
        else:
            self._buffers.pop(exchange, None)
            self._last_roll.pop(exchange, None)
