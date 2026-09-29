"""Exercise release preparation without creating releases or signing remotely."""

import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())


def release_step():
    return next(step for step in WORKFLOW["jobs"]["release"]["steps"] if "IS_PRERELEASE" in step.get("env", {}))


def head_sha():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def build_archive(tmp_path, ref="refs/tags/v0.6.0rc1", sha=None):
    output = tmp_path / "release"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/build_release.sh"), str(output), "maida"],
        cwd=ROOT,
        env={
            **os.environ,
            "GITHUB_REF": ref,
            "GITHUB_SHA": sha or head_sha(),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, output


def run_release(tmp_path, prerelease, *, corrupt_archive=False):
    result, output = build_archive(tmp_path)
    assert result.returncode == 0, result.stderr
    if corrupt_archive:
        (output / "maida.tar.gz").write_bytes(b"corrupted archive")
    bundle = tmp_path / "bundle.json"
    bundle.write_text("test attestation bundle")
    calls = tmp_path / "gh-calls"
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/bash\nset -eu\nprintf "%s\\n" "$@" > "$GH_CALLS"\n')
    gh.chmod(0o755)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", release_step()["run"]],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "GH_CALLS": str(calls),
            "RUNNER_TEMP": str(tmp_path),
            "ATTESTATION_BUNDLE": str(bundle),
            "IS_PRERELEASE": prerelease,
            "GITHUB_REF_NAME": "v0.6.0rc1" if prerelease == "true" else "v0.6.0",
            "GITHUB_REPOSITORY": "maida-ai/maida",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, output, calls, bundle


@pytest.mark.parametrize("prerelease", ["true", "false"])
def test_release_creation_stays_draft_and_uploads_built_archive(tmp_path, prerelease):
    result, output, calls, bundle = run_release(tmp_path, prerelease)
    assert result.returncode == 0, result.stderr
    args = calls.read_text().splitlines()
    assert args[:2] == ["release", "create"]
    assert "--draft" in args
    assert "--verify-tag" in args
    assert "--latest=false" in args
    assert ("--prerelease" in args) == (prerelease == "true")
    assert args[-3:] == ["maida.tar.gz", "SHA256SUMS", "provenance.jsonl"]
    assert all((output / name).is_file() for name in args[-3:])
    assert (output / "provenance.jsonl").read_bytes() == bundle.read_bytes()


@pytest.mark.parametrize("prerelease", ["", "unknown"])
def test_invalid_classification_never_creates_release(tmp_path, prerelease):
    result, _, calls, _ = run_release(tmp_path, prerelease)
    assert result.returncode == 2
    assert "invalid release classification" in result.stderr
    assert not calls.exists()


def test_corrupt_archive_never_creates_release(tmp_path):
    result, _, calls, _ = run_release(tmp_path, "true", corrupt_archive=True)
    assert result.returncode != 0
    assert not calls.exists()


@pytest.mark.parametrize("ref", ["refs/tags/v0.6.0", "refs/tags/v0.6.0rc1"])
def test_archive_is_reproducible_committed_source_with_checksums(tmp_path, ref):
    result, output = build_archive(tmp_path, ref)
    assert result.returncode == 0, result.stderr
    archive = output / "maida.tar.gz"
    first = archive.read_bytes()
    digest = hashlib.sha256(first).hexdigest()
    assert (output / "SHA256SUMS").read_text() == f"{digest}  maida.tar.gz\n"
    assert (tmp_path / "output").read_text() == f"prerelease={'true' if 'rc' in ref else 'false'}\n"
    with tarfile.open(archive) as tar:
        readme = tar.extractfile("maida/README.md").read()
    assert readme == subprocess.check_output(["git", "show", "HEAD:README.md"], cwd=ROOT)
    result, _ = build_archive(tmp_path, ref)
    assert result.returncode == 0, result.stderr
    assert archive.read_bytes() == first


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.6", "refs/tags/v01.6.0", "refs/tags/v0.6.0rc"])
def test_invalid_release_refs_create_no_artifacts(tmp_path, ref):
    result, output = build_archive(tmp_path, ref)
    assert result.returncode == 2
    assert "Release builds require" in result.stderr
    assert not output.exists()


def test_mismatched_event_commit_creates_no_artifacts(tmp_path):
    result, output = build_archive(tmp_path, sha="0" * 40)
    assert result.returncode == 2
    assert "Checkout does not match" in result.stderr
    assert not output.exists()
