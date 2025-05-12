### How the live **funding-rate term-structure** is built

*(the “mental movie” from raw WebSocket ticks to an 8-bucket curve)*

---

## 1. Data ingress – one message ≈ one “bond quote”

```
text
CopyEdit
Exchange WS  ─▶  FundingPrint(exchange, symbol,
                               ts_snap,          ← when we saw it
                               predicted_rate,   ← % for *next* 8-h window
                               funding_time)     ← when that window settles

```

- **Binance** — every second on `…@markPrice`
- **Bybit** — every ≈100 ms on `tickers.BTCUSDT`
    
    Each print is conceptually a *forward rate* for the single 8-hour period beginning **now** and ending at `funding_time`.
    

---

## 2. Ring-buffer per exchange – the “sliding yield curve”

```
arduino
CopyEdit
             ┌──── bucket0 ────┐
deque(maxlen=8)  front-month         0–8h
                 ├ bucket1 ┤         8–16h
                 ├ bucket2 ┤        16–24h
                 ├ bucket3 ┤        24–32h
                 ├ bucket4 ┤        32–40h
                 ├ bucket5 ┤        40–48h
                 ├ bucket6 ┤        48–56h
                 └ bucket7 ┘        56–64h  ← “long end”

```

### Flow

1. **First time you connect** you receive the current *next-funding* print → it’s pushed into **bucket 0**.
2. The buffer is otherwise empty, so no curve snapshot yet (we haven’t seen the future windows).
3. **Keep listening**: every message with the **same `funding_time`** just updates **bucket 0** (better quote, still same window).
4. **When `funding_time` advances** (the 8-h event passed):
    - The *new* print is appended on the right;
    - The deque silently shifts left → yesterday’s *bucket0* becomes today’s *bucket1*, etc.;
    - After eight such advances (≈64 hours) the deque is full.
5. **From then on** every time bucket 0 is updated (≈each second) the builder calls `to_frame()` and emits an *8-row term-structure snapshot*.

---

## 3. Snapshot computation – “convert to annual yield”

```python
python
CopyEdit
fwd_rate_ann = (1 + rate_8h)**(24 * 365 / 8) - 1

```

*Level* = bucket 0’s annualised rate

*Slope* = bucket7 – bucket0

*Curvature* = bucket2 + bucket5 – 2×bucket3

(these factors can then be PCA’ed or fed raw into your regime model).

Each snapshot is appended to **`storage/processed/curve_live.parquet`** and optionally published to Kafka / printed to console.

---

## 4. How the curve evolves through time

```
sql
CopyEdit
Time 0h          8h          16h         …          64h
│------ bucket0 -----│
           │------ bucket0 -----│
                      │------ bucket0 -----│
...
│-b0-│-b1-│-b2-│ … │-b7-│        ← after 64 h the curve is “mature”

```

- You always see the **next eight successive 8-hour forward rates**.
- As real time moves forward, the entire curve slides left one bucket every 8 h, and a fresh right-most quote is discovered.
- Within each 8-h slice, bucket0 itself is updated every second—so you can watch front-end leverage cost spike or collapse in near-real-time.

---

## 5. Historical back-fill (same mechanics, just replayed fast)

1. Call `collector.backfill_realised(start, end)` in chronological chunks.
2. Iterate the resulting `FundingPrint`s **oldest → newest** through the *same* builder.
3. The builder’s ring-buffer fills and slides exactly as in live mode, emitting historical curve snapshots that land in `curve_history.parquet`.

Because the algorithm is identical, you get a seamless dataset where **2021 history joins today’s live feed without any gap**.

---

### TL;DR

*Every FundingPrint is a tiny bond quote for one 8-hour sub-period.*

The builder keeps the last eight such “bonds” in a deque, shifts it forward every time funding rolls, annualises each, and emits the whole 0–64 h term-structure—live every few seconds, historically by replaying prints.


