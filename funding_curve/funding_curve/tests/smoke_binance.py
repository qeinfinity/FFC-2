import asyncio
import sys
from funding_curve.funding_collectors import BinanceCollector

async def main():
    collector = BinanceCollector(symbol="BTCUSDT")

    i = 0
    async for fp in collector.stream_predicted():   # ← async generator
        print(fp)
        i += 1
        if i == 5:          # stop after 5 prints
            break

asyncio.run(main())