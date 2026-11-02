#!/usr/bin/env python3
"""Rewrite absolute local paths to repo-relative ones.

Usage: anonymize_paths.py [FILE-OR-EXPORT-DIR ...]

With no argument it scrubs the committed validation JSON files. An argument may
be a text file or an attack-engine export directory; in a directory HANDOVER.md
and run_provenance.json are rewritten and SHA256SUMS.txt is refreshed for them.
No other hash value is touched.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROJECTS = "attack-engine|cyber-pipeline|signal-processing|USCRN_livraison|evaluation|trust-decision|era5|results"

# Any Windows or POSIX home path, cut at the first project directory.
# Separators may be JSON-escaped in the source text.
_SEP = r"(?:\\|/)+"
_PATH = re.compile(
    rf"(?:[A-Za-z]:{_SEP}Users|/home|/Users){_SEP}[^\"'`\r\n]*?{_SEP}"
    rf"(?=(?:{PROJECTS})(?:{_SEP}|[\"'`\s]|$))(?P<rest>[^\"'`\r\n]*)"
)


def _relative(match: re.Match[str]) -> str:
    return re.sub(_SEP, "/", match.group("rest"))


def scrub_text(text: str) -> str:
    return _PATH.sub(_relative, text)


def scrub_file(path: Path) -> bool:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    new = scrub_text(text)
    if new == text:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def scrub_export(directory: Path) -> list[Path]:
    changed = [
        p
        for name in ("HANDOVER.md", "run_provenance.json")
        for p in directory.rglob(name)
        if scrub_file(p)
    ]
    sums = directory / "SHA256SUMS.txt"
    if changed and sums.is_file():
        lines = sums.read_bytes().decode("utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            digest, sep, name = line.rstrip("\r\n").partition("  ")
            target = directory / name
            if sep and target in changed:
                new = hashlib.sha256(target.read_bytes()).hexdigest()
                lines[i] = line.replace(digest, new, 1)
        sums.write_bytes("".join(lines).encode("utf-8"))
    return changed


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or sorted(
        p
        for d in ("attack-engine/validation", "cyber-pipeline/validation")
        for p in (REPO / d).glob("*.json")
    )
    for target in targets:
        if target.is_dir():
            for p in scrub_export(target):
                print(f"scrubbed {p.name} in {target.name}")
        elif scrub_file(target):
            print(f"scrubbed {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
