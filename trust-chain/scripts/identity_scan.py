#!/usr/bin/env python3
"""Anonymity scan over tracked and untracked-not-ignored files and file names.

Usage: identity_scan.py [--needles FILE]

Always searches for absolute home paths, "github.com/" and "@". Identity strings
are never stored in this repository: pass them in FILE, one per line, kept outside
the repository. Text files are searched line by line and binary files at byte level.
Every hit is classified; a hit with no legitimate explanation makes the script exit 1.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ABSOLUTE = re.compile(r"[A-Za-z]:(?:\\|/)+Users|/home/[a-z]|/Users/[A-Za-z]")
BINARY_SUFFIXES = {".parquet", ".png", ".joblib"}


def _enclosing_word(line: str, match: re.Match[str]) -> str:
    start, end = match.span()
    while start > 0 and line[start - 1].isalpha():
        start -= 1
    while end < len(line) and line[end].isalpha():
        end += 1
    return line[start:end]


def classify(path: str, line: str, match: re.Match[str], names: set[str]) -> str | None:
    needle = match.group(0).lower()
    if path.endswith("uv.lock"):
        return "third-party package metadata in a lockfile"
    if path == "scripts/identity_scan.py":
        return "this scanner's own search patterns"
    if needle in names:
        word = _enclosing_word(line, match).lower()
        if word != needle and word not in names:
            return f'part of the ordinary word "{word}"'
        return None
    if needle == "@":
        if line.lstrip().startswith("@"):
            return "Python decorator"
        if path.endswith(".py") and re.search(r"://\w+:\w+@", line):
            return "credential-URL rejection test fixture"
        if path.endswith(".py") and re.search(r"[\w\])] @ [\w(]", line):
            return "NumPy matrix-multiply operator"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--needles", type=Path)
    args = parser.parse_args()
    names: set[str] = set()
    if args.needles:
        names = {
            n.strip().lower() for n in args.needles.read_text("utf-8").splitlines() if n.strip()
        }
    ordered = sorted(names, key=len, reverse=True)
    pattern = re.compile("|".join([*map(re.escape, ordered), r"github\.com/", "@"]), re.IGNORECASE)
    long_names = [n.encode() for n in ordered if len(n) >= 5]
    binary = re.compile(b"(?i)" + b"|".join(map(re.escape, long_names))) if long_names else None
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    files = [f for f in files if (REPO / f).is_file()]
    reasons: Counter[str] = Counter()
    unexplained: list[str] = []
    for name in files:
        if ABSOLUTE.search(name):
            unexplained.append(f"file name: {name}")
        for match in pattern.finditer(name):
            if not classify(name, name, match, names):
                unexplained.append(f"file name: {name}")
        path = REPO / name
        data = path.read_bytes()
        if path.suffix in BINARY_SUFFIXES or b"\0" in data[:8192]:
            if binary:
                for hit in binary.finditer(data):
                    unexplained.append(f"{name}: binary hit at byte {hit.start()}")
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if ABSOLUTE.search(line) and name != "scripts/anonymize_paths.py":
                unexplained.append(f"{name}:{number}: absolute path: {line.strip()[:120]}")
            for match in pattern.finditer(line):
                reason = classify(name, line, match, names)
                if reason:
                    reasons[reason] += 1
                else:
                    unexplained.append(f"{name}:{number}: {line.strip()[:120]}")
    print(f"files scanned: {len(files)}")
    for reason, count in reasons.most_common():
        print(f"  legitimate {count:6d}  {reason}")
    print(f"unexplained: {len(unexplained)}")
    for item in unexplained:
        print(f"  {item}")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
