"""funding_curve/pipelines/ingest.py

Batch back‑fill of funding‑rate term‑structure history.

* Pulls realised funding events from each exchange chunk‑by‑chunk
  (API‑limit friendly) starting from a user‑configured START_DATE.
* Feeds them through FundingCurveBuilder to generate an 8‑bucket curve
  snapshot *every time the funding window rolls*.
* Writes the snapshots to storage/processed/curve_history.parquet
  using fastparquet (append‑safe) then exits.

Run once:

    poetry run python -m funding_curve.pipelines.ingest --start 2021-01-01

You can rerun later with a later start date; the script will append without
rewriting.
"""
from __future__ import annotations

import asyncio
import os
import argparse
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pandas as pd
from loguru import logger

from funding_curve.builders.curve import FundingCurveBuilder
from funding_curve.funding_collectors import BinanceCollector, BybitCollector

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
DEFAULT_START = "2021-01-01"            # fallback if CLI flag missing
OUT_PATH      = "storage/processed/curve_history.parquet"
ENGINE        = "fastparquet"           # matches live pipeline
CHUNK_HOURS   = 24 * 7                  # one‑week chunks (API safe)


# ---------------------------------------------------------------------------
# UTILS
# ---------------------------------------------------------------------------
async def _historical_stream(collector, start: datetime, end: datetime) -> AsyncIterator:
    """Yield FundingPrint in *chronological* order between start‑end."""
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(hours=CHUNK_HOURS - 1), end)
        prints = await collector.backfill_realised(cursor, chunk_end)
        # backfill_realised returns list (may be empty)
        for fp in sorted(prints, key=lambda p: p.funding_time):
            yield fp
        # move cursor one ms past last chunk_end to avoid overlap
        cursor = chunk_end + timedelta(milliseconds=1)


async def _ingest_exchange(name: str, collector_cls, start: datetime, end: datetime, builder: FundingCurveBuilder):
    logger.info(f"[{name}] ingesting {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    async with collector_cls() as coll:
        async for fp in _historical_stream(coll, start, end):
            snap = builder.update(fp)
            if snap is not None:
                _append_parquet(snap, OUT_PATH)
    logger.success(f"[{name}] done.")


def _append_parquet(df: pd.DataFrame, path: str):
    df.to_parquet(
        path,
        engine=ENGINE,
        compression="snappy",
        index=False,
        append=os.path.exists(path),
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
async def main(start_date: str):
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.now(timezone.utc)

    builder = FundingCurveBuilder()   # shared deque per exchange

    await asyncio.gather(
        _ingest_exchange("binance", BinanceCollector, start_dt, end_dt, builder),
        _ingest_exchange("bybit",   BybitCollector,   start_dt, end_dt, builder),
    )

    logger.info("Historical ingest complete. File saved to %s", OUT_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Back‑fill funding curve history")
    parser.add_argument("--start", default=os.getenv("START_DATE", DEFAULT_START), help="YYYY‑MM‑DD")
    args = parser.parse_args()

    asyncio.run(main(args.start))
