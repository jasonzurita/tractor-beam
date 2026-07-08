import pytest

from sw_sourcing import network


def test_wait_for_network_returns_true_immediately_when_check_succeeds() -> None:
    sleeps: list[float] = []

    result = network.wait_for_network(
        max_attempts=5,
        initial_delay_seconds=1.0,
        max_delay_seconds=16.0,
        check=lambda: True,
        sleep=sleeps.append,
    )

    assert result is True
    assert sleeps == []


def test_wait_for_network_retries_until_check_succeeds() -> None:
    attempts = {"count": 0}

    def flaky_check() -> bool:
        attempts["count"] += 1
        return attempts["count"] >= 3

    sleeps: list[float] = []

    result = network.wait_for_network(
        max_attempts=5,
        initial_delay_seconds=1.0,
        max_delay_seconds=16.0,
        check=flaky_check,
        sleep=sleeps.append,
    )

    assert result is True
    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]


def test_wait_for_network_gives_up_after_max_attempts() -> None:
    sleeps: list[float] = []

    result = network.wait_for_network(
        max_attempts=3,
        initial_delay_seconds=1.0,
        max_delay_seconds=16.0,
        check=lambda: False,
        sleep=sleeps.append,
    )

    assert result is False
    # 3 attempts means 2 sleeps in between -- no sleep after the final,
    # doomed attempt.
    assert sleeps == [1.0, 2.0]


def test_wait_for_network_caps_delay_at_max_delay_seconds() -> None:
    sleeps: list[float] = []

    network.wait_for_network(
        max_attempts=6,
        initial_delay_seconds=1.0,
        max_delay_seconds=4.0,
        check=lambda: False,
        sleep=sleeps.append,
    )

    assert sleeps == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_wait_for_network_with_a_single_attempt_never_sleeps() -> None:
    sleeps: list[float] = []

    result = network.wait_for_network(
        max_attempts=1,
        initial_delay_seconds=1.0,
        max_delay_seconds=16.0,
        check=lambda: False,
        sleep=sleeps.append,
    )

    assert result is False
    assert sleeps == []


def test_is_network_reachable_returns_true_when_connection_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(
        network.socket, "create_connection", lambda *args, **kwargs: FakeSocket()
    )

    assert network.is_network_reachable() is True


def test_is_network_reachable_returns_false_on_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("nodename nor servname provided, or not known")

    monkeypatch.setattr(network.socket, "create_connection", _raise)

    assert network.is_network_reachable() is False


def test_is_network_reachable_passes_host_port_and_timeout_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FakeSocket:
        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    def fake_create_connection(address: tuple[str, int], timeout: float) -> FakeSocket:
        seen["address"] = address
        seen["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setattr(network.socket, "create_connection", fake_create_connection)

    network.is_network_reachable(host="9.9.9.9", port=53, timeout=2.5)

    assert seen == {"address": ("9.9.9.9", 53), "timeout": 2.5}


def test_wait_for_network_default_check_uses_is_network_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check the default `check` really is `is_network_reachable` --
    prevents a refactor from silently wiring in a stub that always passes."""

    class FakeSocket:
        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(
        network.socket, "create_connection", lambda *args, **kwargs: FakeSocket()
    )

    result = network.wait_for_network(
        max_attempts=1, initial_delay_seconds=0.01, max_delay_seconds=0.01
    )

    assert result is True