Exactly — the **rolling 8-bucket strip** is less about forecasting a tradable
forward curve and more about capturing the *life-cycle of leverage
sentiment*. Buckets 1-7 are “fossil prints” of what traders thought funding
would cost 8 h, 16 h … 56 h ahead, and the way those prints decay or persist
is information you never see in a one-point “funding oscillator”.

Below is a concrete playbook for turning the live strip **plus a historical
back-fill** into features you can drop straight into your regime model.

---

## 1 Back-fill pipeline (one-off batch job)

| Step | Code pointers |
| --- | --- |
| **Chunk download** | For each exchange call `collector.backfill_realised(start, end)` in 7-day windows (API limits) until you reach today. |
| **Chronological replay** | Feed each `FundingPrint` into the same `FundingCurveBuilder.update()`; whenever it returns a snapshot, append it to **`storage/processed/curve_history.parquet`**. |
| **Merge live & history** | The live snapshot loop is already writing to `curve_live.parquet`.  Concatenate the two and sort by `ts_snap` for a seamless dataset. |
| **Sanity QC** | Plot bucket-0 vs. BTC price; plot slope (bucket7-bucket0) vs. perp-spot basis to confirm economic intuition. |

A working `pipelines/ingest.py` is only ~100 lines because it re-uses the
collector + builder code you already have.

---

## 2 Feature ideas that *exploit* the strip

| Feature | Formula (using annualised buckets) | Signal intuition |
| --- | --- | --- |
| **Level** | `bucket0_ann` | Front-end cost of leverage (classic funding oscillator). |
| **Slope** | `bucket7_ann − bucket0_ann` | Persistence of leverage: positive means traders *expected* rich funding 56 h ago and it stuck. |
| **Slope decay speed** | `bucket1_ann / bucket0_ann`, `bucket2_ann / bucket1_ann`, … | How quickly funding shocks bleed out.  Sharp decay ⇒ transient squeeze; slow decay ⇒ regime. |
| **Convexity / curvature** | `bucket2 + bucket5 − 2×bucket3` | Flags “hockey-stick” shapes that often precede liquidations. |
| **Time-since-shock indicator** | Index of the leftmost bucket where ` | bucket_k − bucket0 |
| **PCA factors** | First 2 PCs of the 8-vector (re-fit monthly) | Captures orthogonal shape changes; feed directly to tree/NN models. |
| **Cross-venue dispersion** | Std-dev of bucket0 across Binance/Bybit/OKX | Measures consensus vs. idiosyncratic stress. |

All of these come straight from the strip; nothing to extrapolate.

---

## 3 Where to use them

- **Regime classifier** – bucket-decay speed and convexity are strong
predictors of *volatility regime changes* (e.g., front spike with fast decay
often precedes short-squeeze rallies).
- **Position sizing** – when slope ≫ 0 and convexity bullish, allow higher
gross; when slope flips negative *and* convexity turns concave, cut risk.
- **Basis-arb timing** – if bucket0 drops sharply but slope is still steep,
perp shorts are likely crowded: widen basis threshold before legging into a
carry trade.


## 0 — Scope checkpoint
Goal: Every 10 min produce a clean, USD-annualised curve of forward funding rates for BTC perpetuals on the top derivatives venues (0–8 h, 8–16 h, … , 56–64 h).
Live requirements: ≤2 s end-to-end latency, full tick-backfill since Jan-2020, REST+WebSocket for real-time.

## 1 — Pick the venues
Binance USD-M	Deepest liquidity; publishes predicted funding every second.	GET /fapi/v1/premiumIndex (realtime prediction & next funding ts) 
Binance Developers
Binance
No forward curve—​we’ll roll snapshots.

Bybit USDT-Perp	Minute-level predicted funding; clear formula docs.	wss://stream.bybit.com/perpetual/funding_rate + REST /v5/market/funding/history 
Bybit
Bybit
Gives TWAP inside current window.

OKX Swap	Good cross-venue dispersion; stable WebSocket.	public/funding-rate (REST) + WS funding-rate subscription 
OKX
OKX
UTC timestamps, no caps/floors.

(Optional) Deribit	8-h funding but crypto-native yield audience.	public/get_funding_rate_value 
Deribit Docs
Deribit Insights
Smaller volume; nice reality-check.

(Optional) Aggregator (Kaiko, Amberdata)	Fallback + historical backfill if exchange data gap.	Amberdata /funding-rates 
Amberdata API
Paid; keep as emergency plug.


## 2 — Repository scaffold (Python ≥3.11, Poetry)
```
.
├── funding_curve
│   ├── funding_curve
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   ├── builders
│   │   ├── collectors
│   │   ├── feature_build.py
│   │   ├── feature_extractor.py
│   │   ├── funding_collectors.py
│   │   ├── pipelines
│   │   ├── storage
│   │   ├── tests
│   │   └── utils
│   ├── myenv
│   │   ├── bin
│   │   ├── include
│   │   ├── lib
│   │   └── pyvenv.cfg
│   ├── notebooks
│   │   ├── __pycache__
│   │   ├── 01_curve_qc.ipynb
│   │   ├── 01_curve_qc.py
│   │   └── storage
│   ├── notes.md
│   ├── poetry.lock
│   ├── pyproject.toml
│   ├── README.md
│   └── storage
│       └── processed
├── pca.md
└── scaffold.sh

