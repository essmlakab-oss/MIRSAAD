"""Hash-chained decision log: each line carries the SHA-256 of the previous line."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def write_log(records: Iterable[dict[str, Any]], path: Path) -> tuple[int, str]:
    """Write the chain and a ``.head`` sidecar holding the line count and last hash."""
    previous, count = GENESIS, 0
    with path.open("wb") as stream:
        for record in records:
            body = {"prev_sha256": previous, "index": count, **record}
            line = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
            stream.write(line + b"\n")
            previous, count = line_hash(line), count + 1
    head = json.dumps({"lines": count, "last_sha256": previous}) + "\n"
    head_path(path).write_text(head, encoding="utf-8")
    return count, previous


def head_path(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def verify_log(path: Path) -> tuple[bool, str]:
    """Return (ok, message). Detects edited, removed, reordered or appended lines."""
    previous, count = GENESIS, 0
    with path.open("rb") as stream:
        for raw in stream:
            line = raw.rstrip(b"\n")
            try:
                body = json.loads(line)
            except ValueError:
                return False, f"line {count + 1}: not valid JSON"
            if body.get("prev_sha256") != previous:
                return False, f"line {count + 1}: previous-line hash mismatch"
            if body.get("index") != count:
                return False, f"line {count + 1}: index mismatch"
            previous, count = line_hash(line), count + 1
    head = head_path(path)
    if not head.is_file():
        return False, "head file is missing, so truncation cannot be excluded"
    expected = json.loads(head.read_text(encoding="utf-8"))
    if expected != {"lines": count, "last_sha256": previous}:
        return False, "last line or line count does not match the head file"
    return True, f"{count} lines verified, last hash {previous}"
