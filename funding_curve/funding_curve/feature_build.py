"""feature_build.py  (patched v1.2 – 2025‑05‑06)

Batch job to construct the **feature store** from historical+live
funding‑curve snapshots.  This version hardens numerical stability:

• Safe decay ratios – guard against divide‑by‑zero and clip extreme values.
• Winsorise raw bucket array ±5 σ *before* PCA to stop overflow.
• Drop rows with any non‑finite bucket post‑winsorisation.
• Standardise buckets (mean‑0, sd‑1) before PCA for scale invariance.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import yfinance as yf

SRC_HISTORY = Path("storage/processed/curve_history.parquet")
SRC_LIVE    = Path("storage/processed/curve_live.parquet")
DST_FEATURE = Path("notebooks/storage/processed/feature_store.parquet")

BUCKET_COLS = [f"b_{h}" for h in range(0, 64, 8)]
WINSOR_Z    = 5.0   # clip buckets to ±5 σ before PCA

# ---------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------

def load_curve_long() -> pd.DataFrame:
    """Concatenate historical + live snapshot parquet files."""
    df = pd.read_parquet(SRC_HISTORY, engine="fastparquet")
    if SRC_LIVE.exists():
        df_live = pd.read_parquet(SRC_LIVE, engine="fastparquet")
        df = pd.concat([df, df_live], ignore_index=True)
    return df

def pivot_wide(df_long: pd.DataFrame, *, agg: str = "last") -> pd.DataFrame:
    """Return wide DataFrame with one row per **ts_snap × exchange**.

    Duplicate snapshots for the same bucket can occur if both Binance and
    Bybit roll at nearly the same second or if historical ingest overlaps
    with live replay.  We resolve duplicates with *agg* (default "last").
    """
    wide = (
        df_long.pivot_table(
            index=["ts_snap", "exchange"],          # keep exchange separate
            columns="bucket_start_h",
            values="fwd_rate_ann",
            aggfunc=agg,                            # resolves duplicates
        )
        .rename(columns=lambda h: f"b_{int(h)}")
        .sort_index()
    )

    # keep only rows with a complete 8‑bucket strip
    wide = wide.dropna(subset=BUCKET_COLS)

    # flatten index → continuous DateTime index (choose first exchange row)
    wide = (
        wide.reset_index("exchange")                # exchange becomes column
            .groupby("ts_snap", sort=True)          # merge exchanges (mean)
            .mean(numeric_only=True)
    )
    return wide

# ---------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------

def _winsorise(arr: np.ndarray, z: float = WINSOR_Z) -> np.ndarray:
    """Winsorise array to ±z standard deviations (in‑place safe)."""
    mu  = np.nanmean(arr)
    std = np.nanstd(arr)
    if std == 0 or np.isnan(std):
        return arr  # nothing we can do
    lo, hi = mu - z * std, mu + z * std
    return np.clip(arr, lo, hi, out=arr)


def attach_price(df: pd.DataFrame) -> pd.DataFrame:
    start = df.index.min().strftime("%Y-%m-%d")
    end   = (df.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    closes = (
        yf.download("BTC-USD", start=start, end=end, progress=False)
        .loc[:, ["Close"]]
        .rename(columns={"Close": "btc_close"})
        .tz_localize("UTC")
    )
    df["btc_close"]  = closes.reindex(df.index, method="ffill")
    df["btc_ret_1d"] = df["btc_close"].pct_change().shift(-1)
    return df.dropna(subset=["btc_ret_1d"])


def compute_curve_factors(df: pd.DataFrame) -> pd.DataFrame:
    buckets = df[BUCKET_COLS].to_numpy(dtype="float64")

    # ------------------------------------------------------------------
    # Winsorise & drop non‑finite rows
    # ------------------------------------------------------------------
    for j in range(buckets.shape[1]):
        _winsorise(buckets[:, j])
    mask_finite = np.isfinite(buckets).all(axis=1)
    df = df.loc[mask_finite].copy()
    buckets = buckets[mask_finite]

    # ------------------------------------------------------------------
    # Scale buckets → PCA for first 2 components
    # ------------------------------------------------------------------
    scaler = StandardScaler()
    buckets_z = scaler.fit_transform(buckets)

    pca   = PCA(n_components=2, svd_solver="full", random_state=42)
    pcs   = pca.fit_transform(buckets_z)

    # ------------------------------------------------------------------
    # Pointwise engineered factors
    # ------------------------------------------------------------------
    level  = buckets[:, 0]
    slope  = buckets[:, -1] - buckets[:, 0]

    # divide‑by‑zero safe decay ratios
    decay1 = np.divide(buckets[:, 1], buckets[:, 0], out=np.full_like(level, np.nan), where=buckets[:, 0] != 0)
    decay2 = np.divide(buckets[:, 2], buckets[:, 1], out=np.full_like(level, np.nan), where=buckets[:, 1] != 0)

    convexity = buckets[:, 2] + buckets[:, 5] - 2 * buckets[:, 3]

    df["level"]     = level
    df["slope"]     = slope
    df["decay1"]    = decay1
    df["decay2"]    = decay2
    df["convexity"] = convexity
    df["pca1"]      = pcs[:, 0]
    df["pca2"]      = pcs[:, 1]

    return df

# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------

def build_feature_store() -> None:
    df_wide = pivot_wide(load_curve_long())
    df_wide = attach_price(df_wide)
    df_feat = compute_curve_factors(df_wide)

    df_feat.to_parquet(DST_FEATURE, engine="fastparquet", compression="snappy")
    print(
        f"✅ feature_store written → {DST_FEATURE}  "
        f"({len(df_feat):,} rows, {len(df_feat.columns)} columns)"
    )


if __name__ == "__main__":
    build_feature_store()
