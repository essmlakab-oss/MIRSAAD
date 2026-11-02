"""AI Attack Arena engine (block 4): one attack with animation frames, batches, scoreboard, frontier, registry.

    from app.arena.engine import Arena
    A = Arena()
    frames = A.simulate_one({"family": "hidden_storm", "station": "NY_Ithaca_13_E", "start": "2025-10-20"})
    job = A.run_batch(A.generate(families=["bias", "drift"], amplitudes=[0.01, 0.03], durations=[48, 168]))
"""
import os, json, time, uuid, hashlib, threading, numpy as np, pandas as pd, joblib
from app.arena import detector_core as dc
from app.arena.simulator import inject, normalize, DEFAULTS, LIMITS, FAMILIES
from app.arena.defender import FastDefender
from app.arena.config import ASSETS

HERE = str(ASSETS)
RUNS = os.path.join(HERE, "runs"); os.makedirs(RUNS, exist_ok=True)
LOG = os.path.join(HERE, "runs", "arena_log.csv")

WITNESSES = [  # feature, plain-language explanation
    ("tu5_cdev", "5 cm differs from what the untouched sensors (20/50 cm, soil temperature, ERA5) imply"),
    ("box_tu5_absmax", "the 5 cm level shifted compared with the days before and after"),
    ("pair5_24", "unexplained rise followed by an unexplained fall (start/end of an offset)"),
    ("inc5_24", "24 h change of the 5 cm probe not explained by the other sensors"),
    ("rise_nosupport72", "soil got wetter without plausible rain"),
    ("rain_missing24", "weather says it rained (humidity, radiation, ERA5) but no rain was recorded"),
    ("rain_implaus24", "rain recorded but the weather does not support it"),
    ("ratio1020_cdev", "the 10/20 cm depth relationship is distorted"),
    ("dup_later_m24", "this exact sequence of changes was already recorded earlier (copy)"),
    ("raindup_m24", "this rain sequence is an exact copy of another storm"),
    ("nb_rain_vs72", "rain differs from the neighbouring stations"),
    ("tE5_cdev", "5 cm differs from the ERA5 reanalysis"),
]
# sensors each witness looks at: a witness only counts as evidence when the attacker touched one of them
# (a natural rain quirk at the hour of a soil-moisture attack is not a reason)
_S5, _S10, _R = "SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "P_CALC"
WITNESS_SENSORS = {
    "rise_nosupport72": {_S5, _R}, "rain_missing24": {_R}, "rain_implaus24": {_R}, "raindup_m24": {_R},
    "nb_rain_vs72": {_R}, "ratio1020_cdev": {_S10},
}  # every other witness looks at the 5 cm probe


def _relevant(w, changed):
    return not changed or bool(WITNESS_SENSORS.get(w, {_S5}) & set(changed))


FAM_LABEL = {"spike": "Spike", "bias": "Bias", "drift": "Drift", "replay": "Replay", "fake_storm": "Fake storm", "hidden_storm": "Hidden storm", "stealth": "Stealth"}


def _hash01(key):
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 / 1000


