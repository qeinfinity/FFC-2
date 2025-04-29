# =============================================================
# FILE: funding_curve/pipelines/snapshot.py
# =============================================================
"""Live 10‑min funding‑curve snapshot loop.

Run:
    poetry run python -m funding_curve.pipelines.snapshot
"""
# funding_curve/pipelines/snapshot.py
import os
import asyncio
from datetime import datetime, timedelta, timezone
import pandas as pd

from funding_curve.builders.curve import FundingCurveBuilder
from funding_curve.funding_collectors import BinanceCollector, BybitCollector

SNAP_PATH = "storage/processed/curve_live.parquet"
ENGINE    = "fastparquet"                        # ← new constant


async def seed_builder(builder, collector, now_utc):
    """Load the last 7 realised funding prints into the builder (oldest→newest)."""
    start = now_utc - timedelta(hours=64 + 1)   # little buffer
    end   = now_utc - timedelta(seconds=1)

    async with collector:
        prints = await collector.backfill_realised(start, end)

    # keep only the *last* 7 events (one per bucket), sorted oldest→newest
    prints = sorted(prints, key=lambda p: p.funding_time)[-7:]
    for fp in prints:
        builder.update(fp)          # pre-fill buckets 7→1


async def _pipe(stream, builder):
    async for fp in stream:
        snap = builder.update(fp)
        if snap is not None:
            print(snap)

            # ----- fixed parquet append -----
            snap.to_parquet(
                SNAP_PATH,
                engine=ENGINE,
                compression="snappy",
                index=False,
                append=os.path.exists(SNAP_PATH),   # append only after first chunk
            )


async def main():
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)

    builder  = FundingCurveBuilder()
    binance  = BinanceCollector()
    bybit    = BybitCollector()

    # 1️⃣  pre-warm with last 64 h history
    await asyncio.gather(
        seed_builder(builder, binance, now_utc),
        seed_builder(builder, bybit,  now_utc),
    )

    # 2️⃣  start live streams
    async with binance, bybit:
        await asyncio.gather(
            _pipe(binance.stream_predicted(), builder),
            _pipe(bybit.stream_predicted(),   builder),
        )

if __name__ == "__main__":
    asyncio.run(main())
