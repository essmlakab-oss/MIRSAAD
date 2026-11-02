# -*- coding: utf-8 -*-
"""One-time training: SUPERVISED attack-TYPE head for Model 2.

The owner decided Model 2 must classify the attack family. The shipped
artifact has no such head, so this script trains one (LightGBM multiclass,
6 classes) on the labelled attacked rows of the challenge's synthetic seeds
(AI_Realistic_Attacks_v2, the SAME corpus the binary stack was built on),
featurised by the EXACT production pipeline:

    v3.build(detector_input) --with v3.DEPLOY(twins)-->  D  (features rows)
    v3.predict(D, bundle)     -> D.proba / D.alerte      (severity features)

Per attacked row the head consumes every numeric D column (twin residuals,
tu5_z, ERA5 gaps, proba, ...). The trained head is stored as a `type_head`
key INSIDE the deploy bundle:

    type_head = {"model": Booster, "features": [...], "classes": [...],
                 "metrics": {macro_f1, accuracy, confusion}, "trained_on": ...}

The frozen `detecteur_v3.py` is only ever called, never modified.
Result bundle is written to models/detecteur_v3_deploy.joblib (+ pushed to HF
with a separate push script / huggingface_hub upload).

Run:  .venv/Scripts/python.exe _train_type_head.py   (from backend/)
Env:  DEPLOY_SEEDS=1,2,... subset;  EVAL_SEEDS seeds held out for metrics.
"""
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "app" / "fdi"))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

import lightgbm as lgb  # noqa: E402
import detecteur_v3 as v3  # noqa: E402
from app.fdi import config  # noqa: E402

SEED_ROOT = Path(
    os.getenv("SEED_ROOT", "Data/AI_Realistic_Attacks_v2")
)
SEEDS = [int(s) for s in os.environ.get("DEPLOY_SEEDS", "").split(",") if s] or list(range(1, 17))
OUT = BACKEND / "models" / "detecteur_v3_deploy.joblib"
TMP = BACKEND / "data" / "_type_head_tmp"

# DEPLOY twins sorted as in classify; the seeds' detector_input covers these.
ALL_DEPLOY_STATIONS = list(v3.STATIONS)

# D columns that are labels / bookkeeping rather than sensor features —
# never fed to the head. `proba` IS a feature (attack severity); datetime
# and object columns are excluded by dtype automatically.
_LABEL_COLS = {"label", "attack_type", "attack_id", "alerte", "clean"}


def _numeric_features(D: pd.DataFrame) -> list[str]:
    return [
        c
        for c in D.columns
        if c not in _LABEL_COLS and D[c].dtype.kind in "if" and c != "proba"
    ]


