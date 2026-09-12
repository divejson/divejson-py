"""The two §3 rules the schema cannot express about a recording's decompression data.

The corpus is where most of this validator is exercised: `fixtures/invalid/` holds a
document per rule and `divejson conform fixtures --strict` runs every one of them, which
`test_conform.py` does inside this suite. What that cannot reach is a rule's *reach* — §3's
rule 3 is quantified over every series in a recording, so a channel missing from the
validator's list is one whose defect no `invalid/` document would ever show, there being no
fixture for a defect nobody checks.

So these two, and deliberately nothing else: the channels §6.4 added, and the gradient-factor
ordering §3 rule 7 states. Everything already covered by a pair stays covered by the pair.
"""

from __future__ import annotations

import pytest

from divejson.validate import CHANNELS, validate_document


def document(recording: dict) -> dict:
    """The smallest conforming document whose one dive carries `recording`."""
    return {
        "format": "divejson",
        "version": "1.0",
        "exported_at": "2026-09-05T00:00:00+00:00",
        "dives": [
            {
                "uuid": "0198a6f0-9999-7001-8000-000000000001",
                "started_at": "2026-04-17T11:49:23+02:00",
                "recordings": [recording],
            }
        ],
    }


def issues(recording: dict) -> list[str]:
    return [str(issue) for issue in validate_document(document(recording))]


@pytest.mark.parametrize("channel", CHANNELS)
def test_every_channel_the_profile_defines_is_checked_for_strictly_increasing_times(channel) -> None:
    """§6.5 requires it of every series, and a channel the validator's list forgets is one
    where a reader's ordering bug produces a document this package calls conforming."""
    profile = {"duration": 60, channel: {"times": [0, 60, 30], "values": [1, 2, 3]}}
    assert any("times is not strictly increasing" in issue for issue in issues({"profile": profile}))


@pytest.mark.parametrize("channel", CHANNELS)
def test_every_channel_is_covered_by_the_profiles_duration(channel) -> None:
    """§6.4 defines `duration` as the span of the samples, across the channels rather than
    down one of them — a `tts` reaching past the last depth sample is still a sample."""
    profile = {"duration": 60, channel: {"times": [900], "values": [1]}}
    assert any("does not cover the latest sample at 900" in issue for issue in issues({"profile": profile}))


def test_the_validators_channel_list_is_the_schemas() -> None:
    """The list and the schema are two places the same set of channels is written down, and
    a member added to one and not the other is silent in exactly one direction: the schema
    would accept the channel and §3's rule 3 would never look at it.
    """
    from divejson.validate import load_schema

    properties = load_schema()["$defs"]["profile"]["properties"]
    series = {
        name
        for name, definition in properties.items()
        if str(definition.get("$ref", "")).endswith(("/series", "/unsigned_series"))
    }
    assert set(CHANNELS) == series


def test_a_gradient_factor_low_above_the_high_is_rejected() -> None:
    """§3 rule 7, and UDDF's own constraint on the same pair. The schema makes them
    both-or-neither and puts each on 0-100; neither of those can say one is not above the
    other, and a low above a high is a decompression model nothing ran."""
    found = issues({"deco_model": {"gf_low": 85, "gf_high": 30}, "device": {"model": "Perdix 3"}})
    assert found == ["dives/0/recordings/0/deco_model: gf_low exceeds gf_high (spec §3, §6.4c)"]


def test_an_equal_pair_is_conforming() -> None:
    """The rule is `gf_low <= gf_high`: a diver who dialled 85/85 ran a model."""
    assert issues({"deco_model": {"gf_low": 85, "gf_high": 85}, "device": {"model": "Perdix 3"}}) == []


def test_the_rule_reaches_every_recording_and_not_only_the_first() -> None:
    """§3's rules are quantified over every recording (§6.4a), and a validator that walked
    `recordings[0]` alone is the shape the section's ordering rule invites."""
    doc = document({"device": {"model": "Perdix 3"}})
    doc["dives"][0]["recordings"].append(
        {"device": {"model": "Suunto Ocean"}, "deco_model": {"gf_low": 85, "gf_high": 30}}
    )
    assert [str(issue) for issue in validate_document(doc)] == [
        "dives/0/recordings/1/deco_model: gf_low exceeds gf_high (spec §3, §6.4c)"
    ]
