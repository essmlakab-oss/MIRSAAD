"""Détecteur v3 — module de construction des témoins (features) + prédiction deux étages.
Feature builder v3 — witnesses for the realistic multi-sensor attacks.
New vs v2: (1) twins cross-fitted by year on labelled normal hours  +  twins cross-fitted by month on ALL hours (self-supervised,
robust loss, no labels -> in-distribution for the new year); (2) witnesses from UNTOUCHED sensors (20/50 cm, soil temp, RH, solar, ERA5);
(3) rain-plausibility classifier; (4) long-memory residual features (7-30 days); (5) exact-copy detectors (replayed increments,
transplanted rain); (6) neighbours (Goodwell 2 E, SCAN)."""
import numpy as np, pandas as pd, lightgbm as lgb, sys, os, time, warnings
warnings.filterwarnings("ignore")
ROOT = os.environ.get("CIS_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data"))   # Data/ (ERA5_USCRN/, Voisins/)
ERA5 = f"{ROOT}/ERA5_USCRN/era5_uscrn_hourly_2022_2025.csv"
STATIONS = ["AZ_Tucson_11_W", "OK_Goodwell_2_SE", "MS_Newton_5_ENE", "NY_Ithaca_13_E", "CA_Fallbrook_5_NE"]
SCAN = {"AZ_Tucson_11_W": [("2026_AZ_SCAN", 7)], "MS_Newton_5_ENE": [("2114_AL_SCAN", 6), ("2174_AL_SCAN", 6), ("2064_MS_SCAN", 6)],
        "NY_Ithaca_13_E": [("2011_NY_SCAN", 5)]}
TRAIN_YEARS = (2022, 2023, 2024)          # labelled years; 2025 = test (labels never read)

class _Ctx:
    """mode 'fit': every model trained in station_block is recorded; mode 'apply': recorded models are replayed in the same order."""
    def __init__(self, mode, models=None): self.mode, self.models, self.i = mode, (models if models is not None else []), 0
_CTX = None
class _Fit:
    def __init__(self, m): self.m = m
    def fit(self, X, y):
        if _CTX is not None and _CTX.mode == "apply":
            m = _CTX.models[_CTX.i]; _CTX.i += 1; return m
        self.m.fit(X, y)
        if _CTX is not None and _CTX.mode == "fit": _CTX.models.append(self.m)
        return self.m
def reg(obj="l2", n=300):
    return _Fit(lgb.LGBMRegressor(n_estimators=n, learning_rate=0.05, num_leaves=31, min_child_samples=50, subsample=0.8, subsample_freq=1,
                                  colsample_bytree=0.8, reg_lambda=1.0, objective=obj, n_jobs=1, verbose=-1, random_state=0))
def clf(n=200):
    return _Fit(lgb.LGBMClassifier(n_estimators=n, learning_rate=0.05, num_leaves=31, min_child_samples=50, subsample=0.8, subsample_freq=1,
                                   colsample_bytree=0.8, n_jobs=1, verbose=-1, random_state=0))

import functools
@functools.lru_cache(maxsize=None)
def load_scan(name, utc_off):
    s = pd.read_csv(f"{ROOT}/Voisins/scan/{name}.csv")
    s["time"] = pd.to_datetime(s.time) + pd.Timedelta(hours=utc_off + 1)
    s["r"] = (s.PRCP * 25.4).clip(lower=0, upper=100); s["m"] = s["SMS-2"] / 100
    s.loc[(s.m <= 0) | (s.m > 0.7), "m"] = np.nan
    return s.groupby("time")[["r", "m"]].mean()

@functools.lru_cache(maxsize=None)
def era5():
    e = pd.read_csv(ERA5, parse_dates=["time"]); e["station"] = e.station.replace({"OK_Goodwell": "OK_Goodwell_2_SE"})
    return e[["time", "station", "t2m", "d2m", "tp_mm", "ssrd_wm2", "swvl1", "stl1"]].rename(
        columns={"t2m": "e_t2m", "d2m": "e_d2m", "tp_mm": "e_rain", "ssrd_wm2": "e_ssrd", "swvl1": "e_swvl1", "stl1": "e_stl1"})

