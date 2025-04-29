"""funding_curve collectors and config module
This single module contains:
- Settings (config)
- FundingPrint dataclass (shared schema)
- Abstract FundingCollector interface
- Concrete BinanceCollector and BybitCollector implementations that satisfy:
    * Async WebSocket streaming of *predicted* 8‑hour funding prints in real‑time (≤1 s latency)
    * REST back‑fill of realised funding history for any gap
The collectors yield `FundingPrint` objects that can be piped into the FundingCurveBuilder.

Usage example (run inside an `asyncio` event‑loop):

```python
from funding_collectors import BinanceCollector, BybitCollector

async def main():
    async with BinanceCollector() as bnc:
        async for fp in bnc.stream_predicted():
            print(fp)
```
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncGenerator, AsyncIterator, Optional
import websockets

import aiohttp

logger = logging.getLogger(__name__)

###############################################################################
# CONFIG
###############################################################################

class Settings:
    """Centralised config (env‑override friendly)."""

    BINANCE_REST_URL: str = os.getenv("BINANCE_REST_URL", "https://fapi.binance.com")
    BINANCE_WS_URL: str = os.getenv("BINANCE_WS_URL", "wss://fstream.binance.com/ws")

    BYBIT_REST_URL: str = os.getenv("BYBIT_REST_URL", "https://api.bybit.com")
    BYBIT_WS_URL: str = os.getenv("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear")

    API_TIMEOUT: int = int(os.getenv("API_TIMEOUT", "10"))  # seconds

    SYMBOL: str = os.getenv("FUNDING_SYMBOL", "BTCUSDT")


settings = Settings()

###############################################################################
# SHARED DATACLASS & BASE COLLECTOR
###############################################################################


@dataclass(slots=True)
class FundingPrint:
    exchange: str
    symbol: str
    ts_snap: datetime  # when prediction observed (UTC)
    predicted_rate: float  # raw 8‑hour rate (decimal, e.g. 0.0001 = 0.01 %)
    funding_time: datetime  # scheduled funding timestamp (UTC)


class FundingCollector(ABC):
    """Abstract interface every venue collector must implement."""

    def __init__(self, symbol: str | None = None):
        self.symbol = symbol or settings.SYMBOL
        self._session: Optional[aiohttp.ClientSession] = None

    # ---------------------------------------------------------------------
    # Async context manager helpers
    # ---------------------------------------------------------------------
    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=settings.API_TIMEOUT))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------------------------------------------------------------------
    # REST back‑fill (blocking per call)
    # ---------------------------------------------------------------------
    @abstractmethod
    async def backfill_realised(self, start: datetime, end: datetime) -> list[FundingPrint]:
        """Return historic realised funding between *start* and *end* (inclusive)."""

    # ---------------------------------------------------------------------
    # Live WebSocket stream
    # ---------------------------------------------------------------------
    @abstractmethod
    async def stream_predicted(self) -> AsyncIterator[FundingPrint]:
        """Yield *predicted* 8‑hour funding prints as they arrive."""

###############################################################################
# BINANCE COLLECTOR
###############################################################################


class BinanceCollector(FundingCollector):
    """Binance USD‑M perpetual funding collector."""

    _WS_PATH_FMT = "{base}/{channel}"

    async def backfill_realised(self, start: datetime, end: datetime) -> list[FundingPrint]:
        assert self._session, "Session not initialised. Use `async with` or call `setup()` first."
        url = f"{settings.BINANCE_REST_URL}/fapi/v1/fundingRate"
        params = {
            "symbol": self.symbol.upper(),
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 1000,
        }
        prints: list[FundingPrint] = []
        while True:
            async with self._session.get(url, params=params) as resp:
                data = await resp.json()
            if not data:
                break
            for item in data:
                prints.append(
                    FundingPrint(
                        exchange="binance",
                        symbol=self.symbol,
                        ts_snap=datetime.fromtimestamp(item["fundingTime"] / 1000, tz=timezone.utc),
                        predicted_rate=float(item["fundingRate"]),
                        funding_time=datetime.fromtimestamp(item["fundingTime"] / 1000, tz=timezone.utc),
                    )
                )
            # pagination: break because endpoint returns contiguous data when limit≤1000
            if len(data) < 1000 or data[-1]["fundingTime"] >= params["endTime"]:
                break
            params["startTime"] = data[-1]["fundingTime"] + 1
        return prints

    async def stream_predicted(self) -> AsyncIterator[FundingPrint]:
        channel = f"{self.symbol.lower()}@markPrice"
        ws_url = self._WS_PATH_FMT.format(base=settings.BINANCE_WS_URL, channel=channel)
        async for retry in _reconnect():
            try:
                async with aiohttp.ClientSession() as ws_session:
                    async with ws_session.ws_connect(ws_url, heartbeat=60) as ws:
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            data = json.loads(msg.data)
                            fp = FundingPrint(
                                exchange="binance",
                                symbol=self.symbol,
                                ts_snap=datetime.utcnow().replace(tzinfo=timezone.utc),
                                predicted_rate=float(data["r"]),
                                funding_time=datetime.fromtimestamp(data["T"] / 1000, tz=timezone.utc),
                            )
                            yield fp
            except Exception as e:
                logger.exception("Binance WS error: %s", e)
                await asyncio.sleep(retry)

###############################################################################
# BYBIT COLLECTOR (v5 public)
###############################################################################


class BybitCollector(FundingCollector):
    """Bybit USDT perpetual funding collector."""

    exchange: str = "bybit"

    async def backfill_realised(self, start: datetime, end: datetime) -> list[FundingPrint]:
        assert self._session, "Session not initialised. Use `async with` first."
        url = f"{settings.BYBIT_REST_URL}/v5/market/funding/history"
        params = {
            "category": "linear",
            "symbol": self.symbol.upper(),
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 200,
        }
        prints: list[FundingPrint] = []
        while True:
            async with self._session.get(url, params=params) as resp:
                payload = await resp.json()
            rows = payload.get("result", {}).get("list", [])
            if not rows:
                break
            for item in rows:
                prints.append(
                    FundingPrint(
                        exchange="bybit",
                        symbol=self.symbol,
                        ts_snap=datetime.fromtimestamp(int(item["fundingRateTimestamp"]) / 1000, tz=timezone.utc),
                        predicted_rate=float(item["fundingRate"]),
                        funding_time=datetime.fromtimestamp(int(item["fundingRateTimestamp"]) / 1000, tz=timezone.utc),
                    )
                )
            # Bybit returns data in descending order
            last_ts = int(rows[-1]["fundingRateTimestamp"])
            if last_ts <= params["startTime"] or len(rows) < params["limit"]:
                break
            params["endTime"] = last_ts - 1
        return prints

    async def stream_predicted(self) -> AsyncGenerator[FundingPrint, None]:
        """Yield FundingPrint once per Bybit ticker message (≈ every 100 ms)."""
        url      = settings.BYBIT_WS_URL          # wss://stream.bybit.com/v5/public/linear
        topic    = f"tickers.{self.symbol}"       # e.g. tickers.BTCUSDT
        timeout  = aiohttp.ClientTimeout(total=settings.API_TIMEOUT)

        async with aiohttp.ClientSession(timeout=timeout) as ws_session:
            while True:
                try:
                    async with ws_session.ws_connect(url, heartbeat=30) as ws:
                        await ws.send_json({"op": "subscribe", "args": [topic]})

                        latest_rate   = None     # cache fundingRate
                        latest_ftime  = None     # cache nextFundingTime

                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue

                            payload = json.loads(msg.data)
                            if payload.get("topic") != topic:
                                continue

                            snap = payload["data"][0] if isinstance(payload["data"], list) else payload["data"]

                            # update cache **only** when key present
                            if "fundingRate" in snap:
                                latest_rate  = float(snap["fundingRate"])
                            if "nextFundingTime" in snap:
                                latest_ftime = int(snap["nextFundingTime"])

                            # yield only if both cached values are populated
                            if latest_rate is not None and latest_ftime is not None:
                                yield FundingPrint(
                                    exchange=self.exchange,          # "bybit"
                                    symbol=self.symbol,              # "BTCUSDT"
                                    ts_snap=datetime.utcfromtimestamp(payload["ts"] / 1000).replace(tzinfo=timezone.utc),
                                    predicted_rate=latest_rate,
                                    funding_time=datetime.utcfromtimestamp(latest_ftime / 1000).replace(tzinfo=timezone.utc),
                                )

                except (aiohttp.ClientError,
                        asyncio.TimeoutError,
                        websockets.ConnectionClosed) as err:
                    logger.warning("Bybit WS reconnect: %s", err)
                    await asyncio.sleep(3)
                    continue

###############################################################################
# RECONNECT BACKOFF UTILITY
###############################################################################


async def _reconnect(max_retries: int = 5, backoff: float = 1.5):
    """
    Yields retry-counter and waits with exponential back-off.
    """
    retry = 0
    while True:
        yield retry           # << now it's an *async* yield
        return                #   exit after first pass (or tailor to your logic)
        # If you want a loop, move the network call into a try/except
        # and 'continue' when ConnectionClosed is caught.