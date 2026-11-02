"""Attack simulator for the AI Attack Arena.

One scenario (dict / JSON) -> the attack is injected into a COPY of the clean data, with exact labels and a manifest line.
Families: spike, bias, drift, replay, fake_storm, hidden_storm, stealth.
Plausibility rules (same as generator v2): values rounded to the real sensor resolution (0.001 m3/m3, 0.1 mm), never clipped:
a scenario that would leave the station's observed range is REJECTED with a reason; label = 1 only where the rounded value changed.

    from app.arena.simulator import inject
    di_att, labels, manifest = inject(di_clean, [scenario, ...])
"""
import numpy as np, pandas as pd

FAMILIES = ("spike", "bias", "drift", "replay", "fake_storm", "hidden_storm", "stealth")
SENSORS = ("5", "5+10", "5+10+rain")
DEFAULTS = {  # family: (sensors, shape, duration_h, amplitude)
    "spike": ("5", "abrupt", 3, 0.10), "bias": ("5+10", "smooth", 72, 0.03), "drift": ("5+10", "ramp", 168, 0.04),
    "replay": ("5+10+rain", "fade", 48, None), "fake_storm": ("5+10+rain", None, 72, 1.0), "hidden_storm": ("5+10+rain", None, 240, None),
    "stealth": ("5+10", "follow_era5", 96, 0.01)}
LIMITS = {  # family: (min duration, max duration, max |amplitude|)
    "spike": (1, 12, 0.35), "bias": (6, 336, 0.15), "drift": (24, 504, 0.15), "replay": (6, 240, None),
    "fake_storm": (24, 120, 2.0), "hidden_storm": (12, 240, None), "stealth": (12, 336, 0.03)}
