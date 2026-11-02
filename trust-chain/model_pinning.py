"""Load model artifacts only after their SHA-256 matches a committed manifest.

A joblib file is a pickle, and unpickling runs arbitrary code. `load_pinned` hashes the file and
compares it with `models.sha256` BEFORE anything is deserialized, then loads the bytes it hashed,
so the file cannot change between the check and the load.

The manifest is the nearest `models.sha256` from the artifact's folder up to this repository root.
Each line is `<sha256>  <path relative to the manifest>`.

After an intentional retrain, re-pin with:  python model_pinning.py write
Check the shipped artifacts with:           python model_pinning.py check
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from pathlib import Path

import joblib

ROOT = Path(__file__).resolve().parent
MANIFEST_NAME = "models.sha256"
ARTIFACT_SUFFIX = ".joblib"
SKIPPED_FOLDERS = {".git", ".venv", "venv", "__pycache__", "cache_features", "datasets", "outputs"}


class PinningError(ValueError):
    """The artifact is unpinned, tampered with, or its manifest is unusable."""


def read_manifest(manifest: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name.strip():
            raise PinningError(f"{manifest.name}:{number}: malformed line")
        entries[name.strip()] = digest.lower()
    return entries


def find_manifest(path: Path) -> Path:
    """Nearest manifest at or above the artifact, never above the repository root."""
    if ROOT not in path.parents:
        raise PinningError(f"{path.name}: outside the repository, pass an explicit manifest")
    for folder in path.parents:
        candidate = folder / MANIFEST_NAME
        if candidate.is_file():
            return candidate
        if folder == ROOT:
            break
    raise PinningError(f"{path.name}: no {MANIFEST_NAME} found")


def load_pinned(path, manifest=None, by_name=False):
    """Return the object stored in `path`, or raise PinningError without unpickling anything.

    `by_name` looks the artifact up by bare file name, for an explicit sums file kept elsewhere.
    """
    artifact = Path(path).resolve()
    manifest_path = Path(manifest).resolve() if manifest else find_manifest(artifact)
    if by_name:
        name = artifact.name
    elif manifest_path.parent in artifact.parents:
        name = artifact.relative_to(manifest_path.parent).as_posix()
    else:
        raise PinningError(f"{artifact.name}: not under the folder of {manifest_path.name}")
    expected = read_manifest(manifest_path).get(name)
    if expected is None:
        raise PinningError(f"{name}: not pinned in {manifest_path.name}")
    data = artifact.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise PinningError(
            f"{name}: sha256 {actual} does not match pinned {expected}. "
            "If this is an intentional retrain, re-pin with: python model_pinning.py write"
        )
    return joblib.load(io.BytesIO(data))


def shipped_artifacts() -> list[Path]:
    """Artifacts pinned by the root manifest. A folder with its own manifest manages its own."""
    found = []
    for folder, subfolders, files in os.walk(ROOT):
        subfolders[:] = [s for s in subfolders if s not in SKIPPED_FOLDERS]
        here = Path(folder)
        if here != ROOT and MANIFEST_NAME in files:
            subfolders[:] = []
            continue
        found += [here / f for f in files if f.endswith(ARTIFACT_SUFFIX)]
    return sorted(found)


def write_manifest(manifest: Path = ROOT / MANIFEST_NAME) -> int:
    artifacts = shipped_artifacts()
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}\n"
        for p in artifacts
    ]
    manifest.write_bytes("".join(lines).encode("utf-8"))
    return len(lines)


def check_manifest(manifest: Path = ROOT / MANIFEST_NAME) -> list[str]:
    """Problems found; empty when every shipped artifact matches. Unpickles nothing."""
    entries = read_manifest(manifest)
    artifacts = {p.relative_to(ROOT).as_posix(): p for p in shipped_artifacts()}
    problems = [f"unpinned: {name}" for name in sorted(set(artifacts) - set(entries))]
    problems += [f"missing: {name}" for name in sorted(set(entries) - set(artifacts))]
    problems += [
        f"sha256 mismatch: {name}"
        for name, path in sorted(artifacts.items())
        if name in entries and hashlib.sha256(path.read_bytes()).hexdigest() != entries[name]
    ]
    return problems


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "write":
        print(f"pinned {write_manifest()} artifacts in {MANIFEST_NAME}")
        return 0
    if command == "check":
        problems = check_manifest()
        print("\n".join(problems) or "all shipped artifacts match the manifest")
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
