from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.decomposition import PCA

SRC_HISTORY = Path("notebooks/storage/processed/curve_history.parquet")
SRC_LIVE    = Path("notebooks/storage/processed/curve_live.parquet")
DST_FEATURE = Path("notebooks/storage/processed/feature_store.parquet")

BUCKET_COLS = [f"b_{h}" for h in range(0, 64, 8)]

# ---------------------------------------------------------------------
def load_curve_long() -> pd.DataFrame:
    df = pd.read_parquet(SRC_HISTORY, engine="fastparquet")
    if SRC_LIVE.exists():
        df_live = pd.read_parquet(SRC_LIVE, engine="fastparquet")
        df = pd.concat([df, df_live], ignore_index=True)
    return df

def pivot_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    wide = (
        df_long.pivot_table(index="ts_snap",
                            columns="bucket_start_h",
                            values="fwd_rate_ann")
             .rename(columns=lambda h: f"b_{int(h)}")
             .sort_index()
             .dropna()                         # ensure 8 complete buckets
    )
    return wide

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
    buckets = df[BUCKET_COLS].to_numpy()

    level     = buckets[:, 0]
    slope     = buckets[:, -1] - buckets[:, 0]
    decay1    = buckets[:, 1] / buckets[:, 0]
    decay2    = buckets[:, 2] / buckets[:, 1]
    convexity = buckets[:, 2] + buckets[:, 5] - 2 * buckets[:, 3]

    pca       = PCA(n_components=2).fit(buckets)
    pcs       = pca.transform(buckets)

    df["level"]     = level
    df["slope"]     = slope
    df["decay1"]    = decay1
    df["decay2"]    = decay2
    df["convexity"] = convexity
    df["pca1"]      = pcs[:, 0]
    df["pca2"]      = pcs[:, 1]
    return df

def build_feature_store() -> None:
    df_wide = pivot_wide(load_curve_long())
    df_wide = attach_price(df_wide)
    df_feat = compute_curve_factors(df_wide)

    df_feat.to_parquet(DST_FEATURE, engine="fastparquet")
    print(f"✅  feature_store written → {DST_FEATURE}  "
          f"({len(df_feat):,} rows, {len(df_feat.columns)} columns)")

if __name__ == "__main__":
    build_feature_store()
