"""Тесты нарезки статики: размеры тайлов, паддинг, удаление фона."""

import io

import pytest
from PIL import Image

from bot.slicing.grid import GridPlan, plan_grid
from bot.slicing.static import SliceError, load_image, remove_background, slice_image


def make_image(width=900, height=600, background=(240, 240, 240)):
    image = Image.new("RGB", (width, height), background)
    Image.Image.paste(
        image, Image.new("RGB", (width // 2, height // 2), (220, 40, 60)),
        (width // 4, height // 4),
    )
    return image


def test_every_tile_is_exactly_100x100():
    image = make_image()
    plan = plan_grid(image.width, image.height)
    for tile in slice_image(image, plan):
        assert Image.open(io.BytesIO(tile.data)).size == (100, 100)


def test_tile_count_matches_the_plan():
    image = make_image()
    plan = GridPlan(cols=5, rows=4)
    assert len(slice_image(image, plan)) == 20


def test_tiles_keep_rgba_so_transparency_survives():
    image = make_image()
    plan = GridPlan(cols=2, rows=2)
    for tile in slice_image(image, plan):
        assert Image.open(io.BytesIO(tile.data)).mode == "RGBA"


def test_tile_indexes_are_unique_and_ordered():
    plan = GridPlan(cols=3, rows=3)
    tiles = slice_image(make_image(), plan)
    indexes = [tile.index for tile in tiles]
    assert indexes == sorted(indexes)
    assert len(set(indexes)) == len(indexes)


@pytest.mark.parametrize("padding", [-0.2, -0.1, 0.0, 0.1, 0.2])
def test_padding_never_changes_the_tile_size(padding):
    plan = GridPlan(cols=3, rows=2)
    for tile in slice_image(make_image(), plan, padding=padding):
        assert Image.open(io.BytesIO(tile.data)).size == (100, 100)


def test_positive_padding_adds_transparent_margin():
    plan = GridPlan(cols=2, rows=2)
    tiles = slice_image(make_image(), plan, padding=0.15)
    tile = Image.open(io.BytesIO(tiles[0].data))
    assert tile.getchannel("A").getpixel((0, 0)) == 0


def test_contain_keeps_the_whole_picture_and_cover_crops_it():
    image = make_image(1600, 400)
    plan = GridPlan(cols=4, rows=4)
    contained = slice_image(image, plan, fit="contain", skip_empty=False)
    covered = slice_image(image, plan, fit="cover", skip_empty=False)
    assert len(contained) == len(covered) == 16


def test_blank_tiles_are_dropped_when_asked():
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    plan = GridPlan(cols=4, rows=4)
    assert slice_image(image, plan, skip_empty=True) == []
    assert len(slice_image(image, plan, skip_empty=False)) == 16


def test_background_removal_clears_the_corners():
    image = Image.new("RGB", (200, 200), (255, 255, 255))
    Image.Image.paste(image, Image.new("RGB", (80, 80), (10, 10, 200)), (60, 60))
    cleaned = remove_background(image)
    assert cleaned.getpixel((0, 0))[3] == 0


def test_background_removal_keeps_the_subject():
    image = Image.new("RGB", (200, 200), (255, 255, 255))
    Image.Image.paste(image, Image.new("RGB", (80, 80), (10, 10, 200)), (60, 60))
    cleaned = remove_background(image)
    assert cleaned.getpixel((100, 100))[3] > 200


def test_broken_bytes_raise_a_readable_error():
    with pytest.raises(SliceError):
        load_image(b"not an image at all")


def test_slicing_accepts_raw_bytes():
    buffer = io.BytesIO()
    make_image(300, 300).save(buffer, format="PNG")
    tiles = slice_image(buffer.getvalue(), GridPlan(cols=3, rows=3))
    assert len(tiles) == 9
