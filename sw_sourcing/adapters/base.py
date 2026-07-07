"""The protocol every source adapter implements."""

from __future__ import annotations

from typing import Protocol

from sw_sourcing.core.schema import Listing


class Adapter(Protocol):
    """Fetches raw listings from one source and normalizes them to `Listing`.

    `offset` pages into the source's results (0 = first page). A source
    with no real pagination concept (e.g. a one-shot inbox drain) should
    just return an empty list for any non-zero offset.
    """

    def fetch(self, *, offset: int = 0) -> list[Listing]: ...
