"""Fast defender for the AI Attack Arena: detector v3 with its digital twins trained ONCE on clean data (twins_store.joblib),
then only re-applied -> ~7 s per station instead of ~75 s. Validated: same 2025 F1 as the full v3 (within +-0.015).

    from app.arena.defender import FastDefender
    d = FastDefender()                                   # loads twins_store.joblib + detecteur_v3_final.joblib
    pred = d.run(di_attacked, stations=["NY_Ithaca_13_E"])  # -> station, time, proba, alerte, label, attack_type, attack_id
"""
import os, joblib, numpy as np, pandas as pd
from app.arena import detector_core as dc
from app.arena.config import ASSETS

HERE = str(ASSETS)


class FastDefender:
    def __init__(self, store_path=f"{HERE}/twins", model_path=f"{HERE}/detecteur_v3_final.joblib"):
        if os.path.isdir(store_path):   # one file per station (twins/<station>.joblib)
            self.store = {f[:-7]: joblib.load(os.path.join(store_path, f)) for f in os.listdir(store_path) if f.endswith(".joblib")}
        else:
            self.store = joblib.load(store_path)
        self.M = joblib.load(model_path)
        self.thr = self.M["thr"]

    def features(self, di, rl=None, stations=None):
        if rl is None:  # labels are only used for 2022-2024 statistics; the arena injects into clean data -> zeros are correct
            rl = di[["station", "time"]].assign(is_attacked=False, attack_type=None, attack_id=None)
        need = set(stations or dc.STATIONS)
        if "OK_Goodwell_2_SE" in need: need.add("OK_Goodwell_2_E")              # neighbour witness
        di = di[di.station.isin(need)]
        return dc.build_frames(di, rl[rl.station.isin(need)], stations=stations, store=self.store, mode="apply")

    def run(self, di, rl=None, stations=None, keep_features=False):
        D = self.features(di, rl, stations)
        P = dc.predict(D, self.M)
        cols = ["station", "time", "year", "label", "attack_type", "attack_id", "proba", "alerte"]
        return P if keep_features else P[cols].reset_index(drop=True)
