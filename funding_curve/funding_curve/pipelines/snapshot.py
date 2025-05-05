# =============================================================
# FILE: funding_curve/pipelines/snapshot.py  (patched v1.1, 2025‑05‑05)
# =============================================================
"""Live funding‑curve snapshot loop

• Seeds builder with the last 64 h realised prints **and** persists that
  initial curve into *curve_live.parquet* so historical exploration works
  even if you never ran the big `ingest.py` back‑fill.
• After seeding, emits one snapshot per 8‑h funding roll per exchange.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
from loguru import logger

from funding_curve.builders.curve import FundingCurveBuilder
from funding_curve.funding_collectors import BinanceCollector, BybitCollector

SNAP_PATH = Path("storage/processed/curve_live.parquet")
ENGINE    = "fastparquet"  # keep in sync with ingest.py

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
async def _seed_builder(builder: FundingCurveBuilder, collector, now_utc: datetime) -> Optional[pd.DataFrame]:
    """Pre‑warm builder with last seven realised prints *and* return the first
    complete curve snapshot so it can be written to disk."""
    start = now_utc - timedelta(hours=64 + 1)
    end   = now_utc - timedelta(seconds=1)

    async with collector:
        prints = await collector.backfill_realised(start, end)

    # Keep the **last** 7 events (one per 8‑h bucket)
    prints = sorted(prints, key=lambda p: p.funding_time)[-7:]

    snapshot: Optional[pd.DataFrame] = None
    for fp in prints:
        snapshot = builder.update(fp)  # snapshot becomes non‑None on last insert
    return snapshot


async def _pipe(stream, builder):
    async for fp in stream:
        snap = builder.update(fp)
        if snap is not None:
            _append_parquet(snap)
            logger.debug("live snapshot appended: %s / %s", fp.exchange, fp.funding_time)


def _append_parquet(df: pd.DataFrame, *, first_write: bool = False):
    df.to_parquet(
        SNAP_PATH,
        engine=ENGINE,
        compression="snappy",
        index=False,
        append=(not first_write and SNAP_PATH.exists()),
    )


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
async def main():
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)

    builder = FundingCurveBuilder()  # emit_on_roll=True by default
    binance = BinanceCollector()
    bybit   = BybitCollector()

    # 1️⃣  Pre‑warm builder and capture initial snapshots ----------------------
    seed_snaps = await asyncio.gather(
        _seed_builder(builder, binance, now_utc),
        _seed_builder(builder, bybit,  now_utc),
    )

    # Concatenate any non‑empty seed frames and write once --------------------
    seed_frames: List[pd.DataFrame] = [s for s in seed_snaps if s is not None]
    if seed_frames:
        df_init = pd.concat(seed_frames, ignore_index=True)
        _append_parquet(df_init, first_write=not SNAP_PATH.exists())
        logger.success("Initial seeded curve written → %s  (%d rows)", SNAP_PATH, len(df_init))
    else:
        logger.warning("No initial snapshot generated during seeding phase.")

    # 2️⃣  Start live streams --------------------------------------------------
    async with binance, bybit:
        await asyncio.gather(
            _pipe(binance.stream_predicted(), builder),
            _pipe(bybit.stream_predicted(),   builder),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n↯ stopped by user")
