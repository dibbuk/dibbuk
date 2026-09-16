"""Тесты мозаики: смещения обязаны совпадать с кодировкой UTF-16 Bot API."""

from bot.mosaic import GAP, PLACEHOLDER, build_mosaic, mosaic_fits_message, utf16_length
from bot.slicing.grid import GridPlan


def slice_utf16(text: str, offset: int, length: int) -> str:
    """Вырезает кусок так, как это сделает Telegram — по единицам UTF-16."""
    units = text.encode("utf-16-le")
    return units[offset * 2 : (offset + length) * 2].decode("utf-16-le")


def test_every_entity_points_at_a_placeholder():
    plan = GridPlan(cols=4, rows=3)
    mosaic = build_mosaic(plan, {i: f"id{i}" for i in range(plan.total)})
    for entity in mosaic.entities:
        assert slice_utf16(mosaic.text, entity.offset, entity.length) == PLACEHOLDER


def test_offsets_stay_correct_when_tiles_are_missing():
    plan = GridPlan(cols=4, rows=3)
    mosaic = build_mosaic(plan, {0: "a", 5: "b", 11: "c"})
    assert len(mosaic.entities) == 3
    for entity in mosaic.entities:
        assert slice_utf16(mosaic.text, entity.offset, entity.length) == PLACEHOLDER


def test_row_count_matches_the_plan():
    plan = GridPlan(cols=3, rows=5)
    mosaic = build_mosaic(plan, {i: f"id{i}" for i in range(plan.total)})
    assert mosaic.text.count("\n") == plan.rows - 1


def test_missing_tiles_become_gaps_not_holes():
    plan = GridPlan(cols=3, rows=1)
    mosaic = build_mosaic(plan, {1: "only"})
    assert mosaic.text == f"{GAP}{PLACEHOLDER}{GAP}"


def test_entity_ids_follow_the_grid_order():
    plan = GridPlan(cols=2, rows=2)
    mosaic = build_mosaic(plan, {0: "a", 1: "b", 2: "c", 3: "d"})
    assert [e.custom_emoji_id for e in mosaic.entities] == ["a", "b", "c", "d"]


def test_empty_mapping_produces_no_entities():
    assert build_mosaic(GridPlan(cols=2, rows=2), {}).is_empty


def test_placeholder_costs_one_utf16_unit():
    assert utf16_length(PLACEHOLDER) == 1


def test_oversized_mosaic_is_detected():
    plan = GridPlan(cols=12, rows=12)
    mosaic = build_mosaic(plan, {i: f"id{i}" for i in range(plan.total)})
    assert mosaic_fits_message(mosaic)
    assert not mosaic_fits_message(mosaic, limit=10)
