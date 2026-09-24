"""List-price coverage for models the bundled price catalog does not know."""

import pytest

from bizharness.usage import estimate_cost


def test_gpt6_luna_standard_list_price():
    cost = estimate_cost(
        "openai:gpt-6-luna",
        input_tokens=100_000,
        output_tokens=100_000,
    )
    assert cost == pytest.approx(0.06)


def test_gpt6_luna_cached_input_is_a_tenth():
    cost = estimate_cost(
        "gpt-6-luna",
        input_tokens=100_000,
        output_tokens=0,
        cache_read_tokens=100_000,
    )
    assert cost == pytest.approx(0.001)


def test_gpt6_luna_long_context_uses_the_higher_band():
    cost = estimate_cost(
        "gpt-6-luna",
        input_tokens=300_000,
        output_tokens=100_000,
    )
    assert cost == pytest.approx(300_000 / 1_000_000 * 0.20 + 100_000 / 1_000_000 * 0.75)


def test_profile_price_overrides_the_builtin():
    cost = estimate_cost(
        "gpt-6-luna",
        input_tokens=100_000,
        output_tokens=0,
        overrides={"gpt-6-luna": {"input": 9.0, "output": 0.0}},
    )
    assert cost == pytest.approx(0.9)
