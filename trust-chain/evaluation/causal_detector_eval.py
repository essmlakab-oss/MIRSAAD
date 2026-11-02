"""Causal detectors on the signal-processing features, scored blind on the 2025 test year.

Protocol (no test information is used to build or tune anything):
  * features: signal-processing outputs, with or without the ERA5 columns
    (causal: a value at t uses only rows <= t; parameters fitted on train 2022-2023, clean)
  * labels for tuning: validation 2024 only. ``build_detector`` receives validation rows and
    nothing else, and only validation rows of the truth file are loaded for it
  * test 2025 labels (evaluator-only ground_truth.parquet) are read once, after the detector
    is frozen and saved

Detector R  "rules":  max of normalised features vs one threshold.
Detector M  "model":  gradient boosting on the sp_* features + station id,
                      trained on validation 2024; threshold chosen out-of-fold.
Thresholds: maximise F1 on validation subject to a false-alarm rate <= the cap.

Usage:
  causal_detector_eval.py evaluate --features <parquet> [<parquet> ...] --truth <ground_truth.parquet>
      --params <frozen_params.json> --out <dir> --model-out <joblib> [--fa-cap 0.01]
  causal_detector_eval.py score --features <parquet> [<parquet> ...] --model <joblib>
      --sums <models.sha256> --out <dir> --mode <name> --attack-set-id <id> --decision-delay-h <h>

The ``evaluate`` sub-command name may be omitted.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # model_pinning.py, at the trust-chain root
from model_pinning import load_pinned

ABS_FEATURES = ["sp_innov_z_5", "sp_innov_z_10", "sp_depth_gap_z"]
TRUTH_COLUMNS = ["station", "time", "partition", "is_attacked", "attack_type", "attack_id", "is_evaluable"]
FORBIDDEN_FEATURE_COLUMNS = {"is_attacked", "attack_type", "attack_id", "is_evaluable", "clean_target"}
REPLAY_WEIGHT = 10.0  # exact repeat = strong evidence
FOLDS = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
MODEL_FORMAT = "1.0"


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metrics(y, alarm):
    y, alarm = np.asarray(y, bool), np.asarray(alarm, bool)
    tp = int((y & alarm).sum())
    fp = int((~y & alarm).sum())
    fn = int((y & ~alarm).sum())
    tn = int((~y & ~alarm).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
        "false_alarm_rate": round(fp / (fp + tn), 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def pick_threshold(y, score, fa_cap):
    y = np.asarray(y, bool)
    neg = np.sort(score[~y])
    best = (None, -1.0)
    for q in np.linspace(0.9, 0.9999, 400):
        t = neg[min(int(q * len(neg)), len(neg) - 1)]
        m = metrics(y, score > t)
        if m["false_alarm_rate"] <= fa_cap and m["f1"] > best[1]:
            best = (float(t), m["f1"])
    if best[0] is None:
        raise ValueError(f"no threshold satisfies the false-alarm cap {fa_cap}")
    return best[0]


def per_family(df, alarm_col):
    att = df[df["is_attacked"]]
    hour = att.groupby("attack_type")[alarm_col].mean().round(3).to_dict()
    ev = att.groupby(["attack_type", "attack_id"])[alarm_col].any().groupby("attack_type").mean().round(3).to_dict()
    n_ev = att.groupby("attack_type")["attack_id"].nunique().to_dict()
    return {k: {"hourly_recall": hour[k], "events_detected": ev[k], "n_events": int(n_ev[k])} for k in hour}


def load_features(paths):
    """Read one or more feature files and join them on (station, time); first file wins a name."""
    merged = None
    for path in paths:
        frame = pd.read_parquet(path)
        leaked = FORBIDDEN_FEATURE_COLUMNS & set(frame.columns)
        if leaked:
            raise ValueError(f"{Path(path).name} holds label columns: {sorted(leaked)}")
        if merged is None:
            merged = frame
            continue
        new = [c for c in frame.columns if c not in merged.columns]
        merged = merged.merge(frame[["station", "time", *new]], on=["station", "time"], validate="1:1")
    return merged


def measured_year(time_column):
    """Timestamps mark the end of the measured hour, so 2026-01-01 00:00 belongs to 2025."""
    return (time_column - pd.Timedelta(hours=1)).dt.year


def read_truth(path, partition):
    """Load truth rows of ONE partition. Rows of other partitions are never materialised."""
    table = pq.read_table(path, columns=TRUTH_COLUMNS, filters=[("partition", "=", partition)])
    return table.to_pandas()


def rule_scores(df, detector):
    parts = {c: df[c].abs() / detector["rule_scales"][c] for c in ABS_FEATURES}
    for d in ("5", "10"):
        parts[f"sp_cusum_{d}"] = df[f"sp_cusum_{d}"] / df["station"].map(detector["cusum_h"][d])
    parts["sp_replay_match"] = df["sp_replay_match"].astype(float) * REPLAY_WEIGHT
    return pd.DataFrame(parts).fillna(0.0)


def design_matrix(df, detector):
    X = df[detector["feature_columns"]].copy()
    X["station_code"] = df["station"].map(detector["station_codes"]).astype("int8")
    return X


def make_model(X):
    return HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        max_leaf_nodes=31,
        l2_regularization=1.0,
        class_weight="balanced",
        categorical_features=[X.columns.get_loc("station_code")],
        random_state=0,
    )


def build_detector(val, params, fa_cap):
    """Fit everything from VALIDATION rows (features + labels) and the train-fitted params.

    This function cannot see 2025: its only data argument is the validation frame.
    """
    years = measured_year(val["time"]).unique().tolist()
    if years != [2024]:
        raise ValueError(f"build_detector received measured years {years}, expected only 2024")
    sp = [c for c in val.columns if c.startswith("sp_")]
    negatives = val[~val["is_attacked"]]
    detector = {
        "format": MODEL_FORMAT,
        "feature_columns": sp,
        "station_codes": {s: i for i, s in enumerate(sorted(val["station"].unique()))},
        "rule_scales": {c: float(negatives[c].abs().quantile(0.995)) for c in ABS_FEATURES},
        "cusum_h": {
            d: {st: v["depths"][d].get("cusum_h", np.nan) for st, v in params["stations"].items()} for d in ("5", "10")
        },
        "fa_cap": fa_cap,
    }
    score_r = rule_scores(val, detector).max(axis=1)
    X = design_matrix(val, detector)
    y = val["is_attacked"].values
    months = val["time"].dt.month.values
    oof = np.zeros(len(val))
    Xv = X.values
    for fold in FOLDS:
        held = np.isin(months, fold)
        m = make_model(X).fit(Xv[~held], y[~held])
        oof[held] = m.predict_proba(Xv[held])[:, 1]
    detector["model"] = make_model(X).fit(Xv, y)
    detector["thresholds"] = {"R": pick_threshold(y, score_r.values, fa_cap), "M": pick_threshold(y, oof, fa_cap)}
    return detector, oof, score_r.values


def rule_thresholds(detector):
    """Absolute feature thresholds equivalent to the rule detector's validation threshold."""
    thr = detector["thresholds"]["R"]
    return {c: detector["rule_scales"][c] * thr for c in ABS_FEATURES}


