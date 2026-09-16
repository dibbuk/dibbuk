"""Тесты сборки фильтрграфа ffmpeg — без запуска самого ffmpeg."""

import pytest

from bot.slicing.animated import (
    MAX_FPS,
    _build_filtergraph,
    _padding_filter,
    _parse_fps,
)
from bot.slicing.grid import GridPlan


def test_graph_declares_one_output_per_tile():
    plan = GridPlan(cols=3, rows=2)
    graph, outputs = _build_filtergraph(plan, [0, 1, 2, 3], "cover", 0.0)
    assert len(outputs) == 4
    for label, _ in outputs:
        assert f"[{label}]" in graph


def test_split_count_matches_the_batch():
    plan = GridPlan(cols=2, rows=2)
    graph, _ = _build_filtergraph(plan, [0, 1, 2], "cover", 0.0)
    assert "split=3" in graph


def test_each_tile_is_cropped_at_its_own_offset():
    plan = GridPlan(cols=2, rows=2)
    graph, _ = _build_filtergraph(plan, [0, 3], "cover", 0.0)
    assert "crop=100:100:0:0" in graph
    assert "crop=100:100:100:100" in graph


def test_frame_rate_is_capped():
    graph, _ = _build_filtergraph(GridPlan(cols=1, rows=1), [0], "cover", 0.0)
    assert f"fps={MAX_FPS}" in graph


def test_cover_and_contain_pick_different_scaling():
    cover, _ = _build_filtergraph(GridPlan(cols=2, rows=2), [0], "cover", 0.0)
    contain, _ = _build_filtergraph(GridPlan(cols=2, rows=2), [0], "contain", 0.0)
    assert "force_original_aspect_ratio=increase" in cover
    assert "force_original_aspect_ratio=decrease" in contain


def test_zero_padding_adds_no_filter():
    assert _padding_filter(0.0, 100) == ""


def test_positive_padding_shrinks_then_pads():
    result = _padding_filter(0.1, 100)
    assert "scale=80:80" in result
    assert "pad=100:100" in result


def test_negative_padding_zooms_then_crops():
    result = _padding_filter(-0.1, 100)
    assert "scale=120:120" in result
    assert "crop=100:100" in result


@pytest.mark.parametrize(
    "raw,expected",
    [("30/1", 30.0), ("30000/1001", pytest.approx(29.97, abs=0.01)),
     ("0/0", 0.0), (None, 0.0), ("25", 25.0), ("garbage", 0.0)],
)
def test_frame_rate_parsing_handles_every_shape(raw, expected):
    assert _parse_fps(raw) == expected
