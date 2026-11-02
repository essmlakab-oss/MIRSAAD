"""Hash pinning: nothing is unpickled unless its SHA-256 matches the committed manifest."""

import hashlib
import io
import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

import joblib
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import model_pinning as mp  # noqa: E402


def pin(artifact: Path, manifest: Path) -> None:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    name = artifact.relative_to(manifest.parent).as_posix()
    manifest.write_text(f"{digest}  {name}\n", encoding="utf-8")


@pytest.fixture
def pinned(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "model.joblib"
    joblib.dump({"thr": 0.5, "values": list(range(100))}, artifact, compress=3)
    manifest = tmp_path / mp.MANIFEST_NAME
    pin(artifact, manifest)
    return artifact, manifest


class Exploit:
    """Unpickling this creates a marker file, as an attacker's payload would run code."""

    def __init__(self, marker: Path):
        self.marker = marker

    def __reduce__(self):
        return (open, (str(self.marker), "w"))


def test_untampered_file_loads(pinned) -> None:
    artifact, manifest = pinned
    assert mp.load_pinned(artifact, manifest)["thr"] == 0.5


def test_one_flipped_byte_is_refused(pinned) -> None:
    artifact, manifest = pinned
    data = bytearray(artifact.read_bytes())
    data[len(data) // 2] ^= 0x01
    artifact.write_bytes(data)
    with pytest.raises(mp.PinningError, match="does not match pinned"):
        mp.load_pinned(artifact, manifest)


def test_unpinned_file_is_refused(pinned, tmp_path: Path) -> None:
    _, manifest = pinned
    other = tmp_path / "other.joblib"
    joblib.dump({"x": 1}, other)
    with pytest.raises(mp.PinningError, match="not pinned"):
        mp.load_pinned(other, manifest)


def test_refused_payload_never_runs(pinned, tmp_path: Path) -> None:
    artifact, manifest = pinned
    marker = tmp_path / "executed"
    artifact.write_bytes(pickle.dumps(Exploit(marker)))
    with pytest.raises(mp.PinningError):
        mp.load_pinned(artifact, manifest)
    assert not marker.exists()


def test_the_exploit_fixture_really_runs_when_unpickled(tmp_path: Path) -> None:
    """Otherwise the test above would pass for the wrong reason."""
    marker = tmp_path / "executed"
    pickle.Unpickler(io.BytesIO(pickle.dumps(Exploit(marker)))).load().close()
    assert marker.exists()


def test_artifact_outside_manifest_folder_is_refused(pinned, tmp_path: Path) -> None:
    _, manifest = pinned
    elsewhere = tmp_path.parent / f"{tmp_path.name}-other" / "model.joblib"
    elsewhere.parent.mkdir()
    try:
        joblib.dump({"x": 1}, elsewhere)
        with pytest.raises(mp.PinningError, match="not under"):
            mp.load_pinned(elsewhere, manifest)
    finally:
        shutil.rmtree(elsewhere.parent)


def test_by_name_finds_a_file_elsewhere_but_still_checks_the_hash(tmp_path: Path) -> None:
    artifact = tmp_path / "sub" / "m.joblib"
    artifact.parent.mkdir()
    joblib.dump({"x": 1}, artifact)
    sums = tmp_path / "sums"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    sums.write_text(f"{digest}  m.joblib\n", encoding="utf-8")
    assert mp.load_pinned(artifact, sums, by_name=True) == {"x": 1}
    artifact.write_bytes(artifact.read_bytes() + b"x")
    with pytest.raises(mp.PinningError, match="does not match pinned"):
        mp.load_pinned(artifact, sums, by_name=True)


def test_file_outside_repository_needs_an_explicit_manifest(pinned) -> None:
    artifact, _ = pinned
    with pytest.raises(mp.PinningError, match="outside the repository"):
        mp.load_pinned(artifact)


def test_malformed_manifest_is_refused(pinned) -> None:
    artifact, manifest = pinned
    manifest.write_text("not-a-digest model.joblib\n", encoding="utf-8")
    with pytest.raises(mp.PinningError, match="malformed"):
        mp.load_pinned(artifact, manifest)


def test_shipped_manifest_covers_every_artifact() -> None:
    assert mp.check_manifest() == []


def test_no_other_unpickling_call_in_the_folder() -> None:
    """Checks this folder only: git grep run here does not search the rest of the repository."""
    inside = shutil.which("git") and not subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=ROOT, capture_output=True, check=False
    ).returncode
    if not inside:
        pytest.skip("needs a git checkout")
    calls = [f"{module}.{'load'}" for module in ("joblib", "pickle")]
    pattern = "|".join(calls)
    result = subprocess.run(
        ["git", "grep", "-n", "--untracked", "-E", pattern],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )  # fmt: skip
    files = {line.split(":", 1)[0] for line in result.stdout.splitlines()}
    assert files == {"model_pinning.py"}
