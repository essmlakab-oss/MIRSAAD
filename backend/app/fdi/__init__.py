"""False Data Injection detection on 5 cm soil-moisture sensors.

The model is the one-class hybrid detector from the MIRSAAD Track 2 prototype
(`oneclass_hybride_v5.joblib`): per-station digital twins (HistGradientBoosting)
plus three physical rules, trained on normal USCRN/ERA5 data only.

Layout:

    config.py        where the model lives, HF token resolution, size limits
    detector.py      HybridDetector port + load-once singleton
    preprocessing.py CSV validation and the score/feature computation
    demo.py          synthetic dataset with injected FDI attacks
    store.py         run store: uploaded data stays server-side, keyed by run_id
    schemas.py       response models -- the contract the frontend mirrors
    xai.py           the /api/xai panel: the one-hour read-out (rules, ECOD,
                     twin-SHAP, temporal) and the per-hour series
    ecod_decomp.py   vectorized ECOD with families and labeled features
    feature_labels.py  human labels + family keys for the panel's tables
    shap_explainer.py / temporal.py   legacy row-keyed views (deprecated)

Nothing here imports Streamlit; `README_app.md` §7.1 is the porting rule.
"""