R = lambda s, w, f="mean", c=True, mp=None: getattr(s.rolling(w, min_periods=mp or max(1, w // 3), center=c), f)()

def dup_flags(keys_a, valid, W, min_lag, both_dirs):
    """keys_a: int array (n, k). Flag windows [i, i+W) whose exact content appears elsewhere (>= min_lag away)."""
    n = len(keys_a); first = {}; out_later = np.zeros(n, np.float32); out_any = np.zeros(n, np.float32); occ = {}
    for i in range(n - W + 1):
        if not valid[i]: continue
        k = keys_a[i:i + W].tobytes(); occ.setdefault(k, []).append(i)
    for k, lst in occ.items():
        if len(lst) < 2: continue
        lst = np.array(lst)
        for i in lst:
            others = lst[np.abs(lst - i) >= min_lag]
            if len(others) == 0: continue
            out_any[i:i + W] = 1
            if (others < i).any(): out_later[i:i + W] = 1
    return out_later, out_any

def station_block(h, nbE, scans):
    h = h.sort_values("time").reset_index(drop=True)
    yr = (h.time - pd.Timedelta(hours=1)).dt.year.to_numpy(); h["year"] = yr
    mi = (yr - 2022) * 12 + (h.time - pd.Timedelta(hours=1)).dt.month.to_numpy() - 1; fold = mi % 6
    lab = h.label.to_numpy(); known = np.isin(yr, TRAIN_YEARS)
    if nbE is not None:
        h = h.merge(nbE, on="time", how="left")
    else:
        for c in ["nbE5", "nbE10", "nbErain"]: h[c] = np.nan
    if scans:
        S = [load_scan(n, o) for n, o in scans]
        rr = pd.concat([s.r for s in S], axis=1).mean(axis=1); mm = pd.concat([s.m.diff(24) for s in S], axis=1).mean(axis=1)
        h["sc_r"] = h.time.map(rr); h["sc_dm24"] = h.time.map(mm)
    else:
        h["sc_r"] = np.nan; h["sc_dm24"] = np.nan
    # ---------- basic derived inputs
    h["hr"] = h.time.dt.hour; doy = h.time.dt.dayofyear; h["doy_s"] = np.sin(2 * np.pi * doy / 365); h["doy_c"] = np.cos(2 * np.pi * doy / 365)
    for c in ["sm5", "sm10", "sm20", "sm50"]:
        for L in [1, 3, 6, 24, 72]: h[f"{c}_d{L}"] = h[c].diff(L)
    for c, src in [("rain", "rain"), ("erain", "e_rain"), ("nbr", "sc_r"), ("nbEr", "nbErain")]:
        for W in [3, 6, 24, 72, 168]: h[f"{c}_{W}"] = h[src].rolling(W, min_periods=1).sum()
    for L in [6, 24, 72]: h[f"e_swvl1_d{L}"] = h.e_swvl1.diff(L)
    h["st5_amp24"] = R(h.st5, 24, "max") - R(h.st5, 24, "min"); h["st5_d1"] = h.st5.diff(); h["st5_d3"] = h.st5.diff(3)
    h["rh_d1"] = h.rh.diff(); h["rh_d3"] = h.rh.diff(3); h["t_d1"] = h.t.diff(); h["t_d3"] = h.t.diff(3)
    clear = h.groupby("hr").solar.transform(lambda s: s.rolling(30, min_periods=5, center=True).max())  # ~30-day clear-sky envelope per hour
    h["solar_rel"] = h.solar / clear.replace(0, np.nan)
    h["sur_t"] = h.sur - h.t; h["e_dewdep"] = h.e_t2m - h.e_d2m
    h["e_rain_lead1"] = h.e_rain.shift(-1); h["e_rain_lag1"] = h.e_rain.shift(1)
    TW5 = ["sm10", "sm10_d1", "sm10_d3", "sm10_d6", "sm10_d24", "rain_6", "rain_24", "rain_72", "erain_6", "erain_24", "erain_72", "e_swvl1",
           "e_swvl1_d24", "st5", "t", "solar", "e_stl1", "e_t2m", "hr", "doy_s", "doy_c"]
    TU = ["sm20", "sm50", "sm20_d1", "sm20_d6", "sm20_d24", "sm20_d72", "sm50_d24", "st5", "st10", "st20", "st5_amp24", "st5_d1", "st5_d3", "t", "rh",
          "rh_d3", "solar", "solar_rel", "sur", "e_swvl1", "e_swvl1_d6", "e_swvl1_d24", "e_swvl1_d72", "erain_6", "erain_24", "erain_72", "erain_168",
          "e_stl1", "e_t2m", "e_d2m", "hr", "doy_s", "doy_c", "nbE5", "nbE10", "nbr_24", "nbr_168", "sc_dm24"]
    TE = ["e_swvl1", "e_swvl1_d6", "e_swvl1_d24", "e_swvl1_d72", "e_stl1", "e_t2m", "e_d2m", "erain_6", "erain_24", "erain_72", "erain_168", "hr", "doy_s", "doy_c"]
    TU = [c for c in TU if h[c].notna().mean() > 0.5]
    # ---------- twins
    def by_year(target, X):
        y = h[target].to_numpy(); out = np.full(len(h), np.nan); Xv = h[X]
        for Y in range(2022, 2026):
            tr = known & (yr != Y) & (lab == 0) & ~np.isnan(y)
            m = reg().fit(Xv[tr], y[tr]); te = yr == Y; out[te] = m.predict(Xv[te])
        return y - out
    def by_month(target, X):          # self-supervised: every hour, labels unused, robust loss
        y = h[target].to_numpy(); out = np.full(len(h), np.nan); Xv = h[X]
        for f in range(6):
            tr = (fold != f) & ~np.isnan(y)
            m = reg("huber").fit(Xv[tr], y[tr]); te = fold == f; out[te] = m.predict(Xv[te])
        return y - out
    RES = {"tw5": by_year("sm5", TW5), "tu5": by_year("sm5", TU), "tu10": by_year("sm10", TU), "tE5": by_year("sm5", TE), "tE10": by_year("sm10", TE),
           "xw5": by_month("sm5", TW5), "xu5": by_month("sm5", TU), "xu10": by_month("sm10", TU)}
    h["ratio"] = np.log(h.sm5.clip(lower=1e-3) / h.sm10.clip(lower=1e-3))
    RES["ratio"] = h.ratio.to_numpy()
    if h.sm20.notna().mean() > 0.5:
        RES["ratio1020"] = np.log(h.sm10.clip(lower=1e-3) / h.sm20.clip(lower=1e-3)).to_numpy()
    else:
        RES["ratio1020"] = np.full(len(h), np.nan)
    if h.nbE5.notna().mean() > 0.5: RES["nbE"] = (h.sm5 - h.nbE5).to_numpy()
    else: RES["nbE"] = np.full(len(h), np.nan)
    ref = known & (lab == 0)
    F = {}
    for k, v in RES.items():
        s = pd.Series(v)
        sd = np.nanstd(v[ref]) if np.isfinite(v[ref]).sum() > 100 else 1.0
        sd = sd if sd > 0 else 1.0
        z = s / sd
        F[f"{k}_z"] = z - np.nanmedian(z[ref])
        F[f"{k}_dev"] = z - z.shift(96).rolling(336, min_periods=48).median()               # vs recent past (4-18 days ago)
        F[f"{k}_cdev"] = z - R(z, 720, "median")                                            # vs centered 30-day median
        for W in [6, 24, 72, 168]: F[f"{k}_m{W}"] = R(F[f"{k}_dev"], W)
        F[f"{k}_cm168"] = R(F[f"{k}_cdev"], 168)
        F[f"{k}_sd24"] = R(z.diff(), 24, "std")
        F[f"{k}_sl72"] = R(z, 24).diff(72)
    # cross-witness agreement: attack moves 5 & 10 together relative to untouched sensors
    F["agree_u"] = F["tu5_m24"] * F["tu10_m24"]; F["agree_x"] = F["xu5_m24"] * F["xu10_m24"]
    F["gap_u_w"] = F["tu5_m24"] - F["tw5_m24"]
    # ---------- jumps / steps
    d5 = h.sm5.diff(); d10 = h.sm10.diff()
    F["jump"] = d5; F["jump_ex"] = d5 - d10
    mad = np.nanmedian(np.abs(F["jump_ex"][ref] - np.nanmedian(F["jump_ex"][ref]))) * 1.4826 or 1e-3
    jz = F["jump_ex"] / mad; F["jz"] = jz; F["jz_max24"] = R(jz.abs(), 24, "max"); F["jz_sum6"] = R(jz, 6, "sum")
    F["corr24"] = d5.rolling(24, min_periods=8).corr(d10)
    F["sync"] = np.where(d5.abs() > 0.01, d10 / d5.replace(0, np.nan), 0)
    F["sm5_zero"] = (h.sm5 <= 0.001).astype(float)
    # ---------- rain plausibility from untouched sensors (classifier trained by year on labelled normal hours)
    RX = [c for c in ["rh", "rh_d1", "rh_d3", "t_d1", "t_d3", "solar_rel", "sur_t", "st5_d1", "st5_d3", "e_rain", "e_rain_lead1", "e_rain_lag1",
                      "erain_6", "e_ssrd", "e_dewdep", "hr", "doy_s", "doy_c", "sc_r", "nbr_3", "nbErain", "nbEr_3", "sm20_d1", "sm20_d3"] if h[c].notna().mean() > 0.5]
    yb = (h.rain.fillna(0) >= 0.2).astype(int).to_numpy(); rp = np.full(len(h), np.nan)
    for Y in range(2022, 2026):
        tr = known & (yr != Y) & (lab == 0)
        m = clf().fit(h.loc[tr, RX], yb[tr]); te = yr == Y; rp[te] = m.predict_proba(h.loc[te, RX])[:, 1]
    rp = pd.Series(rp); rain = h.rain.fillna(0); wet = (rain >= 0.2).astype(float)
    F["rainp"] = rp; F["rainp_6"] = rp.rolling(6, min_periods=1).sum(); F["rainp_24"] = rp.rolling(24, min_periods=1).sum()
    F["rain_implaus6"] = (wet * (1 - rp)).rolling(6, min_periods=1).sum(); F["rain_implaus24"] = (wet * (1 - rp)).rolling(24, min_periods=1).sum()
    F["rain_missing6"] = ((1 - wet) * rp).rolling(6, min_periods=1).sum(); F["rain_missing24"] = ((1 - wet) * rp).rolling(24, min_periods=1).sum()
    F["rain_missing72"] = ((1 - wet) * rp).rolling(72, min_periods=1).sum()
    F["rainh24_vs_p"] = wet.rolling(24, min_periods=1).sum() - F["rainp_24"]
    F["rain_vs_era24"] = h.rain_24 - h.erain_24; F["rain_vs_era72"] = h.rain_72 - h.erain_72; F["rain_6"] = h.rain_6; F["rain_24"] = h.rain_24
    F["rain_noera"] = ((h.rain_6 > 1) & (h.erain_24 < 0.2)).astype(float)
    rpmax = rp.rolling(7, min_periods=1, center=True).max()
    F["rise_nosupport24"] = (d5.clip(lower=0) * (1 - rpmax)).rolling(24, min_periods=1).sum()
    F["rise_nosupport72"] = (d5.clip(lower=0) * (1 - rpmax)).rolling(72, min_periods=1).sum()
    F["sm20_rise_vs_5"] = h.sm20.diff(48) - h.sm5.diff(48) if h.sm20.notna().mean() > 0.5 else np.nan
    F["era_rise_vs_5"] = h.e_swvl1.diff(48) - h.sm5.diff(48)
    F["nb_rain_vs24"] = h.rain_24 - h.nbr_24; F["nb_rain_vs72"] = h.rain_72 - h.nbr_72
    F["nbE_rain_vs24"] = h.rain_24 - h.nbEr_24
    F["nb_dm_vs24"] = h.sm5.diff(24) - h.sc_dm24
    # ---------- exact-copy detectors
    k5 = np.round(d5.to_numpy() * 1000); k10 = np.round(d10.to_numpy() * 1000); kr = np.round(rain.to_numpy() * 10)
    okd = ~(np.isnan(k5) | np.isnan(k10)); K = np.c_[np.nan_to_num(k5), np.nan_to_num(k10)].astype(np.int16)
    nz = pd.Series(((k5 != 0) | (k10 != 0)).astype(float)).rolling(24).sum().shift(-23).to_numpy()
    allok = pd.Series(okd.astype(float)).rolling(24).sum().shift(-23).to_numpy() == 24
    F["dup_later"], F["dup_any"] = dup_flags(K, allok & (nz >= 10), 24, 72, True)
    KR = kr.astype(np.int16)[:, None]; nzr = pd.Series((kr > 0).astype(float)).rolling(12).sum().shift(-11).to_numpy()
    _, F["raindup"] = dup_flags(KR, nzr >= 3, 12, 48, True)
    v = np.round(h.sm5.to_numpy(), 3); seen = {}; fl = np.zeros(len(h), np.float32)
    for i in range(len(v) - 47):
        w = v[i:i + 48]
        if np.isnan(w).any() or w.std() < 0.0015: continue
        kk = w.tobytes()
        if kk in seen and i - seen[kk] >= 24: fl[i:i + 48] = 1
        elif kk not in seen: seen[kk] = i
    F["replaycopy"] = fl
    # ---------- increment twins: predict the CHANGE of sm5/sm10 over k hours from untouched sensors, anchored on the value k hours ago
    win_lab = pd.Series(lab.astype(float))
    for tgt in ["sm5", "sm10"]:
        for k in [6, 24, 72, 168]:
            Xk = pd.DataFrame({"anchor5": h.sm5.shift(k), "anchor10": h.sm10.shift(k), "erain_k": h.e_rain.rolling(k, min_periods=1).sum(),
                               "essrd_k": h.e_ssrd.rolling(k, min_periods=1).sum(), "t_k": h.t.rolling(k, min_periods=1).mean(),
                               "rhmax_k": h.rh.rolling(k, min_periods=1).max(), "solrel_k": h.solar_rel.rolling(k, min_periods=1).mean(),
                               "de_swvl1": h.e_swvl1.diff(k), "e_swvl1": h.e_swvl1, "dst5": h.st5.diff(k), "st5min_k": h.st5.rolling(k, min_periods=1).min() - h.st5.shift(k),
                               "nbr_k": h.sc_r.rolling(k, min_periods=1).sum(), "nbEr_k": h.nbErain.rolling(k, min_periods=1).sum(),
                               "dnbE5": h.nbE5.diff(k), "doy_s": h.doy_s, "doy_c": h.doy_c, "hr": h.hr})
            for u in ["sm20", "sm50", "st20", "rh"]:
                if h[u].notna().mean() > 0.5: Xk["d" + u] = h[u].diff(k); Xk[u] = h[u]
            Xk = Xk.loc[:, Xk.notna().mean() > 0.5]
            yk = h[tgt].diff(k).to_numpy(); clean_win = (win_lab.rolling(k + 1, min_periods=1).max() == 0).to_numpy()
            out = np.full(len(h), np.nan)
            for Y in range(2022, 2026):
                tr = known & (yr != Y) & clean_win & ~np.isnan(yk)
                m = reg(n=250).fit(Xk[tr], yk[tr]); te = yr == Y; out[te] = m.predict(Xk[te])
            r_ = yk - out; sd = np.nanstd(r_[known & clean_win]) or 1e-3; z = pd.Series(r_ / sd)
            F[f"inc{tgt[2:]}_{k}"] = z
            if k in (6, 24):
                F[f"inc{tgt[2:]}_{k}_m24"] = R(z, 24)
                # onset/offset pairing: unexplained rise in the past week AND unexplained fall in the next week (or the reverse)
                pmax = z.rolling(168, min_periods=1).max(); pmin = z.rolling(168, min_periods=1).min()
                fmax = z[::-1].rolling(168, min_periods=1).max()[::-1]; fmin = z[::-1].rolling(168, min_periods=1).min()[::-1]
                F[f"pair{tgt[2:]}_{k}"] = np.maximum(np.minimum(pmax, -fmin), np.minimum(-pmin, fmax))
            if k in (72, 168):
                F[f"inc{tgt[2:]}_{k}_cm72"] = R(z, 72)
    # ---------- box (change-in-mean) detectors on absolute residuals: window mean vs its two flanks, several lengths
    for k in ["tu5", "tu10", "xu5", "xu10", "ratio", "ratio1020", "tE5"]:
        z = F[f"{k}_z"]
        for L in [24, 72, 168, 240]:
            inside = z.rolling(L, min_periods=L // 2, center=True).mean()
            left = z.shift(L // 2 + L // 4).rolling(L // 2, min_periods=L // 4, center=True).mean()
            right = z.shift(-(L // 2 + L // 4)).rolling(L // 2, min_periods=L // 4, center=True).mean()
            F[f"box_{k}_{L}"] = inside - 0.5 * (left + right)
        F[f"box_{k}_absmax"] = pd.concat([F[f"box_{k}_{L}"].abs() for L in [24, 72, 168, 240]], axis=1).max(axis=1)
    F["box_agree_u"] = F["box_tu5_72"] * F["box_tu10_72"]; F["box_agree_x"] = F["box_xu5_168"] * F["box_xu10_168"]
    F["inc_agree24"] = F["inc5_24"] * F["inc10_24"]; F["inc_agree72"] = F["inc5_72"] * F["inc10_72"]
    for c in ["dup_later", "dup_any", "raindup"]: F[c + "_m24"] = R(pd.Series(F[c]), 24, "mean")
    # expected values from the twins (m3/m3) -- for display / explanations only, not used by the classifier
    F["exp5_tu"] = (h.sm5 - RES["tu5"]).to_numpy(); F["exp10_tu"] = (h.sm10 - RES["tu10"]).to_numpy()
    F["exp5_tw"] = (h.sm5 - RES["tw5"]).to_numpy(); F["exp5_xu"] = (h.sm5 - RES["xu5"]).to_numpy()
    out = pd.DataFrame({k: np.asarray(v, dtype=np.float32) for k, v in F.items()})
    meta = h[["station", "time", "year", "label", "attack_type", "attack_id"]].reset_index(drop=True)
    return pd.concat([meta, out], axis=1)

RENAME = {"SOIL_MOISTURE_5": "sm5", "SOIL_MOISTURE_10": "sm10", "SOIL_MOISTURE_20": "sm20", "SOIL_MOISTURE_50": "sm50", "SOIL_TEMP_5": "st5",
          "SOIL_TEMP_10": "st10", "SOIL_TEMP_20": "st20", "T_HR_AVG": "t", "P_CALC": "rain", "SOLARAD": "solar", "RH_HR_AVG": "rh", "SUR_TEMP": "sur"}

def build_frames(di, rl, stations=None, store=None, mode=None):
    """di: detector_input DataFrame, rl: row_labels DataFrame (labels only read for 2022-2024).
    store: dict station -> list of fitted models. mode=None: train as usual; 'fit': train and record into store; 'apply': reuse store (fast)."""
    global _CTX
    di = di.copy(); di["time"] = pd.to_datetime(di.time).dt.tz_localize(None)
    rl = rl.copy(); rl["time"] = pd.to_datetime(rl.time).dt.tz_localize(None)
    di = di.merge(rl[["station", "time", "is_attacked", "attack_type", "attack_id"]], on=["station", "time"], how="left")
    di["label"] = di.is_attacked.fillna(False).astype(int)
    di = di.rename(columns=RENAME)
    E = era5()
    gE = di[di.station == "OK_Goodwell_2_E"][["time", "sm5", "sm10", "rain"]].rename(columns={"sm5": "nbE5", "sm10": "nbE10", "rain": "nbErain"})
    outs = []
    for st in (stations or STATIONS):
        h = di[di.station == st].merge(E[E.station == st].drop(columns="station"), on="time", how="left")
        if mode in ("fit", "apply"): _CTX = _Ctx(mode, None if mode == "fit" else store[st])
        try:
            outs.append(station_block(h, gE if st == "OK_Goodwell_2_SE" else None, tuple(SCAN.get(st, ())) or None))
            if mode == "fit": store[st] = _CTX.models
        finally:
            _CTX = None
    return pd.concat(outs, ignore_index=True)

def build(DIR, **kw):
    return build_frames(pd.read_parquet(f"{DIR}/detector_input.parquet"), pd.read_parquet(f"{DIR}/row_labels.parquet"), **kw)

# ======================================================================================================================
# Stage 2 (temporal context of the stage-1 score) and end-to-end prediction
STAGE2_KEY = ["tu5_m24", "tu10_m24", "xu5_m24", "xu10_m24", "tw5_m24", "xw5_m24", "rain_missing72", "rise_nosupport72", "dup_later_m24", "raindup_m24"]

def stage2_features(D, p1, group_cols=("station",)):
    D = D.copy(); D["p1"] = p1
    g = D.groupby(list(group_cols), sort=False).p1
    for W in [3, 6, 12, 24, 48, 96, 168]:
        D[f"p_m{W}"] = g.transform(lambda s: s.rolling(W, min_periods=1, center=True).mean())
        D[f"p_max{W}"] = g.transform(lambda s: s.rolling(W, min_periods=1, center=True).max())
        if W >= 24: D[f"p_q25_{W}"] = g.transform(lambda s: s.rolling(W, min_periods=1, center=True).quantile(0.25))
    D["p_past24"] = g.transform(lambda s: s.rolling(24, min_periods=1).mean())
    D["p_fut24"] = g.transform(lambda s: s[::-1].rolling(24, min_periods=1).mean()[::-1])
    D["p_med720"] = g.transform(lambda s: s.rolling(720, min_periods=24, center=True).median())
    D["p_rel"] = D.p1 - D.p_med720; D["p_m24_rel"] = D.p_m24 - D.p_med720
    return D

META = ["station", "time", "year", "label", "attack_type", "attack_id", "seed"]

def load_matrix(files, memmap=None):
    """Load cached feature files into ONE pre-allocated float32 matrix. memmap='path' keeps it in a disk-backed file (low RAM).
    Returns meta DataFrame, X, feature names."""
    import pyarrow.parquet as pq
    keep = [pd.read_parquet(f, columns=["tu5_z"]).tu5_z.notna().to_numpy() for f in files]
    n = int(sum(k.sum() for k in keep))
    cols = pq.read_schema(files[0]).names
    feats = [c for c in cols if c not in META and c != "is_evaluable"]
    X = np.lib.format.open_memmap(memmap, mode="w+", dtype=np.float32, shape=(n, len(feats))) if memmap else np.empty((n, len(feats)), np.float32)
    metas = []; pos = 0
    for f, k in zip(files, keep):
        fcols = pq.read_schema(f).names
        md = pd.read_parquet(f, columns=[c for c in META + ["is_evaluable"] if c in fcols])[k].reset_index(drop=True)
        if "is_evaluable" not in md: md["is_evaluable"] = True
        for j0 in range(0, len(feats), 40):                      # column blocks -> small temporary memory
            blk = feats[j0:j0 + 40]
            X[pos:pos + len(md), j0:j0 + len(blk)] = pd.read_parquet(f, columns=blk)[k].to_numpy(np.float32)
        metas.append(md); pos += len(md)
    M = pd.concat(metas, ignore_index=True)
    if "is_evaluable" in M:
        ev = M.is_evaluable.fillna(False).astype(bool).to_numpy(); M = M[ev].drop(columns="is_evaluable").reset_index(drop=True); X = X[ev]
    return M, X, feats

LGB1 = dict(objective="binary", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, bagging_fraction=0.7, bagging_freq=1,
            feature_fraction=0.5, lambda_l2=2.0, verbose=-1, seed=0, num_threads=0)
LGB2 = dict(LGB1, num_leaves=31, feature_fraction=0.8)

def train_booster(full_ds, idx, params, rounds):
    return lgb.train(params, full_ds.subset(np.sort(idx).astype(np.int32)), num_boost_round=rounds)

def predict_chunks(booster, X, idx, chunk=300000):
    out = np.empty(len(idx), np.float64)
    for i in range(0, len(idx), chunk): out[i:i + chunk] = booster.predict(X[idx[i:i + chunk]])
    return out

def _p(m, X):
    return m.predict(X) if isinstance(m, lgb.Booster) else m.predict_proba(X)[:, 1]

def predict(D, M):
    """D = build(...) output (one dataset). M = joblib.load('detecteur_v3.joblib'). Returns D with proba (0-1) and alerte."""
    D = D[D.tu5_z.notna()].copy()
    p1 = _p(M["M1"], D[M["F1"]].to_numpy(np.float32))
    D2 = stage2_features(D[["station"]], p1)
    X2 = np.column_stack([D2[c].to_numpy() if c in D2 else D[c].to_numpy() for c in M["F2"]]).astype(np.float32)
    D["proba"] = _p(M["M2"], X2)
    D["alerte"] = D.proba >= M["thr"]
    return D
