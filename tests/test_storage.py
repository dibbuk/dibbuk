"""Тесты тарифных правил: квота, брендирование, подписка."""

from datetime import date, timedelta

from bot.storage import Storage, UserSettings, UserState


def test_free_user_gets_branding():
    assert UserState().should_brand()


def test_paid_credit_removes_branding_once():
    state = UserState(unbrand_credits=1)
    assert not state.should_brand()
    state.consume_unbrand()
    assert state.should_brand()


def test_subscription_removes_branding_without_spending_credits():
    state = UserState(subscription_until=date.today() + timedelta(days=5))
    assert not state.should_brand()
    state.consume_unbrand()
    assert not state.should_brand()


def test_expired_subscription_stops_working():
    state = UserState(subscription_until=date.today() - timedelta(days=1))
    assert not state.is_subscribed
    assert state.should_brand()


def test_quota_shrinks_as_packs_are_made():
    state = UserState()
    assert state.quota_left(5) == 5
    state.packs_today = 3
    assert state.quota_left(5) == 2


def test_quota_never_goes_negative():
    state = UserState(packs_today=99)
    assert state.quota_left(5) == 0


def test_counter_resets_on_a_new_day():
    state = UserState(packs_today=5, counter_day=date.today() - timedelta(days=1))
    assert state.quota_left(5) == 5
    assert state.packs_today == 0


def test_padding_steps_stay_inside_the_allowed_range():
    config = UserSettings()
    for _ in range(50):
        config.with_padding(0.02)
    assert config.padding <= 0.20
    for _ in range(100):
        config.with_padding(-0.02)
    assert config.padding >= -0.20


def test_padding_label_is_readable():
    config = UserSettings()
    assert config.padding_label == "0"
    config.with_padding(-0.04)
    assert config.padding_label.startswith("-")


def test_storage_creates_state_on_first_touch():
    store = Storage()
    assert store.size == 0
    store.get(7)
    store.get(7)
    assert store.size == 1


def test_mosaic_refusal_is_told_apart_from_other_errors():
    from bot.handlers import is_mosaic_forbidden

    assert is_mosaic_forbidden("Bad Request: CUSTOM_EMOJI_INVALID")
    assert is_mosaic_forbidden("Bad Request: not enough rights to send custom emoji")
    assert is_mosaic_forbidden("premium subscription required")
    assert not is_mosaic_forbidden("Bad Request: message text is empty")
    assert not is_mosaic_forbidden("Too Many Requests: retry after 12")


def test_quoted_env_values_survive_systemd_and_compose(monkeypatch):
    """Значение со спецсимволом должно читаться одинаково при любом способе запуска."""
    from bot.config import _env_str

    monkeypatch.setenv("X_SUFFIX", '"| @{bot}"')
    assert _env_str("X_SUFFIX", "") == "| @{bot}"

    monkeypatch.setenv("X_SUFFIX", "'| @{bot}'")
    assert _env_str("X_SUFFIX", "") == "| @{bot}"

    monkeypatch.setenv("X_SUFFIX", "| @{bot}")
    assert _env_str("X_SUFFIX", "") == "| @{bot}"

    monkeypatch.delenv("X_SUFFIX", raising=False)
    assert _env_str("X_SUFFIX", "fallback") == "fallback"
