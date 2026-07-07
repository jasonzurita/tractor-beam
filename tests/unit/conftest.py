import pytest


@pytest.fixture(autouse=True)
def _default_log_path(tmp_path, monkeypatch):
    """Every test gets an isolated log path by default, so a test that
    calls cli.main() without caring about logging never writes a stray
    sw_sourcing.log into the repo root."""
    monkeypatch.setenv("SW_SOURCING_LOG_PATH", str(tmp_path / "sw_sourcing.log"))
