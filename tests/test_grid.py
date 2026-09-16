"""Тесты геометрии сетки — здесь живут все краевые случаи нарезки."""

import pytest

from bot.slicing.grid import (
    MAX_COLS,
    MAX_TILES_PER_PACK,
    GridError,
    GridPlan,
    clamp_padding,
    estimate_tiles,
    fit_canvas,
    plan_grid,
    rows_for_cols,
)


def test_square_image_gets_square_grid():
    plan = plan_grid(1000, 1000)
    assert plan.cols == plan.rows


def test_wide_image_gets_more_columns_than_rows():
    plan = plan_grid(1920, 1080)
    assert plan.cols > plan.rows


def test_tall_image_gets_more_rows_than_columns():
    plan = plan_grid(500, 2000)
    assert plan.rows > plan.cols


def test_auto_plan_never_exceeds_pack_limit():
    for width, height in [(100, 9000), (9000, 100), (1, 5000), (4000, 4001)]:
        assert plan_grid(width, height).total <= MAX_TILES_PER_PACK


def test_explicit_columns_are_respected():
    plan = plan_grid(1920, 1080, 8)
    assert plan.cols == 8


def test_columns_are_clamped_to_allowed_range():
    assert plan_grid(1000, 1000, 99).cols == MAX_COLS
    assert plan_grid(1000, 1000, 0).cols == 1


def test_overflowing_grid_is_shrunk_to_fit_the_pack():
    plan = plan_grid(100, 4000, 12)
    assert plan.total <= MAX_TILES_PER_PACK
    assert plan.cols < 12


def test_zero_size_is_rejected():
    with pytest.raises(GridError):
        plan_grid(0, 100)
    with pytest.raises(GridError):
        plan_grid(100, -5)


def test_tile_box_walks_left_to_right_then_down():
    plan = GridPlan(cols=3, rows=2)
    assert plan.tile_box(0) == (0, 0, 100, 100)
    assert plan.tile_box(2) == (200, 0, 300, 100)
    assert plan.tile_box(3) == (0, 100, 100, 200)


def test_tile_box_rejects_index_outside_grid():
    plan = GridPlan(cols=2, rows=2)
    with pytest.raises(IndexError):
        plan.tile_box(4)
    with pytest.raises(IndexError):
        plan.tile_box(-1)


def test_canvas_is_a_whole_number_of_tiles():
    plan = GridPlan(cols=4, rows=3)
    assert plan.canvas == (400, 300)
    assert plan.total == 12


def test_padding_is_clamped_both_ways():
    assert clamp_padding(5.0) == pytest.approx(0.20)
    assert clamp_padding(-5.0) == pytest.approx(-0.20)
    assert clamp_padding(0.05) == pytest.approx(0.05)


def test_cover_fills_the_canvas_and_contain_fits_inside_it():
    plan = GridPlan(cols=4, rows=4)  # холст 400x400
    cover = fit_canvas(800, 400, plan, "cover")
    contain = fit_canvas(800, 400, plan, "contain")
    assert cover[0] >= 400 and cover[1] >= 400
    assert contain[0] <= 400 and contain[1] <= 400


def test_rows_for_cols_never_returns_zero():
    assert rows_for_cols(4000, 1, 12) >= 1


def test_estimate_matches_the_real_plan():
    assert estimate_tiles(1200, 800, 6) == plan_grid(1200, 800, 6).total