19 directories, 13 files
boscovich@Boscovichs-MacBook-Pro funding_termstructure % tree -L 4
.
├── funding_curve
│   ├── funding_curve
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── __init__.cpython-313.pyc
│   │   │   ├── feature_build.cpython-313.pyc
│   │   │   ├── feature_extractor.cpython-313.pyc
│   │   │   ├── feature_store.cpython-313.pyc
│   │   │   └── funding_collectors.cpython-313.pyc
│   │   ├── builders
│   │   │   ├── __init__.py
│   │   │   ├── __pycache__
│   │   │   └── curve.py
│   │   ├── collectors
│   │   │   └── __init__.py
│   │   ├── feature_build.py
│   │   ├── feature_extractor.py
│   │   ├── funding_collectors.py
│   │   ├── pipelines
│   │   │   ├── __init__.py
│   │   │   ├── __pycache__
│   │   │   ├── ingest.py
│   │   │   └── snapshot.py
│   │   ├── storage
│   │   │   ├── __init__.py
│   │   │   ├── db.py
│   │   │   └── schemas.py
│   │   ├── tests
│   │   │   ├── __init__.py
│   │   │   ├── __pycache__
│   │   │   ├── smoke_binance.py
│   │   │   ├── smoke_bybit.py
│   │   │   └── test_builders.py
│   │   └── utils
│   │       ├── __init__.py
│   │       └── time.py
│   ├── myenv
│   │   ├── bin
│   │   │   ├── activate
│   │   │   ├── activate.csh
│   │   │   ├── activate.fish
│   │   │   ├── Activate.ps1
│   │   │   ├── pip
│   │   │   ├── pip3
│   │   │   ├── pip3.9
│   │   │   ├── python -> python3
│   │   │   ├── python3 -> /Library/Developer/CommandLineTools/usr/bin/python3
│   │   │   └── python3.9 -> python3
│   │   ├── include
│   │   ├── lib
│   │   │   └── python3.9
│   │   └── pyvenv.cfg
│   ├── notebooks
│   │   ├── __pycache__
│   │   │   └── 01_curve_qc.cpython-313.pyc
│   │   ├── 01_curve_qc.ipynb
│   │   ├── 01_curve_qc.py
│   │   └── storage
│   │       └── processed
│   ├── notes.md
│   ├── poetry.lock
│   ├── pyproject.toml
│   ├── README.md
│   └── storage
│       └── processed
│           ├── curve_history.parquet
│           ├── curve_live.parquet
│           └── feature_store.parquet
├── pca.md
└── scaffold.sh