class Arena:
    def __init__(self):
        import time
        t0 = time.time()
        print("\n" + "="*50, flush=True)
        print("[Arena Init] STARTING WARM-UP...", flush=True)
        
        print(f"[Arena Init] 1. Reading detector_input.parquet (210k rows)...", flush=True)
        self.base = pd.read_parquet(os.path.join(HERE, "base", "detector_input.parquet"))
        self.base["time"] = pd.to_datetime(self.base.time).dt.tz_localize(None)
        print(f"   -> Done in {time.time()-t0:.1f}s", flush=True)
        
        print(f"[Arena Init] 2. Loading FastDefender (unpickling 5 LightGBM twins)...", flush=True)
        self.D = FastDefender()
        print(f"   -> Done in {time.time()-t0:.1f}s", flush=True)
        
        print(f"[Arena Init] 3. Loading Type Classifier...", flush=True)
        T = joblib.load(os.path.join(HERE, "type_classifier.joblib")); 
        self.tclf, self.tfeat, self.tcls = T["model"], T["features"], T["classes"]
        print(f"   -> Done in {time.time()-t0:.1f}s", flush=True)
        
        print(f"[Arena Init] 4. Checking Witness Reference...", flush=True)
        ref = os.path.join(HERE, "witness_ref.joblib")
        
        if os.path.exists(ref):                                             
            print(f"   -> FOUND witness_ref.joblib (Fast Path)", flush=True)
            W = joblib.load(ref); 
            self.wref, self.sigma = W["wref"], W["sigma"]
        else:                                                               
            print(f"   -> MISSING witness_ref.joblib! Triggering SLOW 60s fallback...", flush=True)
            print(f"   -> Reading base_features.parquet...", flush=True)
            bf = pd.read_parquet(os.path.join(HERE, "base_features.parquet")); bf = bf[bf.year <= 2024]
            
            print(f"   -> Calculating wref...", flush=True)
            self.wref = {st: {w: np.sort(np.abs(g[w].dropna().to_numpy()))[::7] for w, _ in WITNESSES if w in g and g[w].notna().sum() > 500} for st, g in bf.groupby("station")}
            
            print(f"   -> Calculating sigma...", flush=True)
            self.sigma = {}                                                 
            for st, g in bf.groupby("station"):
                b = self.base[(self.base.station == st)].set_index("time").SOIL_MOISTURE_5.reindex(g.time).to_numpy()
                res = pd.Series(b - g.exp5_tu.to_numpy()); self.sigma[st] = float(np.nanstd(res - res.rolling(720, min_periods=48, center=True).median()))
        
        print(f"[Arena Init] 5. DONE. Total time: {time.time()-t0:.1f}s", flush=True)
        print("="*50 + "\n", flush=True)
        self.lock = threading.Lock(); self.jobs = {}
    # ------------------------------------------------------------------ helpers
    def _unusual(self, st, w, x):
        r = self.wref.get(st, {}).get(w)
        if r is None or len(r) == 0: return np.zeros(len(x))
        return np.searchsorted(r, np.abs(np.nan_to_num(x)), side="left") / len(r)      # share of clean hours strictly less extreme

    def _defend(self, att, rl, stations):
        M = self.D.M
        with self.lock:
            P = self.D.features(att, rl, stations)
            P = P[P.tu5_z.notna()].reset_index(drop=True)
            P["p1"] = dc._p(M["M1"], P[M["F1"]].to_numpy(np.float32))
            D2 = dc.stage2_features(P[["station"]], P.p1.to_numpy())
            X2 = np.column_stack([D2[c].to_numpy() if c in D2 else P[c].to_numpy() for c in M["F2"]]).astype(np.float32)
            P["proba"] = dc._p(M["M2"], X2); P["alerte"] = P.proba >= M["thr"]
            m = (P.alerte | P.attack_id.notna()).to_numpy()            # attack type only where it matters (alerts + attack windows)
            for c in self.tcls: P["type_" + c] = np.nan
            if m.any():
                tp = self.tclf.predict_proba(P.loc[m, self.tfeat])
                for j, c in enumerate(self.tcls): P.loc[m, "type_" + c] = tp[:, j]
        return P

    def _evaluate(self, man, P):
        rows = []
        att_hours = P.attack_id.notna()
        for _, m in man.iterrows():
            r = m.to_dict()
            if m.status != "applied": rows.append(r); continue
            st, t0, t1 = m.station, pd.Timestamp(m.start), pd.Timestamp(m.end)
            g = P[P.station == st]
            win = g[(g.time >= t0 - pd.Timedelta(hours=6)) & (g.time <= t1 + pd.Timedelta(hours=24)) & ((g.attack_id == m.id) | g.attack_id.isna())]
            own = g[g.attack_id == m.id]
            al = win[win.alerte]
            r.update(detected=bool(len(al)), hours_detected=float(own.alerte.mean()) if len(own) else 0.0,
                     delay_h=float((al.time.min() - t0) / pd.Timedelta(hours=1)) if len(al) else None, max_proba=float(win.proba.max()))
            src = al if len(al) else own
            tp = {c: float(src["type_" + c].mean()) for c in self.tcls}
            r["type_pred"] = max(tp, key=tp.get); r["type_probs"] = tp
            snr5 = float(m.damage_int / (self.sigma.get(st, 0.02) * max(m.duration_h, 1))) if m.damage_int == m.damage_int else 0.0
            r["snr"] = max(snr5, float(m.get("damage_rain", 0) or 0) / 2.0)   # >= 2 mm of rain added/removed also counts as detectable evidence
            rows.append(r)
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ one attack, with animation frames
    def simulate_one(self, scenario, seed=0, margin_days=7):
        t = time.time()
        att, rl, man = inject(self.base, [scenario], seed=seed)
        m = man.iloc[0].to_dict()
        if m["status"] != "applied":
            return dict(status="rejected", reason=m["reason"], manifest=_clean(m))
        st = m["station"]
        P = self._defend(att, rl, [st])
        ev = self._evaluate(man, P).iloc[0].to_dict()
        t0, t1 = pd.Timestamp(m["start"]) - pd.Timedelta(days=margin_days), pd.Timestamp(m["end"]) + pd.Timedelta(days=margin_days)
        b = self.base[(self.base.station == st) & (self.base.time >= t0) & (self.base.time <= t1)].set_index("time")
        a = att[(att.station == st) & (att.time >= t0) & (att.time <= t1)].set_index("time")
        g = P[(P.station == st) & (P.time >= t0) & (P.time <= t1)].set_index("time")
        idx = b.index
        E = dc.era5(); e = E[E.station == st].set_index("time").reindex(idx)
        g = g.reindex(idx)
        inside = (idx >= pd.Timestamp(m["start"])) & (idx <= pd.Timestamp(m["end"]))
        pre = idx < pd.Timestamp(m["start"])                                  # twins have a station-specific offset: align them on the days before
        off = np.nanmedian((a.SOIL_MOISTURE_5.to_numpy() - g.exp5_tu.to_numpy())[pre]) if pre.any() else 0.0
        exp_aligned = g.exp5_tu.to_numpy() + (0.0 if off != off else off)
        wit = []
        changed = m.get("changed") or []
        for w, txt in WITNESSES:
            if w not in g or not _relevant(w, changed): continue
            u = self._unusual(st, w, g[w].to_numpy())
            wit.append(dict(key=w, text=txt, values=_r(u, 3), peak_inside=float(np.nanmax(u[inside])) if inside.any() else 0.0))
        wit.sort(key=lambda d: -d["peak_inside"])
        reasons = [d["text"] for d in wit if d["peak_inside"] >= 0.98][:3]
        if not ev["detected"]:                                                # a miss: show the closest calls, not "reasons"
            reasons = [d["text"] for d in wit[:2] if d["peak_inside"] >= 0.9]
        tp = ev.get("type_probs", {})
        top = sorted(tp.items(), key=lambda kv: -kv[1])[:3]
        verdict = (f"DETECTED after {ev['delay_h']:.0f} h" if ev["detected"] and ev["delay_h"] is not None and ev["delay_h"] > 0 else
                   "DETECTED from the first hour" if ev["detected"] and ev["delay_h"] is not None else
                   "DETECTED" if ev["detected"] else "NOT DETECTED — the attack got through")
        frames = dict(
            status="applied", scenario=_clean(normalize(scenario)[0]), manifest=_clean(m),
            station=st, threshold=float(self.D.thr), compute_s=round(time.time() - t, 1),
            time=[x.strftime("%Y-%m-%d %H:%M") for x in idx],
            attack_window=[m["start"], m["end"]],
            series=dict(clean5=_r(b.SOIL_MOISTURE_5), att5=_r(a.SOIL_MOISTURE_5), clean10=_r(b.SOIL_MOISTURE_10), att10=_r(a.SOIL_MOISTURE_10),
                        clean_rain=_r(b.P_CALC, 1), att_rain=_r(a.P_CALC, 1), expected5=_r(exp_aligned), sm20=_r(b.SOIL_MOISTURE_20),
                        soil_temp5=_r(b.SOIL_TEMP_5, 1), rh=_r(b.RH_HR_AVG, 0), solar=_r(b.SOLARAD, 0), era5_sm=_r(e.e_swvl1), era5_rain=_r(e.e_rain, 2),
                        label=[int(x) for x in g.label.fillna(0)]),
            untouched=["sm20", "soil_temp5", "rh", "solar", "era5_sm", "era5_rain"],
            witnesses=wit[:8],
            scores=dict(stage1=_r(g.p1, 3), proba=_r(g.proba, 3), alert=[bool(x) for x in g.alerte.fillna(False)]),
            decision=dict(detected=ev["detected"], verdict=verdict, first_alert_delay_h=ev["delay_h"], hours_detected=ev["hours_detected"],
                          max_proba=ev["max_proba"], type_top3=[dict(type=k, label=FAM_LABEL.get(k, k), p=round(v, 3)) for k, v in top],
                          reasons=reasons or ["no witness strongly abnormal — the attack stays within natural variability"],
                          damage_max=m["damage_max"], damage_int=m["damage_int"]))
        return frames

    # ------------------------------------------------------------------ batches
    def generate(self, families=FAMILIES, stations=tuple(dc.STATIONS), amplitudes=None, durations=None, shapes=None, sensors=None, seed=0, max_n=400):
        """grid of scenarios placed in 2025, >= 35 days apart per station (several rounds if needed)."""
        rng = np.random.default_rng(seed); out = []
        for fam in families:
            lo, hi, amax = LIMITS[fam]; dsens, dshape, ddur, damp = DEFAULTS[fam]
            durs = [d for d in (durations or [ddur]) if lo <= d <= hi] or [ddur]
            amps = [a for a in (amplitudes or ([damp] if damp else [None])) if a is None or amax is None or abs(a) <= amax] if fam not in ("replay", "hidden_storm") else [None]
            if fam == "fake_storm": amps = [1.0]
            for st in stations:
                for d in durs:
                    for a in amps:
                        for sh in (shapes or [dshape]):
                            for se in (sensors or [dsens]):
                                if fam in ("fake_storm", "hidden_storm"): se = "5+10+rain"
                                sc = dict(family=fam, station=st, duration_h=int(d), shape=sh, sensors=se)
                                if a is not None: sc["amplitude"] = float(a) * (1 if fam in ("fake_storm",) else rng.choice([-1, 1]))
                                if fam == "replay": sc["params"] = {"replay_lag_d": int(rng.integers(7, 60))}
                                out.append(sc)
        rng.shuffle(out); out = out[:max_n]
        slots = {st: 0 for st in stations}; rounds = {}
        for sc in out:                                     # pack: per station, slot k -> round k // 9, date = 2025-01-20 + 38 days * (k % 9)
            k = slots[sc["station"]]; slots[sc["station"]] += 1
            sc["start"] = str(pd.Timestamp("2025-01-20") + pd.Timedelta(days=38 * (k % 9)) + pd.Timedelta(hours=int(rng.integers(0, 72))))
            sc["round"] = k // 9
        for i, sc in enumerate(out): sc["id"] = f"A{seed:03d}-{i:04d}-{sc['family']}"
        return out

    def run_batch(self, scenarios, job_id=None, keep_frames=True):
        job_id = job_id or uuid.uuid4().hex[:8]
        J = self.jobs[job_id] = dict(id=job_id, status="running", done=0, total=len(scenarios), started=time.time(), results=None, error=None)
        d = os.path.join(RUNS, job_id); os.makedirs(d, exist_ok=True)
        try:
            res = []
            for r in sorted(set(sc.get("round", 0) for sc in scenarios)):
                part = [sc for sc in scenarios if sc.get("round", 0) == r]
                att, rl, man = inject(self.base, part, seed=r)
                pm = {m_.id: m_.params for m_ in man.itertuples()}           # resolved choices (storm source, dry curve) -> exact replays
                for sc in part:
                    if sc.get("id") in pm and isinstance(pm[sc["id"]], dict): sc["params"] = dict(pm[sc["id"]]); sc["start"] = man.set_index("id").start.get(sc["id"], sc["start"])
                ok = man[man.status == "applied"]
                if len(ok):
                    P = self._defend(att, rl, sorted(ok.station.unique()))
                    ev = self._evaluate(man, P)
                else:
                    ev = man
                ev["job"] = job_id; ev["round"] = r; ev["model_version"] = "v3_final"
                res.append(ev); J["done"] += len(part)
            R = pd.concat(res, ignore_index=True)
            R = self._registry(R)
            R.to_json(os.path.join(d, "results.json"), orient="records", default_handler=str)
            R.drop(columns=[c for c in ("params", "type_probs", "changed") if c in R]).to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)
            json.dump(scenarios, open(os.path.join(d, "scenarios.json"), "w"), default=str)
            J.update(status="done", results=_records(R), summary=self.scoreboard(R), elapsed_s=round(time.time() - J["started"]))
        except Exception as e:  # keep the job visible with its error
            J.update(status="error", error=repr(e))
        return J

    def run_batch_async(self, scenarios):
        job_id = uuid.uuid4().hex[:8]
        self.jobs[job_id] = dict(id=job_id, status="queued", done=0, total=len(scenarios))
        threading.Thread(target=self.run_batch, args=(scenarios, job_id), daemon=True).start()
        return job_id

    # ------------------------------------------------------------------ analysis
    def _registry(self, R):
        """missed attacks -> 'known limit' (SNR < 1: as small as natural noise) or detectable; detectable ones split 70/30 by GROUP."""
        R = R.copy(); R["bucket"] = None
        ok = R.status == "applied"
        R.loc[ok & R.detected.fillna(False).astype(bool), "bucket"] = "detected"
        miss = ok & ~R.detected.fillna(False).astype(bool)
        lim = miss & (R.snr.fillna(0) < 1); R.loc[lim, "bucket"] = "known_limit"
        det = miss & ~lim
        amp = R.amplitude.abs().fillna(0); ab = np.floor(np.log2(np.clip(amp, 1e-3, None) / 0.005)).astype(int)
        db = np.select([R.duration_h <= 24, R.duration_h <= 96, R.duration_h <= 240], ["≤1d", "≤4d", "≤10d"], ">10d")
        parts = [R[c].astype(object).where(R[c].notna(), "na").astype(str) for c in ("family", "sensors", "shape")]
        R["group"] = [f"{a}|{b}|{c}|{d}|a{e}|{f}" for a, b, c, d, e, f in zip(*parts, db, ab, R.station.astype(str))]
        R.loc[det, "bucket"] = np.where([_hash01(str(g)) < 0.7 for g in R.loc[det, "group"]], "train", "reference")
        return R

    def rescore(self, R):
        """recompute detectability (SNR) and the registry buckets for stored results"""
        R = R.copy(); ok = R.status == "applied"
        snr5 = R.damage_int / (R.station.map(self.sigma) * R.duration_h.clip(lower=1))
        R.loc[ok, "snr"] = np.maximum(snr5[ok].fillna(0), R.loc[ok, "damage_rain"].fillna(0) / 2.0) if "damage_rain" in R else snr5[ok]
        R["detected"] = R.detected.map(_truthy).where(ok, None)
        return self._registry(R)

    def scoreboard(self, R=None):
        if R is None: R = self.log()
        if R is None or not len(R): return dict(families=[], totals={})
        R = R[R.status == "applied"].copy(); R["detected"] = R.detected.map(_truthy)
        fam = []
        for f, g in R.groupby("family"):
            miss = g[~g.detected]
            fam.append(dict(family=f, label=FAM_LABEL.get(f, f), attempts=int(len(g)), detected=int(g.detected.sum()), rate=round(float(g.detected.mean()), 3),
                            got_through=int((~g.detected).sum()), max_undetected_damage=round(float(miss.damage_max.max()), 4) if len(miss) else None,
                            mean_delay_h=round(float(pd.to_numeric(g.delay_h, errors="coerce").mean()), 1) if g.detected.any() else None,
                            hours_detected=round(float(pd.to_numeric(g.hours_detected, errors="coerce").mean()), 3)))
        b = R.bucket.value_counts().to_dict() if "bucket" in R else {}
        return dict(families=fam, totals=dict(attempts=int(len(R)), detected=int(R.detected.sum()), rate=round(float(R.detected.mean()), 3),
                                               known_limit=int(b.get("known_limit", 0)), train=int(b.get("train", 0)), reference=int(b.get("reference", 0))))

    def frontier(self, R=None):
        """detection rate vs attack size (max change of the 5 cm probe), per family."""
        if R is None: R = self.log()
        if R is None or not len(R): return {}
        R = R[R.status == "applied"].copy(); R["detected"] = R.detected.map(_truthy)
        bins = [0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 1]
        R["bin"] = pd.cut(R.damage_max.astype(float), bins)
        out = {}
        for f, g in R.groupby("family"):
            s = g.groupby("bin", observed=True).detected.agg(["mean", "size"])
            out[f] = [dict(size=f"{iv.left:.3f}–{iv.right:.3f}", mid=round((iv.left + min(iv.right, 0.2)) / 2, 4), rate=round(float(r["mean"]), 3), n=int(r["size"]))
                      for iv, r in s.iterrows()]
        return out

    def log(self):
        return pd.read_csv(LOG) if os.path.exists(LOG) else None


def _truthy(v):
    return v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("true", "1", "1.0"))


def _r(x, d=4):
    a = np.asarray(x, dtype=float); return [None if np.isnan(v) else round(float(v), d) for v in a]


def _clean(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, (np.floating, float)): out[k] = None if v != v else float(v)
        elif isinstance(v, (np.integer,)): out[k] = int(v)
        elif isinstance(v, (np.bool_,)): out[k] = bool(v)
        else: out[k] = v
    return out


def _records(R):
    return json.loads(R.to_json(orient="records", default_handler=str))
