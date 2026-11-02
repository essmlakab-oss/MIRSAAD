"""Where the arena finds its assets and the shared ERA5/SCAN tree."""
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
ASSETS = Path(os.getenv("ARENA_ASSETS", str(BACKEND / "Data" / "arena"))).resolve()
# detector_core reads ERA5 and SCAN through CIS_DATA at import time (same tree as Model 2).
os.environ.setdefault("CIS_DATA", str(BACKEND / "Data"))
