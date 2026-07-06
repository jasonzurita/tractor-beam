"""The protocol every source adapter implements."""

from __future__ import annotations

from typing import Protocol

from sw_sourcing.core.schema import Listing


class Adapter(Protocol):
    """Fetches raw listings from one source and normalizes them to `Listing`."""

    def fetch(self) -> list[Listing]: ...
