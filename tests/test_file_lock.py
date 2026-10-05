"""CLI imports and local locks must work without a POSIX-only dependency."""

import os
import errno
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from maida import _file_lock


def test_cli_import_without_posix_locking_module():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.modules['fcntl'] = None; import maida.cli"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_journal_updates_serialize_across_processes(tmp_path):
    from maida.onboarding import _load

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "journal.json"
    path.write_text('{"journal_version": 2, "attempts": []}')
    script = """import json, sys, time
from pathlib import Path
from maida.onboarding import _locked, _load, _save
path = Path(sys.argv[1])
for i in range(5):
    with _locked(path):
        data = _load(path)
        time.sleep(.005)
        data['attempts'].append({
            'id': __import__('uuid').uuid4().hex,
            'started_at': '2026-10-04T00:00:00+00:00',
            'engine_version': 'test', 'task_kind': None,
            'assistance': 'unknown', 'outcome': 'in-progress', 'events': [],
        })
        _save(path, data)
"""

    def update(_):
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)], cwd=root, text=True, capture_output=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(4)))
    assert len(_load(path)["attempts"]) == 20
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.with_suffix(".lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body_error", [False, True])
def test_windows_lock_retries_contention_and_unlocks_first_byte(tmp_path, monkeypatch, body_error):
    calls = []

    def locking(fd, mode, length):
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        assert length == 1
        calls.append(mode)
        if len(calls) == 1:
            raise OSError(errno.EACCES, "locked by another process")

    backend = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    monkeypatch.setattr(_file_lock, "_BACKEND", "msvcrt")
    monkeypatch.setattr(_file_lock, "import_module", lambda name: backend if name == "msvcrt" else pytest.fail(name))
    sleeps = []
    monkeypatch.setattr(_file_lock.time, "sleep", sleeps.append)
    path = tmp_path / "journal.lock"
    try:
        with _file_lock.file_lock(path, RLock()):
            if body_error:
                raise ValueError("body failed")
    except ValueError:
        assert body_error
    assert calls == [1, 1, 2]
    assert sleeps == [0.05]
    assert path.exists()  # Removing a lock file would let another writer bypass it.


def test_windows_lock_fatal_io_error_is_not_retried(tmp_path, monkeypatch):
    def failed(*args):
        raise OSError(errno.EBADF, "invalid descriptor")

    monkeypatch.setattr(_file_lock, "_BACKEND", "msvcrt")
    monkeypatch.setattr(_file_lock, "import_module", lambda _: SimpleNamespace(LK_NBLCK=1, locking=failed))
    monkeypatch.setattr(_file_lock.time, "sleep", lambda _: pytest.fail("fatal failures must not retry"))
    with pytest.raises(OSError, match="invalid descriptor"):
        with _file_lock.file_lock(tmp_path / "journal.lock", RLock()):
            pytest.fail("failed lock must not enter the critical section")


def test_unavailable_lock_backend_is_an_io_failure(tmp_path, monkeypatch):
    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(_file_lock, "import_module", missing)
    with pytest.raises(OSError, match="locking is unavailable"):
        with _file_lock.file_lock(tmp_path / "journal.lock", RLock()):
            pytest.fail("failed lock must not enter the critical section")


def test_journal_temp_file_is_closed_before_atomic_replace(tmp_path, monkeypatch):
    from maida import onboarding

    created = []
    create = onboarding.tempfile.NamedTemporaryFile
    replace = os.replace

    def track_file(**kwargs):
        stream = create(**kwargs)
        created.append(stream)
        return stream

    def replace_closed(source, target):
        assert created[-1].closed
        replace(source, target)

    monkeypatch.setattr(onboarding.tempfile, "NamedTemporaryFile", track_file)
    monkeypatch.setattr(onboarding.os, "replace", replace_closed)
    path = tmp_path / "journal.json"
    payload = {"journal_version": 2, "attempts": []}
    onboarding._save(path, payload)
    assert onboarding._load(path) == payload
    assert list(tmp_path.iterdir()) == [path]


def test_failed_atomic_replace_preserves_journal_and_cleans_temp_file(tmp_path, monkeypatch):
    from maida import onboarding

    path = tmp_path / "journal.json"
    path.write_bytes(b"original journal bytes")

    def failed(*args):
        raise PermissionError("replacement denied")

    monkeypatch.setattr(onboarding.os, "replace", failed)
    with pytest.raises(PermissionError, match="replacement denied"):
        onboarding._save(path, {"journal_version": 2, "attempts": []})
    assert path.read_bytes() == b"original journal bytes"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_temp_file_creation_preserves_journal(tmp_path, monkeypatch):
    from maida import onboarding

    path = tmp_path / "journal.json"
    path.write_bytes(b"original journal bytes")

    def failed(**kwargs):
        raise PermissionError("temporary file denied")

    monkeypatch.setattr(onboarding.tempfile, "NamedTemporaryFile", failed)
    with pytest.raises(PermissionError, match="temporary file denied"):
        onboarding._save(path, {"journal_version": 2, "attempts": []})
    assert path.read_bytes() == b"original journal bytes"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are not available")
def test_existing_lock_becomes_private_without_replacing_its_inode(tmp_path):
    path = tmp_path / "existing.lock"
    path.write_bytes(b"existing lock")
    path.chmod(0o644)
    inode = path.stat().st_ino
    with _file_lock.file_lock(path, RLock()):
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_ino == inode
    assert path.read_bytes() == b"existing lock"
