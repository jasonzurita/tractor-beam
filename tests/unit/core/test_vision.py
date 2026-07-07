import json
from pathlib import Path

from sw_sourcing.core.vision import (
    Vision,
    build_prompt,
    extract_json,
    hash_image_set,
)
from sw_sourcing.storage.db import Database
from tests.unit.factories import FakeVisionClient

RAW_RESULT = json.dumps(
    {
        "items": [
            {
                "id": 1,
                "type": "figure",
                "grade": "high",
                "issues": [],
                "repro_risk": "low",
                "confidence": 0.9,
            },
            {
                "id": 2,
                "type": "figure",
                "grade": "damaged",
                "issues": ["missing arm"],
                "repro_risk": "low",
                "confidence": 0.8,
            },
            {
                "id": 3,
                "type": "weapon",
                "grade": "mid",
                "issues": [],
                "repro_risk": "elevated",
                "confidence": 0.5,
                "repro_notes": "plastic looks glossy/new",
            },
            {
                "id": 4,
                "type": "accessory",
                "grade": "high",
                "issues": [],
                "repro_risk": "low",
                "confidence": 0.95,
            },
        ],
        # deliberately wrong aggregates, to prove these are never trusted:
        "target_grade_count": 999,
        "authentic_weapon_count": 999,
        "photo_quality": "clear",
        "notes": "test fixture",
    }
)


def make_vision(
    tmp_path: Path, response: str = RAW_RESULT
) -> tuple[Vision, FakeVisionClient]:
    client = FakeVisionClient(response)
    vision = Vision(client, Database(tmp_path / "test.db"))
    return vision, client


def test_target_grade_count_is_recomputed_not_trusted(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.target_grade_count() == 1  # only item 1: figure, high, risk low


def test_target_grade_count_respects_the_grade_floor(tmp_path: Path) -> None:
    mixed_grades = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "mid",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                },
                {
                    "id": 2,
                    "type": "figure",
                    "grade": "low",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    vision, _ = make_vision(tmp_path, response=mixed_grades)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )

    assert result.target_grade_count(grade_floor="high") == 0
    assert result.target_grade_count(grade_floor="mid") == 1
    assert result.target_grade_count(grade_floor="low") == 2


def test_authentic_weapon_count_ignores_grade_and_only_gates_on_repro_risk(
    tmp_path: Path,
) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.authentic_weapon_count == 1  # only item 4; item 3 is elevated risk


def test_max_repro_risk_is_the_highest_across_items(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.max_repro_risk == "elevated"


def test_min_confidence_is_the_lowest_across_items(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.min_confidence == 0.5


def test_has_uncertain_grade_is_false_when_none_are_uncertain(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert not result.has_uncertain_grade


def test_cache_hit_never_calls_the_client_again(tmp_path: Path) -> None:
    vision, client = make_vision(tmp_path)
    vision.grade(
        images=["a", "b"],
        title="t",
        description="d",
        graded_at="2026-07-06T00:00:00Z",
    )
    vision.grade(
        images=["a", "b"],
        title="t",
        description="d",
        graded_at="2026-07-07T00:00:00Z",
    )
    assert client.calls == 1


def test_empty_items_never_crash_the_derived_properties(tmp_path: Path) -> None:
    empty = json.dumps({"items": [], "photo_quality": "clear", "notes": ""})
    vision, _ = make_vision(tmp_path, response=empty)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.target_grade_count() == 0
    assert result.authentic_weapon_count == 0
    assert result.max_repro_risk == "low"
    assert result.min_confidence == 0.0
    assert not result.has_uncertain_grade


def test_hash_image_set_is_order_independent() -> None:
    assert hash_image_set(["a", "b"]) == hash_image_set(["b", "a"])


def test_hash_image_set_differs_for_different_sets() -> None:
    assert hash_image_set(["a", "b"]) != hash_image_set(["a", "c"])


def test_build_prompt_includes_title_description_and_each_image_path() -> None:
    prompt = build_prompt(
        title="Vintage lot",
        description="12 loose figures",
        image_paths=[Path("/tmp/a.jpg"), Path("/tmp/b.jpg")],
    )
    assert "Vintage lot" in prompt
    assert "12 loose figures" in prompt
    assert "/tmp/a.jpg" in prompt
    assert "/tmp/b.jpg" in prompt


def test_extract_json_passes_through_plain_json() -> None:
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_strips_markdown_fence_with_language_tag() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'


def test_extract_json_strips_fence_without_language_tag() -> None:
    fenced = '```\n{"a": 1}\n```'
    assert extract_json(fenced) == '{"a": 1}'


def test_extract_json_drops_trailing_commentary_after_the_json_object() -> None:
    text = '{"a": 1}\nThese are genuine vintage stock.'
    assert extract_json(text) == '{"a": 1}'


def test_extract_json_drops_a_prose_preamble_before_a_fenced_json_block() -> None:
    text = (
        "Based on the six photos, here is my assessment:\n"
        '```json\n{"a": 1}\n```\n'
        "Note: ambiguous piles."
    )
    assert extract_json(text) == '{"a": 1}'


def test_extract_json_drops_a_prose_preamble_before_unfenced_json() -> None:
    text = 'Here is my assessment: {"a": 1}'
    assert extract_json(text) == '{"a": 1}'
