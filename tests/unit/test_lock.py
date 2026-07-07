from pathlib import Path

from sw_sourcing import lock


def test_acquire_succeeds_when_the_lock_is_free(tmp_path: Path) -> None:
    with lock.acquire(tmp_path / "scan.lock") as acquired:
        assert acquired


def test_acquire_fails_when_the_lock_is_already_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "scan.lock"
    with lock.acquire(lock_path) as first:
        assert first
        with lock.acquire(lock_path) as second:
            assert not second


def test_lock_is_released_once_the_context_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / "scan.lock"
    with lock.acquire(lock_path) as first:
        assert first

    with lock.acquire(lock_path) as second:
        assert second


def test_acquire_creates_the_parent_directory_if_missing(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "scan.lock"
    with lock.acquire(lock_path) as acquired:
        assert acquired
    assert lock_path.exists()
