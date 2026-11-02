"""Check that every copilot destination has a matching data-tour anchor.

Documented in README.md (section "The Copilot"); this file is the Windows
one-liner version of it.

Run:  .\\.venv\\Scripts\\python.exe _anchors.py
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, "backend")

from app.ai.platform_map import TARGETS  # noqa: E402

found: set[str] = set()
for f in pathlib.Path("frontend/src").rglob("*.tsx"):
    found |= set(re.findall(r'data-tour="([a-z0-9-]+)"', f.read_text(encoding="utf-8")))

missing = [t.id for t in TARGETS if t.anchor not in found]
print(f"targets: {len(TARGETS)} | anchors found: {len(found)} | missing: {missing or 'none'}")
raise SystemExit(1 if missing else 0)
