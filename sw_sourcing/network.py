"""Best-effort internet reachability check with bounded exponential backoff.

Used as a preflight before network-dependent commands (`scan`, `send-report`)
so a machine that just woke from sleep with no network yet gets a few bounded
retries instead of failing on the first raw DNS/socket error -- which
previously meant a missed `send-report` email sat unsent until the next
scheduled slot, and a network-dead `scan` spent a full run writing a bug
report per failed adapter/listing for a condition no code change can fix.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable

# A public DNS resolver's raw IP, not a hostname -- this deliberately
# bypasses DNS so the check isolates "no network route at all" (the
# wake-from-sleep failure mode actually seen in production) from "DNS is
# fine but this one hostname is having trouble."
_DEFAULT_PROBE_HOST = "1.1.1.1"
_DEFAULT_PROBE_PORT = 443
_DEFAULT_PROBE_TIMEOUT = 3.0


def is_network_reachable(
    *,
    host: str = _DEFAULT_PROBE_HOST,
    port: int = _DEFAULT_PROBE_PORT,
    timeout: float = _DEFAULT_PROBE_TIMEOUT,
) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_network(
    *,
    max_attempts: int,
    initial_delay_seconds: float,
    max_delay_seconds: float,
    check: Callable[[], bool] = is_network_reachable,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Retries `check` with exponential backoff (capped at
    `max_delay_seconds`) up to `max_attempts` times total. Returns True the
    moment `check` succeeds, False if every attempt fails.
    """
    delay = initial_delay_seconds
    for attempt in range(max_attempts):
        if check():
            return True
        if attempt < max_attempts - 1:
            sleep(delay)
            delay = min(delay * 2, max_delay_seconds)
    return False