24 directories, 47 files
```

### Key package choices
aiohttp + websockets for async sockets.
pandas / polars for fast frame ops.
pydantic v2 for strict typing + validation.
SQLModel (or Prisma) + Postgres if you need SQL, else straight Parquet on S3/GCS.


## 3 — Collector design
Each concrete collector (`binance.py`, `bybit.py`) should:  (*collectors all merged into `funding_curve/funding_curve/funding_collectors.py`*)

1. **WebSocket task** – keep an async listener that yields `(timestamp, predicted_rate, funding_time)` every second/minute.
2. **REST backfill** – pull realised funding history at start-up to fill gaps.
3. **Rate-limit guard** – Binance: 500 weight/5 min cap [Binance Developers](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History?utm_source=chatgpt.com).

Store raw JSON *verbatim* in `storage/raw/{exchange}/{YYYY-MM}.parquet` for audit.


## 4 — Curve builder
`FILE: funding_curve/funding_curve/builders/curve.py`
Algorithm:

1. **Snapshot roll-forward**
    - At T₀ you know funding for window `[T_next, T_next+8h)`.
    - Store it as **bucket 0**.
    - Retain last 7 snapshots; when a funding event passes, shift queue → you now have buckets 0-7.
2. **Annualise**
    
    r8hann=(1+r)24⋅3658−1r_{8h}^{\text{ann}} = (1+r)^{\frac{24·365}{8}}-1r8hann=(1+r)824⋅365−1
    
3. **Interpolation (optional)**
    
    If bucket missing (exchange outage), linear-interpolate in **yield** space.
    

Emit one tidy curve every 10 min to `storage/processed/curve_{exchange}.parquet`.



## 5 — Quick sanity notebook (`notebooks/01_curve_qc.ipynb`)
1. Plot level & slope z-scores for Binance and Bybit.
2. Scatter curve slope vs. next-1h BTC realised vol.
3. Check missing-bucket percentage < 2 % over last 30 days.


## 6 — Testing & CI


## 7 — Next milestones (after curve is live)
| Phase                 | Deliverable 
| ---                   | ---           
| **Enrichment**        | Merge perp-spot basis (`GET /fapi/v1/premiumIndex`) and OI from `/futures/data/v1` into same snapshot. 
| **Feature store**     | Feather/Parquet feature matrix with PCA factors: *level*, *slope*, *curvature*, *1st PC*, *2nd PC*. 
| **Model integration** | Add curve factors into our regime-classifier pipeline alongside tick features. 
| **Stress dashboard**  | Stream curve to Grafana; alert on z-score > +2 or < -2. 



## Generate poetry env
1. added dependenccies to pyproject.toml


## Create tests/smoke_binance.py
`python -m poetry run python tests/smoke_binance.py`


# so far were attempting to get funding rate term structure or FFC-2 snapshots from bybit and binance


### Why we aren’t seeing curve snapshots yet

- **The builder only emits a snapshot after its 8-bucket deque is full.**
    
    Each bucket represents one entire 8-hour funding window:
    

```
bucket 0  : 0 – 8 h ahead of “now”
bucket 1  : 8 – 16 h ahead  (was bucket-0 8 h ago)
…
bucket 7  : 56 – 64 h ahead