TARGETS = ["SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "P_CALC"]


def normalize(sc):
    """Fill defaults and check a scenario. Returns (scenario, list_of_errors)."""
    sc = dict(sc); err = []
    fam = sc.get("family")
    if fam not in FAMILIES: return sc, [f"family must be one of {FAMILIES}"]
    dsens, dshape, ddur, damp = DEFAULTS[fam]
    sc.setdefault("sensors", dsens); sc.setdefault("shape", dshape); sc.setdefault("duration_h", ddur)
    if damp is not None: sc.setdefault("amplitude", damp)
    sc.setdefault("params", {}); sc.setdefault("id", f"{fam}-{sc.get('station', '?')}-{sc.get('start', '?')}")
    if sc["sensors"] not in SENSORS: err.append(f"sensors must be one of {SENSORS}")
    lo, hi, amax = LIMITS[fam]
    if not lo <= int(sc["duration_h"]) <= hi: err.append(f"duration_h for {fam} must be in [{lo}, {hi}]")
    if amax is not None and abs(float(sc.get("amplitude", 0))) > amax: err.append(f"|amplitude| for {fam} must be <= {amax}")
    if fam in ("fake_storm", "hidden_storm", "replay") and sc["sensors"] != "5+10+rain" and fam != "replay":
        err.append(f"{fam} needs sensors '5+10+rain'")
    if "station" not in sc or "start" not in sc: err.append("station and start are required")
    return sc, err


def _smoothstep(n, tau):
    x = np.arange(n); return 1 - np.exp(-x / max(tau, 1e-6))


def _envelope(shape, L, p):
    if shape == "abrupt": return np.ones(L)
    if shape == "smooth": return np.minimum(_smoothstep(L, p.get("tau_in_h", 6)), _smoothstep(L, p.get("tau_out_h", 12))[::-1])
    if shape == "ramp":
        ret = int(min(p.get("return_h", 24), L - 1)); return np.r_[np.linspace(0, 1, L - ret), np.linspace(1, 0, ret)] if ret > 0 else np.linspace(0, 1, L)
    if shape == "triangle": return 1 - np.abs(np.linspace(-1, 1, L))
    raise ValueError(f"unknown shape {shape}")


def _lagged(env, lag, k):
    lag = int(max(0, min(lag, len(env) - 1))); return np.r_[np.zeros(lag), env[:len(env) - lag]] * k


class _Station:
    """numpy view of one station's series (clean values) + the attacked copy being built."""
    def __init__(self, g, era):
        self.t = pd.to_datetime(g.time).dt.tz_localize(None).to_numpy()
        self.s5 = g.SOIL_MOISTURE_5.to_numpy(float); self.s10 = g.SOIL_MOISTURE_10.to_numpy(float)
        self.rnan = g.P_CALC.isna().to_numpy(); self.r = np.nan_to_num(g.P_CALC.to_numpy(float))
        self.a5, self.a10, self.ar = self.s5.copy(), self.s10.copy(), self.r.copy()
        self.lab = np.zeros((len(g), 3), int); self.busy = np.zeros(len(g), bool)
        self.typ = np.array([None] * len(g), dtype=object); self.aid = np.array([None] * len(g), dtype=object)
        self.lo5, self.hi5 = np.nanmin(self.s5), np.nanmax(self.s5); self.lo10, self.hi10 = np.nanmin(self.s10), np.nanmax(self.s10)
        self.doy = pd.DatetimeIndex(self.t).dayofyear.to_numpy()
        self.sw = era.reindex(pd.DatetimeIndex(self.t)).to_numpy(float) if era is not None else np.full(len(g), np.nan)
        dry24 = pd.Series(self.r).rolling(24, min_periods=1).sum().to_numpy() == 0
        self.dry24 = dry24
        self.storms = np.flatnonzero((self.r >= 1) & np.r_[False, dry24[:-1]])            # real storm starts
        fwd240 = pd.Series(self.r[::-1]).rolling(240, min_periods=240).sum().to_numpy()[::-1] == 0
        self.drystarts = np.flatnonzero(fwd240 & ~np.isnan(self.s5) & ~np.isnan(np.r_[np.nan, self.s5[:-1]]))


def _apply(S, sc, rng):
    """returns (status, reason, s, L) and writes into S.a5/a10/ar."""
    fam, L, amp, p = sc["family"], int(sc["duration_h"]), float(sc.get("amplitude") or 0), sc["params"]
    s = int(np.searchsorted(S.t, np.datetime64(pd.Timestamp(sc["start"]))))
    n = len(S.t)
    if s < 48 or s >= n: return "rejected", "start outside the data (need 48 h of history)", s, L
    use10 = "10" in sc["sensors"]; userain = "rain" in sc["sensors"]
    v5 = v10 = vr = None
    if fam == "hidden_storm":                                   # snap to the next real storm (within 14 days)
        nxt = S.storms[(S.storms >= s) & (S.storms <= s + 24 * 14)]
        if len(nxt) == 0: return "rejected", "no real storm within 14 days after start", s, L
        s = int(nxt[0])
    if s + L + 1 >= n: return "rejected", "attack runs past the end of the data", s, L
    if S.busy[max(0, s - 12):s + L + 12].any(): return "rejected", "overlaps another attack (keep >= 12 h apart; >= 30 days recommended)", s, L
    if np.isnan(S.s5[s - 1:s + L]).any() or (use10 and np.isnan(S.s10[s - 1:s + L]).any()): return "rejected", "missing sensor values in the window", s, L
    sl = slice(s, s + L)
    if fam in ("spike", "bias", "drift"):
        env = _envelope(sc["shape"] or ("abrupt" if fam != "drift" else "ramp"), L, p)
        v5 = S.s5[sl] + amp * env
        if use10: v10 = S.s10[sl] + amp * _lagged(env, p.get("lag10_h", 4 if fam != "spike" else 1), p.get("k10", 0.7))
    elif fam == "stealth":
        sw = S.sw[s - 168:s + L]
        if np.isnan(sw).any(): return "rejected", "ERA5 missing around the window", s, L
        an = sw[168:] - sw[:168].mean(); shape = np.clip(an / (np.abs(an).max() + 1e-6), -1, 1) * 0.5 + 0.5 * np.minimum(1, np.arange(1, L + 1) / 6)
        v5 = S.s5[sl] + amp * shape
        if use10: v10 = S.s10[sl] + 0.6 * amp * shape
    elif fam == "replay":
        lag = int(p.get("replay_lag_d", 21)) * 24
        if s - lag - 1 < 0 or np.isnan(S.s5[s - lag - 1:s - lag + L]).any() or np.isnan(S.s10[s - lag - 1:s - lag + L]).any():
            return "rejected", "replay source period missing", s, L
        fade = np.minimum(1, np.minimum(np.arange(1, L + 1), np.arange(L, 0, -1)) / max(1, p.get("fade_h", 6)))
        c5 = S.s5[s - lag:s - lag + L] + (S.s5[s - 1] - S.s5[s - lag - 1]); c10 = S.s10[s - lag:s - lag + L] + (S.s10[s - 1] - S.s10[s - lag - 1])
        noise = float(p.get("noise", 0.0))
        if noise > 0: c5 = c5 + rng.normal(0, noise, L); c10 = c10 + rng.normal(0, noise, L)
        v5 = fade * c5 + (1 - fade) * S.s5[sl]
        if use10: v10 = fade * c10 + (1 - fade) * S.s10[sl]
        if userain: vr = S.r[s - lag:s - lag + L].copy()
    elif fam == "fake_storm":
        if S.r[sl].sum() > 0 or not S.dry24[s]: return "rejected", "fake_storm needs a dry window (no rain in the 24 h before and during)", s, L
        dd = np.minimum(np.abs(S.doy[S.storms] - S.doy[s]), 365 - np.abs(S.doy[S.storms] - S.doy[s]))
        cand = S.storms[(dd <= 60) & (S.storms + L < n) & (np.abs(S.storms - s) > L)]
        cand = [c for c in cand if not np.isnan(S.s5[c - 1:c + L]).any() and not np.isnan(S.s10[c - 1:c + L]).any()]
        if not cand: return "rejected", "no real storm of the same season to transplant", s, L
        src = int(p["source_index"]) if "source_index" in p else int(rng.choice(cand))
        k = amp if amp else 1.0
        v5 = S.s5[sl] + k * (S.s5[src:src + L] - S.s5[src - 1]); v10 = S.s10[sl] + k * (S.s10[src:src + L] - S.s10[src - 1])
        vr = S.r[sl] + k * S.r[src:src + L]; sc["params"]["source_start"] = str(pd.Timestamp(S.t[src])); sc["params"]["source_index"] = src
    elif fam == "hidden_storm":
        b5, b10 = S.s5[s - 1], S.s10[s - 1]
        dd = np.minimum(np.abs(S.doy[S.drystarts] - S.doy[s]), 365 - np.abs(S.doy[S.drystarts] - S.doy[s]))
        qc = S.drystarts[(np.abs(S.s5[S.drystarts - 1] - b5) < 0.02) & (dd <= 45) & (np.abs(S.drystarts - s) > 240) & (S.drystarts + 240 < n)]
        qc = [q for q in qc if not np.isnan(S.s5[q:q + 240]).any() and not np.isnan(S.s10[q:q + 240]).any()]
        if not qc: return "rejected", "no real dry-down curve of the same season and level", s, L
        q = int(p["dry_index"]) if "dry_index" in p else int(rng.choice(qc)); c5 = b5 + (S.s5[q:q + 240] - S.s5[q - 1]); c10 = b10 + (S.s10[q:q + 240] - S.s10[q - 1])
        k = np.flatnonzero((S.s5[s:s + 240] <= c5) & (np.arange(240) > 6)); L = min(int(k[0]) if len(k) else 240, L)
        if L < 6: return "rejected", "storm too small to hide", s, L
        sl = slice(s, s + L); v5 = np.minimum(S.s5[sl], c5[:L]); v10 = np.minimum(S.s10[sl], c10[:L]); vr = np.zeros(L)
        sc["params"]["dry_curve_from"] = str(pd.Timestamp(S.t[q])); sc["params"]["dry_index"] = q
    # ---- plausibility: real resolution, never clip (reject instead)
    v5 = np.round(v5, 3)
    if v5.min() < S.lo5 or v5.max() > S.hi5: return "rejected", f"5 cm would leave the station's range [{S.lo5:.3f}, {S.hi5:.3f}]", s, L
    if v10 is not None:
        v10 = np.round(v10, 3)
        if v10.min() < S.lo10 or v10.max() > S.hi10: return "rejected", "10 cm would leave the station's range", s, L
    if vr is not None: vr = np.round(np.clip(vr, 0, None), 1)
    S.a5[sl] = v5
    if v10 is not None: S.a10[sl] = v10
    if vr is not None and userain: S.ar[sl] = vr
    return "applied", "", s, L


def inject(di, scenarios, seed=0):
    """di: clean detector_input DataFrame. Returns (attacked detector_input, row_labels, manifest DataFrame)."""
    rng = np.random.default_rng(seed)
    di = di.sort_values(["station", "time"]).reset_index(drop=True).copy()
    from app.arena.detector_core import era5
    E = era5().set_index("time")
    stations = {}
    man = []
    for raw in scenarios:
        sc, err = normalize(raw)
        if err: man.append(dict(id=sc.get("id"), family=sc.get("family"), station=sc.get("station"), status="rejected", reason="; ".join(err))); continue
        st = sc["station"]
        if st not in stations:
            idx = np.flatnonzero(di.station.to_numpy() == st)
            if len(idx) == 0: man.append(dict(id=sc["id"], family=sc["family"], station=st, status="rejected", reason="unknown station")); continue
            era = E[E.station == st].e_swvl1 if "e_swvl1" in E else None
            stations[st] = (idx, _Station(di.iloc[idx], era))
        idx, S = stations[st]
        b5, b10, br = S.a5.copy(), S.a10.copy(), S.ar.copy()
        status, reason, s, L = _apply(S, sc, rng); shifted_h = 0
        SHIFTABLE = ("missing", "overlaps", "dry window", "storm within")
        if status == "rejected" and sc["params"].get("auto_shift", True) and any(k in reason for k in SHIFTABLE):
            t0 = pd.Timestamp(sc["start"])
            for k in range(1, 57):                          # try every 6 h, up to 14 days later
                sc2 = dict(sc, start=str(t0 + pd.Timedelta(hours=6 * k)), params=dict(sc["params"]))
                st2, rs2, s2, L2 = _apply(S, sc2, rng)
                if st2 == "applied": status, reason, s, L, shifted_h = st2, "", s2, L2, 6 * k; sc["params"] = sc2["params"]; break
        row = dict(id=sc["id"], family=sc["family"], station=st, sensors=sc["sensors"], shape=sc.get("shape"), amplitude=sc.get("amplitude"),
                   duration_h=L, start=str(pd.Timestamp(S.t[min(s, len(S.t) - 1)])), status=status, reason=reason, params=dict(sc["params"]),
                   requested_start=str(sc["start"]), shifted_h=shifted_h)
        if status == "applied":
            sl = slice(s, s + L); ch = np.c_[np.abs(S.a5[sl] - S.s5[sl]) > 1e-9, np.abs(S.a10[sl] - S.s10[sl]) > 1e-9, np.abs(S.ar[sl] - S.r[sl]) > 1e-9]
            if not ch.any():
                S.a5, S.a10, S.ar = b5, b10, br; row.update(status="rejected", reason="no value changes after rounding (attack too small)")
            else:
                S.lab[sl] |= ch; m = ch.any(1); S.busy[sl] = True; S.typ[sl][m] = sc["family"]; S.aid[sl][m] = sc["id"]
                d5 = np.abs(S.a5[sl] - S.s5[sl])
                row.update(end=str(pd.Timestamp(S.t[s + L - 1])), hours_changed=int(m.sum()), damage_max=float(d5.max()), damage_int=float(d5.sum()),
                           damage_rain=float(np.abs(S.ar[sl] - S.r[sl]).sum()),
                           changed=[TARGETS[j] for j in range(3) if ch[:, j].any()])
        man.append(row)
    out = di.copy(); lab = np.zeros((len(di), 3), int); typ = np.array([None] * len(di), dtype=object); aid = typ.copy()
    for st, (idx, S) in stations.items():
        out.loc[idx, "SOIL_MOISTURE_5"] = S.a5; out.loc[idx, "SOIL_MOISTURE_10"] = S.a10
        out.loc[idx, "P_CALC"] = np.where(S.rnan, np.nan, S.ar); lab[idx] = S.lab; typ[idx] = S.typ; aid[idx] = S.aid
    man = pd.DataFrame(man)
    if len(man) and "end" in man:                           # witnesses look up to +-15 days: attacks closer than 30 days disturb each other
        man["spacing_warning"] = False
        ok = man.status == "applied"
        for st, g in man[ok].sort_values("start").groupby("station"):
            st_ = pd.to_datetime(g.start).to_numpy(); en = pd.to_datetime(g.end).to_numpy()
            close = np.r_[False, (st_[1:] - en[:-1]) < np.timedelta64(30, "D")]; close = close | np.r_[close[1:], False]
            man.loc[g.index[close], "spacing_warning"] = True
    rl = out[["station", "time", "partition"]].copy()
    rl["is_attacked"] = lab.max(1).astype(bool); rl["attack_type"] = typ; rl["attack_id"] = aid
    for j, c in enumerate(TARGETS): rl[c + "_lab"] = lab[:, j]
    return out, rl, man
