"""Тесты правил именования паков и брендирования заголовка."""

import re

import pytest

from bot.packs import MAX_NAME_LEN, MAX_TITLE_LEN, build_pack_name, build_pack_title

BOT = "dibbuk_emoji_bot"


def test_name_ends_with_the_required_suffix():
    assert build_pack_name(BOT, 42).endswith(f"_by_{BOT}")


def test_name_uses_only_characters_telegram_allows():
    name = build_pack_name(BOT, 987654321)
    assert re.fullmatch(r"[a-zA-Z0-9_]+", name)


def test_name_fits_the_length_limit():
    assert len(build_pack_name(BOT, 10**18)) <= MAX_NAME_LEN


def test_names_do_not_repeat_between_calls():
    names = {build_pack_name(BOT, 1) for _ in range(50)}
    assert len(names) == 50


def test_branded_title_carries_the_bot_link():
    title = build_pack_title("Мой мем", BOT, branded=True, suffix="| @{bot}")
    assert f"@{BOT}" in title


def test_unbranded_title_stays_clean():
    title = build_pack_title("Мой мем", BOT, branded=False, suffix="| @{bot}")
    assert "@" not in title


def test_long_title_is_truncated_but_keeps_the_link():
    title = build_pack_title("А" * 200, BOT, branded=True, suffix="| @{bot}")
    assert len(title) <= MAX_TITLE_LEN
    assert title.endswith(f"| @{BOT}")


def test_empty_title_falls_back_to_a_default():
    assert build_pack_title("   ", BOT, branded=False, suffix="") == "Emoji pack"


@pytest.mark.parametrize("branded", [True, False])
def test_title_never_exceeds_the_limit(branded):
    title = build_pack_title("x" * 300, BOT, branded=branded, suffix="| @{bot}")
    assert len(title) <= MAX_TITLE_LEN