def apply_detector(df, detector):
    out = df[["station", "time"]].copy()
    norm = rule_scores(df, detector)
    out["score_R"] = norm.max(axis=1).values
    out["why_R"] = norm.idxmax(axis=1).values
    out["score_M"] = detector["model"].predict_proba(design_matrix(df, detector).values)[:, 1]
    return out


def scored_results(d, part_scores=None):
    r = {"rows": len(d), "attacked_rows": int(d["is_attacked"].sum())}
    for k in ("R", "M", "RorM"):
        r[k] = metrics(d["is_attacked"], d[f"alarm_{k}"])
        r[k]["per_family"] = per_family(d, f"alarm_{k}")
    r["M"]["pr_auc"] = round(average_precision_score(d["is_attacked"], d["score_M"]), 3)
    r["R"]["pr_auc"] = round(average_precision_score(d["is_attacked"], d["score_R"]), 3)
    return r


def with_alarms(d, thresholds):
    d = d.copy()
    for k, t in thresholds.items():
        d[f"alarm_{k}"] = d[f"score_{k}"] > t
    d["alarm_RorM"] = d["alarm_R"] | d["alarm_M"]
    return d


def evaluate(a):
    feats = load_features(a.features)
    params = json.loads(Path(a.params).read_text(encoding="utf-8"))

    # --- Tuning: validation rows only -------------------------------------------------
    val_truth = read_truth(a.truth, "validation")
    val = feats.merge(val_truth, on=["station", "time"], how="inner", validate="1:1")
    val = val[val["is_evaluable"]].reset_index(drop=True)
    detector, oof, score_r_val = build_detector(val, params, a.fa_cap)
    thr = detector["thresholds"]

    # --- Freeze: the detector is on disk before any test label is loaded --------------
    model_out = Path(a.model_out)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(detector, model_out, compress=3)
    model_sha = sha256_file(model_out)

    val_scored = val[["station", "time", "partition", "is_attacked", "attack_type", "attack_id"]].copy()
    val_scored["score_R"], val_scored["score_M"] = score_r_val, oof  # out-of-fold, never in-sample
    val_scored = with_alarms(val_scored, thr)

    # --- Scores on 2025 use frozen detector and no labels -----------------------------
    test_rows = feats[measured_year(feats["time"]) == 2025].reset_index(drop=True)
    t0 = time.perf_counter()
    test_scores = apply_detector(test_rows, detector)
    lat_us = 1e6 * (time.perf_counter() - t0) / max(len(test_rows), 1)

    # --- Test labels are read once, here ----------------------------------------------
    test_truth = read_truth(a.truth, "test")
    test = test_scores.merge(test_truth, on=["station", "time"], how="inner", validate="1:1")
    test = with_alarms(test[test["is_evaluable"]], thr)

    res = {
        "protocol": {
            "features_fit": "train 2022-2023 (clean)",
            "tuning": "validation 2024 labels",
            "scored": "test 2025, evaluator ground truth, read once",
            "fa_cap": a.fa_cap,
            "causal": True,
        },
        "feature_files": [{"file": Path(p).name, "sha256": sha256_file(p)} for p in a.features],
        "feature_columns": detector["feature_columns"],
        "model": {"file": model_out.name, "sha256": model_sha, "format": MODEL_FORMAT},
        "thresholds": thr,
        "rule_thresholds": rule_thresholds(detector),
        "results": {},
    }
    res["results"]["validation"] = scored_results(val_scored)
    res["results"]["test"] = scored_results(test)
    res["inference_us_per_row"] = round(lat_us, 2)
    res["rules_alarm_reason_on_test_alarms"] = test[test["alarm_R"]]["why_R"].value_counts().to_dict()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "causal_detector_results.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("thresholds", "model")}, indent=2))
    for name in ("R", "M"):
        m = res["results"]["test"][name]
        print(
            f"test {name}: F1 {m['f1']} P {m['precision']} R {m['recall']} "
            f"FA {m['false_alarm_rate']} PR-AUC {m['pr_auc']}"
        )


