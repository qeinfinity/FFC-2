# %% Visualise funding-curve factors vs. next-day BTC return  ----------------
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns; sns.set_theme(style="ticks")

# ------------------------------------------------------------------ #
# 1.  Load feature store + attach returns
# ------------------------------------------------------------------ #
feat = pd.read_parquet("storage/processed/feature_store.parquet",
                       engine="fastparquet")

# If you already have btc_ret_1d in curve_full, merge it; else compute here
if "btc_ret_1d" not in feat.columns:
    # quick Yahoo close fetch for the same date span
    import yfinance as yf
    closes = (
        yf.download(
            "BTC-USD",
            start=feat.index.min().strftime("%Y-%m-%d"),
            end  =(feat.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
        )
        .loc[:, ["Close"]]                      # keep only the Close column
        .rename(columns={"Close": "btc_close"}) # rename properly with a dict
        .tz_localize("UTC")
    )

feat["btc_close"]  = closes.reindex(feat.index, method="ffill")
feat["btc_ret_1d"] = feat["btc_close"].pct_change().shift(-1)
feat = feat.dropna(subset=["btc_ret_1d"])

SAMPLE_N = 2_000
plot_df  = feat.sample(n=min(SAMPLE_N, len(feat)), random_state=42)

g = sns.pairplot(
    plot_df,
    vars=["level", "slope", "decay1", "convexity", "pca1", "pca2"],
    y_vars=["btc_ret_1d"],
    height=2.0, aspect=1,
    plot_kws=dict(alpha=0.35, s=10, edgecolor="none")
)
g.figure.suptitle("Funding-curve factors vs. next-day BTC return\n(sampled)",
               y=1.02)
plt.show()

# ── quick correlation heat-map (full dataset is fine) ──────────────────────
import matplotlib.pyplot as plt
corr = feat[["level","slope","decay1","decay2","convexity","pca1","pca2",
             "btc_ret_1d"]].corr()
plt.figure(figsize=(7,5))
sns.heatmap(corr, cmap="coolwarm", center=0, annot=True, fmt=".2f",
            cbar_kws=dict(label="Pearson r"))
plt.title("Correlation – curve factors & next-day return")
plt.show()