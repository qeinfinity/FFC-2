# funding_curve/feature_extractor.py
"""
Derive explanatory factors from an 8-bucket funding curve snapshot.

Input  : tidy DataFrame with columns b_0 … b_56  (annualised)  at ts_snap
Output : single-row DataFrame with
         level, slope, decay1, decay2, convexity, pca1, pca2
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA


def _pca_components(buckets: np.ndarray) -> tuple[float, float]:
    """Return first two PCA scores of an 8-bucket vector."""
    pca = PCA(n_components=2)
    comps = pca.fit_transform(buckets.reshape(1, -1))
    return comps[0, 0], comps[0, 1]


def extract_features(curve_row: pd.Series) -> pd.Series:
    """
    curve_row: Series with index  ['b_0','b_8', … ,'b_56']  (annualised decimal)

    Returns: Series with engineered factors.
    """
    b = curve_row.filter(like="b_").values.astype(float)  # length-8
    level      = b[0]
    slope      = b[-1] - b[0]
    decay1     = b[1] / b[0] if b[0] else np.nan          # 8-→16 h
    decay2     = b[2] / b[1] if b[1] else np.nan          # 16-→24 h
    convexity  = b[2] + b[5] - 2 * b[3]                   # simple second-deriv
    pca1, pca2 = _pca_components(b)

    return pd.Series(
        dict(level=level, slope=slope,
             decay1=decay1, decay2=decay2,
             convexity=convexity, pca1=pca1, pca2=pca2)
    )