```

If we start the process “cold” the deque is empty, so we'll need **64 hours
of live streaming** before the first snapshot can print—exactly what we're
experiencing.

---

## Instant fix: **pre-warm** the builder with the last 7 realised funding prints

1. **Pull the most recent 7 funding events** (one per 8-h window) via each
collector’s `backfill_realised()` method.
2. Feed them—oldest first—into the builder **before** we start the live pipes.
That seeds buckets 1-7, so the very next live quote becomes bucket 0 and a
snapshot is emitted within seconds.

`funding_curve/pipelines/snapshot.py`.
It back-fills ~2 days automatically and prints a curve as soon as bucket 0
ticks.

*Our current FundingCurveBuilder.update() fires a snapshot whenever it receives any FundingPrint and the deque is full, so after the one-time seeding we indeed get an 8-row frame every second.
That’s great if we want to watch the front-end leverage cost tick by tick, but it’s noisy for a regime model that cares about the shape only when the curve genuinely slides.*

we have made the choice to retain the front end micronoise. we have no throttled the snapshot to per 8 hour funding roll

| If our downstream task… | Snapshot cadence that makes sense | Why |
| --- | --- | --- |
| **Wants to track front-end leverage micro-movements** (e.g. liquidation monitors, intraday risk dashboard) | *Every time bucket 0 updates* (≈ 1 s on Binance) | we care about the *instant* change in funding cost, so throttle-off would hide useful information. |
| **Detects regime shifts / trades multi-hour swings** | *Once per 8-hour roll* (when `funding_time` advances) | The curve’s *shape* is what matters; extra per-second frames add noise and blow up storage for no new information. |
| **Feeds a model that resamples everything later** (e.g. we'll down-sample to 30 min bars anyway) | Keep the fast frames and resample offline | Gives maximum flexibility; heavier to store, but easy to aggregate. |
| **Is strictly end-of-day / end-of-session** | Emit *only* on roll **and** aggregate those into daily features | Smallest data footprint; enough for most macro-style analyses. |




## backfill historical data
`funding_curve/funding_curve/storage/processed` containt the parquet data

First time takes ~3-5 min per year per exchange (API paginated to 500–1000 rows).
The resulting curve_history.parquet aligns 1-to-1 with the curve_live.parquet file our snapshot loop is updating—append them or just query both as one DuckDB view.



## Create a notebook to plot the data
`funding_curve/notebooks`
initially we attempted to broadcast price to every bucket row. However this didnt work (need to generate factors and pca) *INVESTIGATE FULL REASON WHY*

we instead plot the single bucket 0 against btcusd daily close in the notebook 
we also plot funding curve slope against return but of course yields nothing because there is no slope computed yet




```
**Dual risk pre-mortem (current FFC-2 branch)**  
Operational  
• Parquet fire-hose: 1 Hz snapshots → ≥40 GB/mo; slow append & fragile merge.  
• `_reconnect()` stops after first yield → no exponential back-off; WS death = silent stall.  
• Builder lacks gap-fill (network-brownout → NaNs propagate into PCA).  
• PCA fitted once on full history; semver drift when live buckets ≠ training (rank-deficient).  
• No CI over async collectors → schema/field drift undetected until runtime.  
• Single process writes both exchanges → GIL contention spikes latency >2 s budget.  

Tactical / market  
• Second-PC may re-orient under regime shift; static loadings risk alpha flip.  
• Retaining per-second micro-noise for regime classifier likely degrades SHARPE via label blur.  
• Funding prints can be spoofed near roll; no outlier guard inflates variance.  
• Crowding: many desks now use curve slope; alpha half-life <12 m without decay monitor.  

**Integrated pipeline context (key decisions to revisit)**  
Target-regime alignment: Using 1-s cadence implies intraday **Vol-Expansion** edge; but FFC-2 thesis is 8-h liquidation pivot → best for **Range→Trend-Down** transitions.  
Market-mechanics: predicted funding often quotes stale within window; need last-trade TPS sync.  
Latency budget: <2 s OK if snapshots throttled to roll; current design wastes 95 % CPU on unwritten duplicates.  
Edge-decay monitor: not wired; add live SHARPE vs. back-test tracker on 30-sample rolling.  

**Unknowns / clarifications**  
1. Do downstream models truly need sub-minute FFC-2, or is 8-h roll sufficient?  
2. Storage target (S3 + DuckDB, Postgres, or in-memory)? Impacts writer design.  
3. Planned PCA refit cadence (rolling 21 d? monthly) and deployment path?  
```

so now will running snapshot.py produce a curve_live.parquet that is a reasonable size? after we get a new curve_live.parquet im assuming we need to run feature_build again, and then the final cell of the notebook that was previously taking more than 10mins and timing out should run 

```
Snapshot loop now instantiates `FundingCurveBuilder()` → default `emit_on_roll=True`.  
Outcome: **one snapshot per 8-h funding roll per exchange** → 3 × 2 = 6 snapshots · 8 rows ≈ 48 rows day⁻¹.  
At ~200 B row⁻¹ (Parquet+Snappy) that is **<10 kB day⁻¹**, i.e. <4 MB yr⁻¹ — essentially negligible versus the previous ≳40 GB mo⁻¹ fire-hose.  