def main() -> None:
    t0 = time.time()

    # ---- 0. deploy bundle (base + frozen twins) straight from HF
    from huggingface_hub import hf_hub_download

    bundle_path = hf_hub_download(
        repo_id=config.HF_REPO_ID,
        filename=config.FDI_V3_FILENAME,
        token=config.resolve_hf_token(),
    )
    bundle = joblib.load(bundle_path)
    twins = bundle.get("twins") or {}
    assert twins, "deploy bundle on the Hub carries no twins — cannot featurise predict-only"
    bundle_stations = set(twins)
    model = bundle  # v3.predict wants M1/M2/F1/F2/thr — the bundle has them all

    # ---- 1. featurise the seeds' attacked rows with the EXACT pipeline
    rows: list[pd.DataFrame] = []
    feature_names: set[str] = set()
    per_seed = {}
    for s in SEEDS:
        base = SEED_ROOT / f"seed{s}"
        di = base / "detector_input.parquet"
        rl = base / "row_labels.parquet"
        if not di.is_file() or not rl.is_file():
            print(f"seed {s}: missing input — skipped", flush=True)
            continue
        di_df = pd.read_parquet(di)
        di_df["time"] = pd.to_datetime(di_df.time).dt.tz_localize(None)
        stations_here = sorted(set(di_df.station) & bundle_stations)
        if not stations_here:
            print(f"seed {s}: no station covered by the deploy twins — skipped", flush=True)
            continue

        TMP.mkdir(parents=True, exist_ok=True)
        di_df.to_parquet(TMP / "detector_input.parquet", index=False)
        pd.read_parquet(rl).to_parquet(TMP / "row_labels.parquet", index=False)

        with _deploy(twins, ALL_DEPLOY_STATIONS):
            D = v3.build(str(TMP))
        D = v3.predict(D, model)

        # build() merges row_labels itself: D.label = is_attacked int,
        # D.attack_type = family (None on clean hours).
        atk = D[D.label.fillna(0).astype(int) == 1].copy()
        atk = atk[atk.attack_type.notna()]
        if atk.empty:
            print(f"seed {s}: no labelled attack rows — skipped", flush=True)
            continue
        cols = _numeric_features(D)
        feature_names.update(cols)
        rows.append(atk[["station", "time", "attack_type", "proba"] + cols])
        per_seed[s] = len(atk)
        print(f"seed {s}: {len(atk)} attacked rows  ({time.time()-t0:.0f}s)", flush=True)

    data = pd.concat(rows, ignore_index=True)
    feats = sorted(feature_names)
    print(f"total attacked rows: {len(data)}  feature columns: {len(feats)}", flush=True)
    print("class counts:", data.attack_type.value_counts().to_dict(), flush=True)

    # ---- 2. train LightGBM multiclass, seeds split for an honest metric
    # Track which seed each row came from, hold the last present seed out.
    seed_of = []
    for s in SEEDS:
        seed_of.extend([s] * per_seed.get(s, 0))
    data["seed"] = seed_of
    eval_seed = max(per_seed)
    train = data[data.seed != eval_seed]
    test = data[data.seed == eval_seed]

    Xtr = train[feats].to_numpy(np.float32, na_value=np.nan)
    ytr = train.attack_type.astype(str).to_numpy()
    classes = sorted(set(ytr))
    model_head = lgb.LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=30,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        n_jobs=-1,
        verbose=-1,
        random_state=0,
    )
    model_head.fit(Xtr, ytr)

    # ---- 3. held-out metrics (seenstations, unseen seed = honest generalisation)
    def _score(frame: pd.DataFrame) -> dict:
        X = frame[feats].to_numpy(np.float32, na_value=np.nan)
        y = frame.attack_type.astype(str).to_numpy()
        pred = model_head.predict(X)
        acc = float((pred == y).mean())
        macro = {}
        for c in classes:
            tp = ((pred == c) & (y == c)).sum()
            fp = ((pred == c) & (y != c)).sum()
            fn = ((pred != c) & (y == c)).sum()
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            f1 = 0.0 if not (p or r) else 2 * p * r / (p + r)
            macro[c] = f1
        conf = {f"{t}|{p_}": int(((pred == t) & (y == p_)).sum()) for t in classes for p_ in classes}
        conf = {k: v for k, v in conf.items() if v}
        return {
            "accuracy": round(acc, 4),
            "macro_f1": round(float(np.mean(list(macro.values()))), 4),
            "per_class_f1": {c: round(v, 3) for c, v in macro.items()},
            "confusion_nonzero": conf,
            "test_rows": int(len(frame)),
        }

    train_metrics = _score(train)
    test_metrics = _score(test) if len(test) else {}
    print("TRAIN split:", json.dumps(train_metrics, indent=1), flush=True)
    print("HELD-OUT seed:", json.dumps(test_metrics, indent=1), flush=True)

    # ---- 4. store head INSIDE the deploy bundle
    head = {
        "model": model_head,
        "features": feats,
        "classes": classes,
        "metrics": {"train": train_metrics, "heldout": test_metrics},
        "trained_on": f"AI_Realistic_Attacks_v2 seeds {sorted(per_seed)}",
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    payload = dict(bundle)
    payload["type_head"] = head
    OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, OUT, compress=3)
    print(
        f"saved {OUT} ({math.ceil(os.path.getsize(OUT)/1e6)} MB) in {time.time()-t0:.0f}s — upload to HF",
        flush=True,
    )
    shutil.rmtree(TMP, ignore_errors=True)


class _deploy:
    """v3.DEPLOY + STATIONS guard identical to attack_classifier's usage."""

    def __init__(self, twins: dict, stations: list[str]):
        self.twins, self.stations, self._st = twins, stations, None

    def __enter__(self):
        self._st = list(v3.STATIONS)
        v3.STATIONS = self.stations
        v3.DEPLOY = self.twins
        return self

    def __exit__(self, *a):
        v3.DEPLOY = None
        v3.STATIONS = self._st
        return False


if __name__ == "__main__":
    main()
