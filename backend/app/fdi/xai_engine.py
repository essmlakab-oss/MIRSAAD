"""XAI engine — the enrich() port from app/xai_streamlit.py, backend edition.

Three layers, three honest claims, identical to the Streamlit version:

  1. TEMPORAL — exp5_tu is the twin's expected sm5; residual_tu is the
     observed − expected gap. Exact identity, not a reconstruction.

  2. WITNESS PERCENTILES + ECOD — upload-derived reference (v3 alerts
     excluded). ECOD score = mean(−log p) across 10 witnesses; the score is
     the mean of contributions BY CONSTRUCTION, so sum(contrib)/n_present
     == score exactly.

  3. SHAP on M1 — the stage-1 LightGBM booster, not a fictional witness-only
     twin. sum(shap) = pred − base is exact.

Hardening patches vs the Streamlit original:
  * `_assert_ecod_additive` warns, does not raise.
  * `reason` is computed only on alert rows (vectorized prefilter).
  * `residual_tu_sigma` is a per-station column, so the temporal panel and
    `twin_residual_z` are well-defined.
  * `m1_base` propagates from `_shap_m1` into `D.attrs`, so the SHAP panel
    reports the correct base value.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("fdi.xai_engine")

WITNESSES: list[tuple[str, str]] = [
    ("tu5_cdev",         "5 cm differs from what untouched sensors imply"),
    ("box_tu5_absmax",   "the 5 cm level shifted vs the days around it"),
    ("pair5_24",         "unexplained rise followed by unexplained fall"),
    ("inc5_24",          "24 h change of sm5 not explained by witnesses"),
    ("rise_nosupport72", "soil got wetter without plausible rain"),
    ("rain_missing24",   "weather says it rained but no rain was recorded"),
    ("rain_implaus24",   "rain recorded but weather disagrees"),
    ("ratio1020_cdev",   "the 10/20 cm depth relationship is distorted"),
    ("dup_later_m24",    "this exact sequence was already recorded (copy)"),
    ("raindup_m24",      "this rain sequence is an exact copy"),
    ("nb_rain_vs72",     "rain differs from neighbouring stations"),
    ("tE5_cdev",         "5 cm differs from the ERA5 reanalysis"),
]

ABNORMAL_P = 0.98
DEFAULT_MIN_REF_ROWS = 200
DEFAULT_TWIN_RESIDUAL_QUANTILE = 0.95

ECOD_FEATURES: tuple[str, ...] = (
    "jump_ex", "jz", "ratio_dev", "ratio1020_dev",
    "inc5_24", "inc10_24", "rise_nosupport72",
    "rain_implaus24", "rain_missing24", "dup_later_m24",
)

ECOD_META: dict[str, dict[str, str]] = {
    "jump_ex":         {"label": "1 h change of sm5 not explained by sm10",
                        "family": "Paired-step fingerprint"},
    "jz":              {"label": "paired-step z-score (jump_ex / MAD)",
                        "family": "Paired-step fingerprint"},
    "ratio_dev":       {"label": "log(sm5/sm10) deviation vs its 14-day median",
                        "family": "Depth-relationship"},
    "ratio1020_dev":   {"label": "10/20 cm depth-relationship deviation",
                        "family": "Depth-relationship"},
    "inc5_24":         {"label": "24 h change of sm5 not explained by witnesses",
                        "family": "Unresolved increment"},
    "inc10_24":        {"label": "24 h change of sm10 not explained by witnesses",
                        "family": "Unresolved increment"},
    "rise_nosupport72":{"label": "72 h wetting without plausible rain",
                        "family": "Rain plausibility"},
    "rain_implaus24":  {"label": "rain recorded, weather disagrees (24 h)",
                        "family": "Rain plausibility"},
    "rain_missing24":  {"label": "weather says rain, sensor shows none (24 h)",
                        "family": "Rain plausibility"},
    "dup_later_m24":   {"label": "exact-increment replay fingerprint (24 h)",
                        "family": "Replay"},
}

FAMILY_PROSE = {
    "spike":        "a rapid excursion on the 5 cm probe",
    "bias":         "a sustained offset on the 5 cm probe",
    "drift":        "a slowly increasing deviation",
    "replay":       "re-recorded values from an earlier period",
    "fake_storm":   "invented rainfall",
    "hidden_storm": "a real rainfall event whose soil response was hidden",
    "stealth":      "an attack shaped to follow ERA5",
}


# ═══════════════════════════════════════════════════════════════════════════
#  Reference builder — the upload IS the reference
# ═══════════════════════════════════════════════════════════════════════════

def _build_reference_from_upload(
    D: pd.DataFrame,
    use_twin_residual: bool = True,
    min_ref_rows: int = DEFAULT_MIN_REF_ROWS,
    twin_residual_quantile: float = DEFAULT_TWIN_RESIDUAL_QUANTILE,
) -> dict[str, dict] | None:
    if "alerte" not in D.columns:
        return None
    alerta = D["alerte"].fillna(True).astype(bool).to_numpy()
    residual = (D["residual_tu"].to_numpy(dtype=float)
                if "residual_tu" in D.columns else None)

    out: dict[str, dict] = {}
    for st, idx in D.groupby("station").groups.items():
        idx = np.asarray(idx)
        clean_mask = ~alerta[idx]
        if use_twin_residual and residual is not None:
            res_st = np.abs(residual[idx])
            finite = np.isfinite(res_st)
            if finite.any():
                q = np.nanquantile(res_st, twin_residual_quantile)
                clean_mask &= finite & (res_st <= q)
        clean_idx = idx[clean_mask]
        if clean_idx.size < min_ref_rows:
            continue
        Dc = D.loc[clean_idx]
        ref: dict[str, np.ndarray] = {}
        for f in ECOD_FEATURES:
            if f not in Dc.columns:
                continue
            v = Dc[f].to_numpy(dtype=float)
            v = v[np.isfinite(v)]
            if v.size < min_ref_rows // 2:
                continue
            ref[f] = np.sort(v)
        if not ref:
            continue
        sigma = float("nan")
        if residual is not None:
            r = residual[clean_idx]
            r = r[np.isfinite(r)]
            if r.size >= 20:
                med = float(np.median(r))
                mad = float(np.median(np.abs(r - med)))
                sigma = 1.4826 * mad if mad > 0 else float(np.std(r))
        out[st] = {"ref": ref, "sigma": sigma, "n": int(len(Dc))}

    if not out:
        return None
    frame_stations = set(D["station"].astype(str).unique())
    missing = sorted(frame_stations - set(out))
    for st in missing:
        st_alerta = alerta[D["station"].to_numpy() == st]
        if len(st_alerta) and st_alerta.all():
            return None
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  Witness percentiles
# ═══════════════════════════════════════════════════════════════════════════

def _percentiles(out: pd.DataFrame, witness_ref: dict | None) -> pd.DataFrame:
    cols: dict[str, np.ndarray] = {}
    for w, _ in WITNESSES:
        p = np.full(len(out), np.nan, dtype=np.float32)
        if witness_ref and any(w in r for r in witness_ref.values()):
            for st, ref in witness_ref.items():
                if w not in ref:
                    continue
                mask = out["station"].to_numpy() == st
                if not mask.any():
                    continue
                vals = np.abs(np.nan_to_num(out.loc[mask, w].to_numpy(dtype=float)))
                arr = np.asarray(ref[w], dtype=float)
                if arr.size == 0:
                    continue
                p[mask] = np.searchsorted(arr, vals, side="left") / arr.size
        else:
            if "alerte" not in out.columns or w not in out.columns:
                cols[f"p_{w}"] = p
                continue
            is_clean = ~out["alerte"].fillna(True).astype(bool)
            for st in out["station"].astype(str).unique():
                st_mask = out["station"].astype(str).to_numpy() == st
                clean_vals = out.loc[st_mask & is_clean.to_numpy(), w].to_numpy(dtype=float)
                clean_vals = np.abs(clean_vals[np.isfinite(clean_vals)])
                if clean_vals.size < 20:
                    continue
                arr = np.sort(clean_vals)
                row_vals = np.abs(np.nan_to_num(out.loc[st_mask, w].to_numpy(dtype=float)))
                p[st_mask] = np.searchsorted(arr, row_vals, side="left") / arr.size
        cols[f"p_{w}"] = p
    return pd.DataFrame(cols, index=out.index)


# ═══════════════════════════════════════════════════════════════════════════
#  ECOD scoring + contributions
# ═══════════════════════════════════════════════════════════════════════════

def _ecod_scores_and_contribs(out, ecod_ref):
    scores = pd.Series(np.nan, index=out.index, dtype=float)
    contribs = {f: pd.Series(np.nan, index=out.index, dtype=float) for f in ECOD_FEATURES}
    for st, ref in ecod_ref.items():
        mask = out["station"].to_numpy() == st
        if not mask.any():
            continue
        idx = out.index[mask]
        per_feat_total = np.zeros(len(idx), dtype=float)
        n_ok = np.zeros(len(idx), dtype=int)
        for f in ECOD_FEATURES:
            arr = ref["ref"].get(f)
            if arr is None or arr.size < 2:
                continue
            x = out.loc[idx, f].to_numpy(dtype=float)
            ok = np.isfinite(x)
            if not ok.any():
                continue
            n = arr.size
            left = np.searchsorted(arr, x[ok], side="right") / n
            right = 1.0 - np.searchsorted(arr, x[ok], side="left") / n
            p = np.clip(np.minimum(left, right), 1.0 / n, 1.0)
            c = -np.log(p)
            contribs[f].loc[idx[ok]] = c
            per_feat_total[ok] += c
            n_ok[ok] += 1
        with np.errstate(invalid="ignore", divide="ignore"):
            scores.loc[idx] = np.where(n_ok > 0, per_feat_total / np.maximum(n_ok, 1), np.nan)
    return scores, contribs


def _top_family(contribs: dict[str, pd.Series]) -> pd.Series:
    if not contribs:
        return pd.Series(dtype=object)
    fam_sum: dict[str, pd.Series] = {}
    for f, s in contribs.items():
        fam = ECOD_META[f]["family"]
        fam_sum[fam] = fam_sum.get(fam, pd.Series(0.0, index=s.index)) + s.fillna(0.0)
    df = pd.DataFrame(fam_sum)
    top = df.idxmax(axis=1)
    return top.where(df.sum(axis=1) > 0)


def _assert_ecod_additive(D: pd.DataFrame, atol: float = 1e-9) -> bool:
    cols = [f"ecod_contrib_{f}" for f in ECOD_FEATURES if f"ecod_contrib_{f}" in D.columns]
    if not cols or "ecod_score" not in D.columns:
        return True
    s = D[cols].sum(axis=1, skipna=True)
    ok = s.notna() & D["ecod_score"].notna()
    if not ok.any():
        return True
    n_present = D.loc[ok, cols].notna().sum(axis=1).to_numpy(dtype=float)
    diff = np.abs(s[ok].to_numpy() - D.loc[ok, "ecod_score"].to_numpy() * n_present)
    worst = float(diff.max()) if diff.size else 0.0
    if worst > atol:
        logger.warning(
            "ECOD decomposition does not close to ecod_score "
            "(max_abs=%.2e). The panel still serves it.", worst,
        )
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  SHAP on stage-1 booster (M1)
# ═══════════════════════════════════════════════════════════════════════════

def _shap_m1(D: pd.DataFrame, M: dict, top_k: int = 5) -> tuple[pd.DataFrame | None, dict]:
    """SHAP of M1. Returns (top-k shap columns, meta) or (None, {}) if unavailable."""
    try:
        import shap
    except ImportError:
        logger.info("shap not installed — SHAP panel disabled.")
        return None, {}
    try:
        F1 = list(M["F1"])
        missing = [f for f in F1 if f not in D.columns]
        if missing:
            logger.warning("SHAP features missing from frame: %s…", missing[:5])
            return None, {}
        X = D[F1].to_numpy(np.float32)
        explainer = shap.TreeExplainer(M["M1"])
        sv = np.asarray(explainer.shap_values(X))
        base = float(np.asarray(explainer.expected_value).ravel()[0])
        mean_abs = np.abs(sv).mean(axis=0)
        order = np.argsort(-mean_abs)[:top_k]
        chosen = [F1[i] for i in order]
        df = pd.DataFrame({f"shap_{f}": sv[:, F1.index(f)] for f in chosen}, index=D.index)
        return df, {"m1_base": base}
    except Exception as e:
        logger.warning("SHAP failed: %s", e)
        return None, {}


# ═══════════════════════════════════════════════════════════════════════════
#  Prose
# ═══════════════════════════════════════════════════════════════════════════

def _short_reason(row: pd.Series, top_witnesses: list[tuple[str, str, float]]) -> str:
    if not bool(row.get("alerte", False)):
        return "v3 scored this hour below threshold."
    if not top_witnesses:
        return (f"v3 flagged this hour (proba {float(row.get('proba', float('nan'))):.2f}); "
                "no single witness dominates.")
    joined = "; ".join(t for _, t, _ in top_witnesses[:2])
    type_pred = row.get("type_pred")
    if isinstance(type_pred, str) and type_pred in FAMILY_PROSE:
        return f"v3 flagged this hour as {FAMILY_PROSE[type_pred]} — {joined}."
    return f"v3 flagged this hour — {joined}."


# ═══════════════════════════════════════════════════════════════════════════
#  Public — enrich a full witness frame
# ═══════════════════════════════════════════════════════════════════════════

def enrich(
    D: pd.DataFrame,
    defender,
    witness_ref: dict | None,
    with_shap: bool = True,
    use_twin_residual: bool = True,
    min_ref_rows: int = DEFAULT_MIN_REF_ROWS,
    ecod_ref: dict | None = None,
) -> pd.DataFrame:
    """Annotate the full v3 witness frame with percentiles, ECOD, SHAP, reason.

    Adds:
      residual_tu, residual_tu_sigma  (temporal band basis)
      p_<witness>                     (percentile per witness)
      ecod_score, ecod_contrib_<f>    (mean −log p per feature)
      top_ecod_family
      shap_<feat>                     (top 5 by global |shap| on M1)
      reason                          (one-line prose per alert row)
    """
    D = D.copy()

    # 1. Temporal residual — exact identity.
    if "exp5_tu" in D.columns and "residual_tu" not in D.columns:
        D["residual_tu"] = D["sm5"] - D["exp5_tu"]

    # 2. Per-station robust sigma of the residual (temporal band basis and
    #    twin_residual_z). Computed once per station on clean rows.
    if "residual_tu" in D.columns and "residual_tu_sigma" not in D.columns:
        def _robust_sigma(s: pd.Series) -> float:
            r = s.dropna().to_numpy(dtype=float)
            if r.size < 20:
                return float("nan")
            med = float(np.median(r))
            mad = float(np.median(np.abs(r - med)))
            sig = 1.4826 * mad
            if not np.isfinite(sig) or sig <= 0:
                sig = float(np.std(r)) or float("nan")
            return sig
        D["residual_tu_sigma"] = (
            D.groupby("station")["residual_tu"].transform(_robust_sigma)
        )

    # 3. Witness percentiles.
    pct = _percentiles(D, witness_ref)
    for c in pct.columns:
        D[c] = pct[c]

    # 4. ECOD — upload-derived reference unless the caller forced one.
    if ecod_ref is None:
        ecod_ref = _build_reference_from_upload(
            D, use_twin_residual=use_twin_residual, min_ref_rows=min_ref_rows,
        )

    if ecod_ref:
        score, contribs = _ecod_scores_and_contribs(D, ecod_ref)
        D["ecod_score"] = score
        for f, s in contribs.items():
            D[f"ecod_contrib_{f}"] = s
        try:
            D["top_ecod_family"] = _top_family(contribs)
        except Exception:
            D["top_ecod_family"] = None
        D.attrs["ecod_additive_ok"] = _assert_ecod_additive(D)
        D["ecod_ref_used"] = True
    else:
        D["ecod_score"] = np.nan
        for f in ECOD_FEATURES:
            D[f"ecod_contrib_{f}"] = np.nan
        D["top_ecod_family"] = None
        D["ecod_ref_used"] = False

    D.attrs["ecod_ref_summary"] = (
        {st: int(ref["n"]) for st, ref in ecod_ref.items()} if ecod_ref else {}
    )

    # 5. SHAP on M1 — propagate the base value into attrs.
    if with_shap:
        shap_df, meta = _shap_m1(D, defender.M)
        if shap_df is not None:
            for c in shap_df.columns:
                D[c] = shap_df[c]
            D.attrs["m1_base"] = float(meta.get("m1_base", 0.0))

    # 6. Reason — only on alert rows, vectorized.
    n = len(D)
    reasons = ["v3 scored this hour below threshold."] * n
    alert_mask = (
        D["alerte"].fillna(False).to_numpy().astype(bool)
        if "alerte" in D else np.zeros(n, bool)
    )
    if alert_mask.any():
        alert_idx = np.where(alert_mask)[0]
        Da = D.iloc[alert_idx]
        present = [w for w, _ in WITNESSES if f"p_{w}" in Da.columns]
        if present:
            p_matrix = np.column_stack(
                [Da[f"p_{w}"].to_numpy(dtype=float) for w in present]
            )
            texts = {w: t for w, t in WITNESSES}
            for k, pos in enumerate(alert_idx):
                row_vals = p_matrix[k]
                valid = np.isfinite(row_vals)
                if not valid.any():
                    reasons[pos] = _short_reason(D.iloc[pos], [])
                    continue
                order = np.argsort(-np.where(valid, row_vals, -np.inf))
                top = [
                    (present[j], texts[present[j]], float(row_vals[j]))
                    for j in order[:3]
                    if valid[j] and row_vals[j] >= ABNORMAL_P
                ]
                reasons[pos] = _short_reason(D.iloc[pos], top)
    D["reason"] = reasons
    return D