"""Vision grade + repro-risk gate.

Sends all of a listing's photos in one request; parses the strict JSON
response; deterministically recomputes `target_grade_count` and
`authentic_weapon_count` from the itemized results — the model's own
aggregate is advisory only and never trusted for cost/decision math. Caches
by a hash of the listing's full image set so a listing is never billed
twice.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

from sw_sourcing.core.authenticity import ReproRisk
from sw_sourcing.storage.db import Database

Grade = Literal["high", "mid", "low", "damaged", "uncertain"]
ItemType = Literal["figure", "weapon", "accessory"]

# "damaged"/"uncertain" never count toward target_grade_count regardless of
# grade_floor -- they aren't points on the wear spectrum, they're rejects.
_GRADE_ORDER: dict[Grade, int] = {
    "high": 3,
    "mid": 2,
    "low": 1,
    "damaged": 0,
    "uncertain": -1,
}
_RISK_ORDER: dict[ReproRisk, int] = {"low": 0, "elevated": 1, "high": 2}


class VisionItem(BaseModel):
    id: int
    type: ItemType
    grade: Grade
    issues: list[str] = []
    repro_risk: ReproRisk
    confidence: float
    repro_notes: str | None = None


class VisionResult(BaseModel):
    items: list[VisionItem]
    photo_quality: str
    notes: str = ""

    def target_grade_count(self, *, grade_floor: Grade = "mid") -> int:
        """Figures at or above `grade_floor`, undamaged, and repro_risk low.

        `grade_floor` is config-driven (see storage/config.py), never
        hardcoded here -- CLAUDE.md locks it as a business rule.
        """
        floor_rank = _GRADE_ORDER[grade_floor]
        return sum(
            1
            for item in self.items
            if item.type == "figure"
            and item.grade not in ("damaged", "uncertain")
            and _GRADE_ORDER[item.grade] >= floor_rank
            and item.repro_risk == "low"
        )

    @property
    def authentic_weapon_count(self) -> int:
        """Weapons/accessories with repro_risk low (grade is not a gate here)."""
        return sum(
            1
            for item in self.items
            if item.type in ("weapon", "accessory") and item.repro_risk == "low"
        )

    @property
    def max_repro_risk(self) -> ReproRisk:
        if not self.items:
            return "low"
        return max(self.items, key=lambda item: _RISK_ORDER[item.repro_risk]).repro_risk

    @property
    def has_uncertain_grade(self) -> bool:
        return any(item.grade == "uncertain" for item in self.items)

    @property
    def min_confidence(self) -> float:
        if not self.items:
            return 0.0
        return min(item.confidence for item in self.items)


class VisionClient(Protocol):
    """The client contract `Vision` needs — injected, never hardcoded here."""

    def grade_listing(
        self, *, images: Sequence[str], title: str, description: str
    ) -> str:
        """Return the raw strict-JSON string from a single grading request."""
        ...


def hash_image_set(images: Sequence[str]) -> str:
    """Stable, order-independent hash of a listing's full image set."""
    canonical = "|".join(sorted(images))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Vision:
    def __init__(self, client: VisionClient, db: Database) -> None:
        self._client = client
        self._db = db

    def grade(
        self, *, images: Sequence[str], title: str, description: str, graded_at: str
    ) -> VisionResult:
        image_set_hash = hash_image_set(images)
        cached = self._db.get_vision_cache(image_set_hash)
        if cached is not None:
            return VisionResult.model_validate_json(cached)

        raw = self._client.grade_listing(
            images=images, title=title, description=description
        )
        result = VisionResult.model_validate_json(raw)
        self._db.put_vision_cache(
            image_set_hash, result.model_dump_json(), created_at=graded_at
        )
        return result


_PROMPT_TEMPLATE = """\
You are grading a secondhand marketplace listing for vintage Kenner Star \
Wars figures/weapons/accessories.

Listing title: {title}
Listing description: {description}

Read every image at these exact local paths, in order:
{image_paths}

For each item visible across the photos, grade it and flag reproduction \
risk:
- grade: "high" (sharp, minimal wear), "mid" (clean, minor wear), "low" \
(heavy wear/fading), "damaged" (missing/broken piece), or "uncertain" \
(photo insufficient to grade).
- repro_risk: "low", "elevated", or "high" — bias to caution; any \
uncertainty about a weapon or accessory's authenticity is at least \
"elevated".
- Ambiguous piles: be conservative and lower confidence rather than guess.

Return ONLY strict JSON (no markdown fences, no commentary) matching this \
exact shape:
{{
  "items": [
    {{"id": 1, "type": "figure|weapon|accessory", "grade": "...", \
"issues": ["..."], "repro_risk": "...", "confidence": 0.0, \
"repro_notes": "optional string"}}
  ],
  "photo_quality": "clear|unclear",
  "notes": "short free-text notes"
}}
"""


def build_prompt(*, title: str, description: str, image_paths: Sequence[Path]) -> str:
    paths_block = "\n".join(str(path) for path in image_paths)
    return _PROMPT_TEMPLATE.format(
        title=title, description=description, image_paths=paths_block
    )


def extract_json(text: str) -> str:
    """Extract the first JSON object in the text.

    Despite being told to return JSON only, the model sometimes adds a
    prose preamble, wraps the JSON in a ```-fenced block, appends trailing
    commentary, or any combination of those. Finding the first `{` and
    parsing from there handles all of them in one pass, rather than trying
    to special-case each wrapping style.
    """
    start = text.index("{")
    _, end = json.JSONDecoder().raw_decode(text, start)
    return text[start:end]


class ClaudeCliVisionClient:
    """Grades a listing by shelling out to the `claude` CLI.

    No ANTHROPIC_API_KEY is provisioned for this project; the CLI is already
    authenticated. There's no direct image-attachment flag, so images are
    downloaded locally and the model is instructed to read them with its own
    Read tool, scoped to a temp directory via --add-dir.
    """

    def __init__(self, *, claude_bin: str = "claude", model: str | None = None) -> None:
        self._claude_bin = claude_bin
        self._model = model

    def grade_listing(
        self, *, images: Sequence[str], title: str, description: str
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="sw-sourcing-vision-") as tmp_dir:
            image_paths = [
                self._download(url, Path(tmp_dir), index)
                for index, url in enumerate(images)
            ]
            prompt = build_prompt(
                title=title, description=description, image_paths=image_paths
            )
            command = [
                self._claude_bin,
                "-p",
                prompt,
                "--allowedTools",
                "Read",
                "--add-dir",
                tmp_dir,
                "--output-format",
                "text",
            ]
            if self._model:
                command += ["--model", self._model]

            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return extract_json(result.stdout)

    @staticmethod
    def _download(url: str, directory: Path, index: int) -> Path:
        suffix = Path(url).suffix or ".jpg"
        destination = directory / f"image_{index}{suffix}"
        with httpx.stream("GET", url, follow_redirects=True, timeout=30.0) as response:
            response.raise_for_status()
            with destination.open("wb") as file_obj:
                for chunk in response.iter_bytes():
                    file_obj.write(chunk)
        return destination