`seed_builder()` still feeds the last seven realised prints; they bypass the throttle because each has a distinct `funding_time`. You’ll see ≤7 historical snapshots written on start-up, then one every roll.  

Downstream:  
• `feature_build.py` unions `curve_history`+`curve_live` and recomputes factors — with the live file now tiny, wall-time drops from minutes to seconds.  
• Notebook’s final cell mainly spends time loading `feature_store.parquet`; with the reduced live delta it should execute comfortably under **30 s** even without Polars.  
```

ok yes but the effort is not just live emissions but to explore the historical funding rate curve (which the last cell in the 01_curve_qc.ipynb seeks to do
will running snapshot take care of the backfill correctly ? i believe curve_live has every new snapshot appended to it but also backfilled with curve_history. 

*patched `FILE: funding_curve/pipelines/snapshot.py `*

```
**Behaviour after patch**

* **Back-fill coverage** – snapshot loop now writes an initial 8-row curve for each exchange using the last 64 h realised prints; you **no longer need to run the heavy `ingest.py`** just to explore recent history.  
* **File growth** – afterward only one 8-row snapshot per 8-h roll is appended, keeping `curve_live.parquet` tiny (≈ 4 MB yr⁻¹).  
* **Historical research** – for reaches beyond 64 h simply run `ingest.py` once (start date configurable) which saves to `curve_history.parquet`; `feature_build` automatically unions both files.  
* **Notebook performance** – feature build now touches a small delta; the final cell loads a lean `feature_store.parquet` and should finish in <30 s on a laptop.  
```

```
**Workflow recap**

```
# one-off long back-fill (optional for >64 h look-back)
poetry run python -m funding_curve.pipelines.ingest --start 2021-01-01

# start live throttled loop (includes 64 h seed snapshot)
poetry run python -m funding_curve.pipelines.snapshot
# keep running in tmux / systemd

# whenever you want fresh factors
poetry run python -m funding_curve.feature_build
# -> storage/processed/feature_store.parquet updated

# open notebooks/01_curve_qc.ipynb  – cells now fast
```



RAN THE LONG BACKFILL BUT SOMEHOW ISNT ENDING UP IN FEATURE_STORE.PARQUET.

```
funding-curve-py3.13boscovich@Mac funding_curve % poetry run python -m funding_curve.pipelines.ingest --start 2021-01-01
2025-05-05 23:46:22.543 | INFO     | __main__:_ingest_exchange:59 - [binance] ingesting 2021-01-01 → 2025-05-05
2025-05-05 23:46:22.543 | INFO     | __main__:_ingest_exchange:59 - [bybit] ingesting 2021-01-01 → 2025-05-05
2025-05-05 23:58:50.411 | SUCCESS  | __main__:_ingest_exchange:65 - [binance] done.
2025-05-05 23:59:01.058 | SUCCESS  | __main__:_ingest_exchange:65 - [bybit] done.
2025-05-05 23:59:01.058 | INFO     | __main__:main:92 - Historical ingest complete. File saved to %s
funding-curve-py3.13boscovich@Mac funding_curve % poetry run python -m funding_curve.pipelines.snapshot
/Users/boscovich/Documents/dev/funding_termstructure/funding_curve/funding_curve/pipelines/snapshot.py:71: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
  now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
2025-05-06 00:00:41.912 | WARNING  | __main__:main:90 - No initial snapshot generated during seeding phase.
2025-05-06 00:00:42.761 | DEBUG    | __main__:_pipe:54 - live snapshot appended: %s / %s
2025-05-06 00:00:45.158 | DEBUG    | __main__:_pipe:54 - live snapshot appended: %s / %s

```

