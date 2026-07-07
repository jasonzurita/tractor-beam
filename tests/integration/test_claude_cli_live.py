"""Live `claude` CLI integration test.

Confirms extract_json's contract against the *actual* CLI's tendency to
wrap JSON in prose/fences -- the exact failure class that broke real eBay
scans twice before being fixed (see core/vision.py). Marked
@pytest.mark.integration; skips if the `claude` binary isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from sw_sourcing.core.vision import extract_json

pytestmark = pytest.mark.integration


def test_claude_cli_json_only_response_round_trips_through_extract_json() -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH; skipping live test")

    prompt = (
        "Return ONLY strict JSON (no markdown fences, no commentary) "
        'matching this exact shape: {"ok": true}'
    )
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    parsed = json.loads(extract_json(result.stdout))

    assert parsed.get("ok") is True