def verify_pinned(model_path, sums_path):
    """Refuse a model (a pickle) unless its SHA-256 matches the sums file, before loading it."""
    model_path = Path(model_path)
    expected = None
    for line in Path(sums_path).read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if name.strip() == model_path.name:
            expected = digest.strip()
    actual = sha256_file(model_path)
    if expected != actual:
        raise ValueError(f"{model_path.name}: sha256 {actual} does not match pinned {expected}")
    return actual


def reasons(scores, threshold):
    """Plain reason per row, naming the strongest normalised rule feature. Uses no labels."""
    alarm = scores["score_M"] > threshold
    top = scores["why_R"].astype(str) + " at " + scores["score_R"].round(2).astype(str) + " x scale"
    return np.where(alarm, "model_alarm; strongest " + top, "below_threshold; strongest " + top)


def score(a):
    model_sha = verify_pinned(a.model, a.sums)
    detector = load_pinned(a.model, a.sums, by_name=True)
    feats = load_features(a.features)
    rows = feats[measured_year(feats["time"]) == 2025].reset_index(drop=True)
    missing = [c for c in detector["feature_columns"] if c not in rows.columns]
    if missing:
        raise ValueError(f"features lack model inputs: {missing}")
    scores = apply_detector(rows, detector)
    threshold = detector["thresholds"]["M"]
    output = pd.DataFrame(
        {
            "station": scores["station"],
            "time": scores["time"],
            "ai_score": scores["score_M"],
            "ai_reason": reasons(scores, threshold),
        }
    )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "ai_scores.parquet"
    table = pa.Table.from_pandas(output, preserve_index=False).replace_schema_metadata(None)
    pq.write_table(table, path, compression="zstd", compression_level=3, row_group_size=65_536)
    manifest = {
        "file": path.name,
        "sha256": sha256_file(path),
        "threshold": threshold,
        "model_version": f"{Path(a.model).stem}-{detector['format']}",
        "model_sha256": model_sha,
        "attack_set_id": a.attack_set_id,
        "decision_delay_h": a.decision_delay_h,
        "mode": a.mode,
        "rows": len(output),
        "scope": "measured year 2025 (test)",
        "rule_thresholds": rule_thresholds(detector),
        "feature_files": [{"file": Path(p).name, "sha256": sha256_file(p)} for p in a.features],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{a.mode}: wrote {len(output)} scores, sha256 {manifest['sha256']}, delay {a.decision_delay_h} h")


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--features", required=True, nargs="+")
    ev.add_argument("--truth", required=True)
    ev.add_argument("--params", required=True)
    ev.add_argument("--out", required=True)
    ev.add_argument("--model-out", required=True)
    ev.add_argument("--fa-cap", type=float, default=0.01)
    sc = sub.add_parser("score")
    sc.add_argument("--features", required=True, nargs="+")
    sc.add_argument("--model", required=True)
    sc.add_argument("--sums", required=True)
    sc.add_argument("--out", required=True)
    sc.add_argument("--mode", required=True)
    sc.add_argument("--attack-set-id", required=True)
    sc.add_argument("--decision-delay-h", required=True, type=int)
    if argv and argv[0].startswith("--") and argv[0] not in ("--help",):
        argv = ["evaluate", *argv]
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    (evaluate if a.command == "evaluate" else score)(a)


if __name__ == "__main__":
    main()
