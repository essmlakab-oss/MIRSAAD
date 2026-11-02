# -*- coding: utf-8 -*-
"""One-time training script: freeze the deployment twins + calibration scalars.

Produces `models/detecteur_v3_deploy.joblib` = the ORIGINAL artifact dict
(M1/M2/F1/F2/thr/results_2025/trained_on untouched) + a `twins` bundle:
    {station: {"by_year": {reskey: LGBM}, "by_month": {reskey: Huber},
               "rain": {"RX": [...], "m": LGBMClassifier},
               "incr": {(tgt, k): {"cols": [...], "m": LGBM}},
               "sc": {reskey: (sd, med)}, "mad": float,
               "inc_sd": {(tgt, k): sd}}}

The twins are fitted EXACTLY as `detecteur_v3.build()` does in capture mode,
deployment fold = known & lab==0 (2022-2024 normal hours). The training corpus
is the deduplicated union of the clean 2022-2024 rows of seeds 1..16 (the same
seeds the shipped classifiers were trained on); per station every (time) appears
once, carrying its values from the seed in which it was clean most often.
Run:  .venv/Scripts/python.exe _train_deploy_v3.py            (from backend/)
"""
import os, sys, time, math
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, str(Path(__file__).resolve().parent / "app" / "fdi"))
import detecteur_v3 as v3

SEED_ROOT = Path(os.getenv("SEED_ROOT", "Data/AI_Realistic_Attacks_v2"))
SEEDS = [int(s) for s in os.environ.get("DEPLOY_SEEDS", "").split(",") if s] or list(range(1, 17))
OUT = Path(os.environ.get("DEPLOY_OUT", str(Path(__file__).resolve().parent / "models" / "detecteur_v3_deploy.joblib")))

# LightGBM: give the one-time fit all threads (num_threads does not change the
# model, only the wall clock; infer-time predictions are single-chunk anyway).
import lightgbm as lgb
def patched_reg(obj="l2", n=300):
    return lgb.LGBMRegressor(n_estimators=n, learning_rate=0.05, num_leaves=31, min_child_samples=50,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                             objective=obj, n_jobs=-1, verbose=-1, random_state=0)
def patched_clf(n=200):
    return lgb.LGBMClassifier(n_estimators=n, learning_rate=0.05, num_leaves=31, min_child_samples=50,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              n_jobs=-1, verbose=-1, random_state=0)
v3.reg = patched_reg
v3.clf = patched_clf


def build_corpus():
    """Corpus is built inline in __main__ so we can stream per-seed progress."""
    return None


if __name__ == "__main__":
    t0 = time.time()
    # ---- 1. corpus: union of clean 2022-2024 rows, dedup by (station, time)
    per_seed = []
    for s in SEEDS:
        base = SEED_ROOT / f"seed{s}"
        di = pd.read_parquet(base / "detector_input.parquet")
        rl = pd.read_parquet(base / "row_labels.parquet")
        di["time"] = pd.to_datetime(di.time).dt.tz_localize(None)
        di = di.merge(rl[["station", "time", "is_attacked"]], on=["station", "time"], how="left")
        di = di[(di.time.dt.year.isin(v3.TRAIN_YEARS)) & ~(di.is_attacked.fillna(False).astype(bool))]
        per_seed.append(di)
        print(f"seed {s}: {len(di)} clean historical rows", flush=True)
    allc = pd.concat(per_seed, ignore_index=True)
    # a (station, time) enters the corpus only if a majority of seeds had it clean;
    # the row VALUE kept is the first seed (lowest number) in which it was clean.
    majority = allc.groupby(["station", "time"])["is_attacked"].apply(lambda x: x.mean() < 0.5)
    allc = allc.merge(majority.rename("majority_clean").reset_index(), on=["station", "time"], how="inner")
    corpus = allc[allc.pop("majority_clean") == True].drop_duplicates(["station", "time"], keep="first")  # noqa: E712
    print("corpus rows:", len(corpus), "per station:", corpus.station.value_counts().to_dict(), flush=True)

    tmp = Path("data/_deploy_corpus_tmp"); tmp.mkdir(parents=True, exist_ok=True)
    di_cols = corpus.drop(columns=["is_attacked", "clean"], errors="ignore")   # build() merges labels itself
    di_cols.to_parquet(tmp / "detector_input.parquet", index=False)
    rl = corpus[["station", "time"]].copy()
    rl["is_attacked"] = False; rl["attack_type"] = None; rl["attack_id"] = None
    rl.to_parquet(tmp / "row_labels.parquet", index=False)

    # ---- 2. capture build (fit the 17 deployment twins per station + scalars)
    want = os.environ.get("DEPLOY_STATIONS", "").split(",")
    stations = [st for st in v3.STATIONS if st in set(corpus.station) and (not want[0] or st in want)]
    v3.STATIONS = stations
    v3.CAPTURE = {st: {} for st in stations}
    D = v3.build(str(tmp))
    twins = v3.CAPTURE
    v3.STATIONS = ["AZ_Tucson_11_W", "OK_Goodwell_2_SE", "MS_Newton_5_ENE", "NY_Ithaca_13_E", "CA_Fallbrook_5_NE"]
    v3.CAPTURE = None

    for st, b in twins.items():
        assert "by_year" in b and "by_month" in b and "rain" in b and "incr" in b and "sc" in b and "mad" in b, st
        assert len(b["by_year"]) == 5 and len(b["by_month"]) == 3 and len(b["incr"]) == 8, st
    print("twins captured OK:", {st: sorted(b.keys()) for st, b in twins.items()}, flush=True)

    # ---- 3. artifact = original dict (untouched) + twins
    orig = joblib.load(Path(__file__).resolve().parent / "models" / "detecteur_v3.joblib")
    payload = dict(orig); payload["twins"] = twins
    joblib.dump(payload, OUT, compress=3)
    print(f"saved {OUT} ({math.ceil(os.path.getsize(OUT)/1e6)} MB) in {time.time()-t0:.0f}s", flush=True)
