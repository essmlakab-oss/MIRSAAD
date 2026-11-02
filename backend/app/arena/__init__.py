"""
Attack Arena — attacker-vs-defender simulation module.

An attacker injects false data into a clean copy of a USCRN station, the
defender (detector v3.x with digital twins fitted once) scores it, and every
result is logged and sorted (detected / known limit / train / reference).

Assets (`ARENA_ASSETS`, default backend/Data/arena/):
    base/                         clean 2022-2025 station data the attacks are injected into
    twins/<station>.joblib        digital twins fitted once on clean 2022-2024
    detecteur_v3_final.joblib     the supervised detector (two-stage LightGBM)
    type_classifier.joblib        7-class attack-type classifier
    witness_ref.joblib            witness references (plain-language reasons, noise level)
    runs/                         recorded batches + arena_log.csv (written by the module)
ERA5 and SCAN neighbours are read from CIS_DATA (default backend/Data/), the same tree Model 2 uses.
"""
