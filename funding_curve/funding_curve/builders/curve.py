# =============================================================
# FILE: funding_curve/builders/curve.py
# =============================================================
from __future__ import annotations

import pandas as pd
from collections import deque, defaultdict
from datetime import timedelta
from typing import Dict, Deque, Optional, List

from funding_curve.funding_collectors import FundingPrint

__all__ = ["FundingCurveBuilder"]


class FundingCurveBuilder:
    """Accumulates FundingPrints into an 8‑bucket forward curve (0‑64 h)."""

    BUCKETS_H: List[int] = list(range(0, 64, 8))  # 0‑8 h … 56‑64 h
    _BUCKET_SECONDS: int = 8 * 60 * 60            # 8 h in seconds

    def __init__(self) -> None:
        # one deque per exchange → ordered by funding_time (oldest→newest)
        self._buffers: Dict[str, Deque[FundingPrint]] = defaultdict(
            lambda: deque(maxlen=len(self.BUCKETS_H))
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update(self, fp: FundingPrint) -> Optional[pd.DataFrame]:
        """Push a *predicted* FundingPrint and maybe return a DataFrame.

        Returns a tidy 8‑row DataFrame **only** when we have the complete
        forward curve for `fp.exchange`. Otherwise returns **None**.
        """
        buf = self._buffers[fp.exchange]

        # Maintain chronological order per exchange.
        if buf and fp.funding_time <= buf[-1].funding_time:
            # Out‑of‑order or duplicate print – drop it.
            return None

        buf.append(fp)

        if len(buf) < len(self.BUCKETS_H):
            return None  # not enough future windows yet

        # ----------------------------------------------------------------
        # Build curve snapshot (latest ts_snap drives the snapshot timestamp)
        # ----------------------------------------------------------------
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
    # Helper for historical ingest (optional)
    # ------------------------------------------------------------------
    def reset(self, exchange: str | None = None) -> None:
        """Clear buffers (useful when replaying history in multiple chunks)."""
        if exchange is None:
            self._buffers.clear()
        else:
            self._buffers.pop(exchange, None)