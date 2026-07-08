import json
from pathlib import Path

from sw_sourcing.core.vision import (
    Vision,
    build_prompt,
    extract_json,
    hash_listing_content,
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


def test_target_grade_count_excludes_an_era_mismatched_figure(tmp_path: Path) -> None:
    era_mismatch_result = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "era_mismatch": True,
                    "era_notes": "2022 Hasbro Vintage Collection, not 1977-1985.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    vision, _ = make_vision(tmp_path, response=era_mismatch_result)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.target_grade_count() == 0


def test_authentic_weapon_count_excludes_an_era_mismatched_weapon(
    tmp_path: Path,
) -> None:
    era_mismatch_result = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "weapon",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "era_mismatch": True,
                    "era_notes": "Modern reissue weapon, not original vintage tooling.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    vision, _ = make_vision(tmp_path, response=era_mismatch_result)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.authentic_weapon_count == 0


def test_era_mismatch_defaults_to_false(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert not any(item.era_mismatch for item in result.items)


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


def test_has_rare_candidate_is_false_when_none_are_flagged(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert not result.has_rare_candidate


def test_has_rare_candidate_is_true_when_an_item_is_flagged(tmp_path: Path) -> None:
    rare_result = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "weapon",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "rare_candidate": True,
                    "rarity_notes": "Matches the long-saber variant.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    vision, _ = make_vision(tmp_path, response=rare_result)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.has_rare_candidate


def test_rare_items_summary_is_empty_when_none_are_flagged(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert result.rare_items_summary == ""


def test_rare_items_summary_includes_the_rarity_notes(tmp_path: Path) -> None:
    rare_result = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "weapon",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "rare_candidate": True,
                    "rarity_notes": "Matches the long-saber variant.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    vision, _ = make_vision(tmp_path, response=rare_result)
    result = vision.grade(
        images=["a"], title="t", description="d", graded_at="2026-07-06T00:00:00Z"
    )
    assert "Matches the long-saber variant." in result.rare_items_summary


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


def test_has_cached_grade_is_false_before_grading(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    assert not vision.has_cached_grade(images=["a", "b"], title="t", description="d")


def test_has_cached_grade_is_true_after_grading(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    vision.grade(
        images=["a", "b"],
        title="t",
        description="d",
        graded_at="2026-07-06T00:00:00Z",
    )
    assert vision.has_cached_grade(images=["a", "b"], title="t", description="d")


def test_has_cached_grade_is_false_when_the_title_changed(tmp_path: Path) -> None:
    vision, _ = make_vision(tmp_path)
    vision.grade(
        images=["a", "b"],
        title="t",
        description="d",
        graded_at="2026-07-06T00:00:00Z",
    )
    assert not vision.has_cached_grade(
        images=["a", "b"], title="edited title", description="d"
    )


def test_has_cached_grade_never_calls_the_client(tmp_path: Path) -> None:
    vision, client = make_vision(tmp_path)
    vision.has_cached_grade(images=["a", "b"], title="t", description="d")
    assert client.calls == 0


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
    assert not result.has_rare_candidate
    assert result.rare_items_summary == ""


def test_hash_listing_content_is_order_independent_for_images() -> None:
    assert hash_listing_content(
        images=["a", "b"], title="t", description="d"
    ) == hash_listing_content(images=["b", "a"], title="t", description="d")


def test_hash_listing_content_differs_for_different_images() -> None:
    assert hash_listing_content(
        images=["a", "b"], title="t", description="d"
    ) != hash_listing_content(images=["a", "c"], title="t", description="d")


def test_hash_listing_content_differs_for_different_titles() -> None:
    assert hash_listing_content(
        images=["a"], title="t1", description="d"
    ) != hash_listing_content(images=["a"], title="t2", description="d")


def test_hash_listing_content_differs_for_different_descriptions() -> None:
    assert hash_listing_content(
        images=["a"], title="t", description="d1"
    ) != hash_listing_content(images=["a"], title="t", description="d2")


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


def test_build_prompt_asks_about_rare_variants_and_extra_repro_caution() -> None:
    prompt = build_prompt(title="t", description="d", image_paths=[])
    assert "rare" in prompt.lower()
    assert "counterfeit" in prompt.lower() or "reproduc" in prompt.lower()


def test_build_prompt_asks_to_screen_for_non_vintage_era_items() -> None:
    prompt = build_prompt(title="t", description="d", image_paths=[])
    assert "era_mismatch" in prompt
    assert "1977" in prompt and "1985" in prompt


def test_build_prompt_treats_scuffs_marks_and_paint_loss_as_beater_grade() -> None:
    prompt = build_prompt(title="t", description="d", image_paths=[])
    assert "scuff" in prompt.lower()
    assert "paint" in prompt.lower()
    assert "beater" in prompt.lower()


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
