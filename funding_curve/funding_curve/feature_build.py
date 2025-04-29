# funding_curve/feature_build.py  (vectorised, PCA fitted once)
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

SRC_HISTORY = Path("storage/processed/curve_history.parquet")
SRC_LIVE    = Path("storage/processed/curve_live.parquet")
DST_FEATURE = Path("storage/processed/feature_store.parquet")

# --------------------------------------------------------------------- #
def load_curve_long() -> pd.DataFrame:
    df = pd.read_parquet(SRC_HISTORY, engine="fastparquet")
    if SRC_LIVE.exists():
        df = pd.concat(
            [df,
             pd.read_parquet(SRC_LIVE, engine="fastparquet")],
            ignore_index=True,
        )
    return df

def pivot_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    wide = (
        df_long.pivot_table(index="ts_snap",
                            columns="bucket_start_h",
                            values="fwd_rate_ann")
             .rename(columns=lambda h: f"b_{int(h)}")
             .sort_index()
    )
    # keep only rows with all 8 buckets present
    return wide.dropna()

def build_feature_store() -> None:
    df_wide = pivot_wide(load_curve_long())

    # numpy view: rows × 8 buckets
    buckets = df_wide[[f"b_{h}" for h in range(0, 64, 8)]].to_numpy()

    # -------- simple factors ------------------------------------------
    level     = buckets[:, 0]
    slope     = buckets[:, -1] - buckets[:, 0]
    decay1    = buckets[:, 1] / buckets[:, 0]
    decay2    = buckets[:, 2] / buckets[:, 1]
    convexity = buckets[:, 2] + buckets[:, 5] - 2 * buckets[:, 3]

    # -------- PCA (fit once) ------------------------------------------
    pca = PCA(n_components=2).fit(buckets)
    pcs = pca.transform(buckets)          # shape (n_samples, 2)

    # -------- assemble feature DataFrame ------------------------------
    feat = pd.DataFrame({
        "level":     level,
        "slope":     slope,
        "decay1":    decay1,
        "decay2":    decay2,
        "convexity": convexity,
        "pca1":      pcs[:, 0],
        "pca2":      pcs[:, 1],
    }, index=df_wide.index)

    feat.to_parquet(DST_FEATURE, engine="fastparquet")
    print(f"✅  feature_store written → {DST_FEATURE}  ({len(feat):,} rows)")

# --------------------------------------------------------------------- #
if __name__ == "__main__":
    build_feature_store()
