# -*- coding: utf-8 -*-
"""Upload the deploy bundle (base + twins + type_head) to the HF Hub.

The deploy bundle IS the deployment artifact: `attack_classifier._load_model`
fetches it on boot, so the freshly-trained bundle must replace the Hub file
before the new type output appears in the app.

Run:  .venv/Scripts/python.exe _push_bundle.py   (from backend/)
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.fdi import config  # noqa: E402

SRC = BACKEND / "models" / "detecteur_v3_deploy.joblib"


def main() -> None:
    import joblib
    from huggingface_hub import HfApi

    assert SRC.is_file(), f"{SRC} missing — run _train_type_head.py first"
    bundle = joblib.load(SRC)
    for key in ("F1", "F2", "M1", "M2", "thr", "twins", "type_head"):
        assert key in bundle, f"bundle lacks {key} — refusing to push"
    metrics = bundle["type_head"]["metrics"]
    print("type_head metrics:", metrics.get("heldout") or metrics.get("train"))

    token = config.resolve_hf_token()
    assert token, "HF_TOKEN required to push to the private repo"
    api = HfApi(token=token)
    api.upload_file(
        path_or_fileobj=SRC,
        path_in_repo=config.FDI_V3_FILENAME,
        repo_id=config.HF_REPO_ID,
        repo_type="model",
        commit_message=f"type_head: supervised attack-family classifier ({bundle['type_head']['trained_on']})",
    )
    print(f"pushed {config.HF_REPO_ID}/{config.FDI_V3_FILENAME} ({os.path.getsize(SRC)} bytes)")


if __name__ == "__main__":
    main()
