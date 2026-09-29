"""What the corpus cannot say about this validator: a rule's reach, and why a document fails.

The corpus is where most of this validator is exercised: `fixtures/invalid/` holds a
document per rule and `divejson conform fixtures --strict` runs every one of them, which
`test_conform.py` does inside this suite. What that cannot reach is a rule's *reach* — §3's
rule 3 is quantified over every series in a recording, so a channel missing from the
validator's list is one whose defect no `invalid/` document would ever show, there being no
fixture for a defect nobody checks. Nor can it say *why* an invalid document fails, only
that it does.

So these, and deliberately nothing else: the channels §6.4 added, the gradient-factor
ordering §3 rule 6 states, what §3 rule 4 accepts as a recording's content, the one member
that may hold a date as well as a date-time, the member order the validator does not
check — no `invalid/` document can pin an absence of a rule — the one uuid claim whose
fixture is refused for another reason by any validator that does not know its member, the
hosts a contact reference sits on, the corpus holding a dangling one on a trip part alone,
the hosts a person reference sits on, the corpus holding its defects on a dive and a
certification alone, what a sighting's two defects are reported as, the collections the
validator walks, which the schema can gain one of without it, and what rule 8 counts as one
tag — a fold one fixture cannot sweep.
Everything already covered by a pair stays covered by the pair.
"""

from __future__ import annotations

import json

import pytest
from helpers import FIXTURES

from divejson.validate import (
    CHANNELS,
    COLLECTIONS,
    READOUTS,
    WHITE_SPACE,
    validate_document,
)


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


def test_the_validators_collection_list_is_the_schemas() -> None:
    """The same drift as the channels', and as silent: a collection the schema gains and the
    list does not is one whose duplicate uuids nothing sees and whose `created_at` nothing
    checks, and no document fails for it."""
    from divejson.validate import load_schema

    properties = load_schema()["properties"]
    assert set(COLLECTIONS) == {name for name, definition in properties.items() if definition.get("type") == "array"}


def test_a_gradient_factor_low_above_the_high_is_rejected() -> None:
    """§3 rule 6, and UDDF's own constraint on the same pair. The schema makes them
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


def test_a_portrait_shares_the_documents_one_identifier_space() -> None:
    """§5.3: a Stored File's uuid is claimed like any record's, and the portrait's is claimed
    beside the diver's. The fixture is schema-valid, so this is the only thing refusing it."""
    doc = json.loads((FIXTURES / "invalid" / "duplicate-file-uuid-portrait.divejson").read_text(encoding="utf-8"))
    assert [str(issue) for issue in validate_document(doc)] == [
        "certifications/0/front_file: uuid 0198a6f0-1111-7081-8000-000000000081 already used at diver/portrait_file"
    ]


# -- §3 rule 4: what a recording carries -------------------------------------------------


@pytest.mark.parametrize("readout", READOUTS)
def test_a_readout_alone_is_a_recording(readout) -> None:
    """A figure the device computed is a record of the dive nothing else produces (§6.4a) —
    the CNS a diver copied off their computer into a hand-kept log."""
    value = 1.0 if readout == "surface_pressure" else 0
    assert issues({readout: value}) == []


def test_a_setting_alone_is_not() -> None:
    """`mode`, `deco_model` and `salinity` describe how a computer was set, and a setting
    nothing recorded a dive with is not a record of one."""
    assert issues({"mode": "gauge", "salinity": "en13319", "deco_model": {"conservatism": 0}}) == [
        (
            "dives/0/recordings/0: a recording carries at least one of device, profile, source_files and a "
            "readout — surface_pressure, cns_start, cns_end, otu_start, otu_end (spec §3, §6.4a)"
        )
    ]


def test_the_readout_list_is_the_recordings_own() -> None:
    """Every readout is a member of the recording and of nothing else a dive carries, so the
    list and the schema cannot drift apart silently in either direction."""
    from divejson.validate import load_schema

    defs = load_schema()["$defs"]
    assert set(READOUTS) <= set(defs["recording"]["properties"])
    assert not set(READOUTS) & set(defs["dive"]["properties"])


# -- a dive's start may be its date --------------------------------------------------------


def _with_start(started_at: str) -> list[str]:
    doc = document({"device": {"model": "Perdix 3"}})
    doc["dives"][0]["started_at"] = started_at
    return [str(issue) for issue in validate_document(doc)]


def test_a_dive_may_start_on_a_date_alone() -> None:
    assert _with_start("2026-07-05") == []


def test_a_date_that_is_not_one_is_refused() -> None:
    assert "dives/0/started_at: '2026-02-30' is not a real calendar date" in _with_start("2026-02-30")


def test_a_recordings_own_start_takes_a_date_time_and_nothing_else() -> None:
    """§5.2: no other date-time member takes a date — a recording that knows its own start
    knows its instant."""
    found = issues({"device": {"model": "Perdix 3"}, "started_at": "2026-07-05"})
    assert "dives/0/recordings/0/started_at: '2026-07-05' is not a DiveJSON date-time" in found


# -- §4's member order is a SHOULD -------------------------------------------------------------


def test_a_document_in_any_member_order_conforms() -> None:
    """A generic re-serialisation — Go's `encoding/json` sorting a map's keys — writes
    `exported_at` before `format`, and the document it wrote is as conforming as the one it
    read (§4)."""
    doc = document({"device": {"model": "Perdix 3"}})
    assert validate_document(dict(sorted(doc.items()))) == []


# -- §3 rule 1 reaches every contact reference --------------------------------------------------

CONTACT = "0198a6f0-9999-7020-8000-000000000020"
NOWHERE = "0198a6f0-9999-7029-8000-000000000029"


def _referencing(host: str, record: dict) -> dict:
    return {
        "format": "divejson",
        "version": "1.0",
        "exported_at": "2026-09-05T00:00:00+00:00",
        "gear": [{"uuid": "0198a6f0-9999-7004-8000-000000000004", "name": "Regulator", "type": "regulator"}],
        "contacts": [{"uuid": CONTACT, "name": "Blue Hole Divers"}],
        host: [record],
    }


REFERENCING = {
    "dives": {"uuid": "0198a6f0-9999-7001-8000-000000000001", "started_at": "2026-04-17T11:49:23+02:00"},
    "courses": {"uuid": "0198a6f0-9999-7022-8000-000000000022", "name": "AOW"},
    "certifications": {"uuid": "0198a6f0-9999-7023-8000-000000000023", "agency": "padi", "name": "AOW"},
    "gear_service_records": {
        "uuid": "0198a6f0-9999-7024-8000-000000000024",
        "gear_uuid": "0198a6f0-9999-7004-8000-000000000004",
        "type": "service",
        "serviced_on": "2026-03-05",
    },
}


@pytest.mark.parametrize("host", REFERENCING)
def test_a_contact_uuid_resolves_in_contacts_on_every_host(host: str) -> None:
    assert validate_document(_referencing(host, {**REFERENCING[host], "contact_uuid": CONTACT})) == []
    dangling = _referencing(host, {**REFERENCING[host], "contact_uuid": NOWHERE})
    found = [str(issue) for issue in validate_document(dangling)]
    assert found == [f"{host}/0/contact_uuid: references {NOWHERE}, not present in contacts"]


def test_a_parts_accommodation_resolves_in_contacts_though_it_is_not_named_after_them() -> None:
    """§5.3: a member not named after its collection resolves where its definition says."""
    trip = {
        "uuid": "0198a6f0-9999-7003-8000-000000000003",
        "name": "Spring",
        "parts": [{"accommodation_uuid": CONTACT}],
    }
    assert validate_document(_referencing("trips", trip)) == []
    trip["parts"].append({"accommodation_uuid": NOWHERE})
    assert [str(issue) for issue in validate_document(_referencing("trips", trip))] == [
        f"trips/0/parts/1/accommodation_uuid: references {NOWHERE}, not present in contacts"
    ]


# -- §3 rule 1 reaches every person reference, and a person is on a host once -------------------

PERSON = "0198a6f0-9999-7030-8000-000000000030"
PEOPLE_HOSTS = {host: REFERENCING[host] for host in ("dives", "courses")} | {
    "trips": {"uuid": "0198a6f0-9999-7003-8000-000000000003", "name": "Spring"},
}


def _with_people(host: str, references: list[dict]) -> dict:
    document = _referencing(host, {**PEOPLE_HOSTS[host], "people": references})
    document["people"] = [{"uuid": PERSON, "name": "Ada Lovelace"}]
    return document


@pytest.mark.parametrize("host", PEOPLE_HOSTS)
def test_a_person_reference_resolves_in_people_on_every_host(host: str) -> None:
    assert validate_document(_with_people(host, [{"person_uuid": PERSON, "role": "buddy"}])) == []
    dangling = _with_people(host, [{"person_uuid": PERSON}, {"person_uuid": NOWHERE, "role": "guide"}])
    assert [str(issue) for issue in validate_document(dangling)] == [
        f"{host}/0/people/1/person_uuid: references {NOWHERE}, not present in people"
    ]


@pytest.mark.parametrize("host", PEOPLE_HOSTS)
def test_a_person_is_listed_on_a_host_once_whatever_the_roles_say(host: str) -> None:
    """Two references differing in their role are two objects to `uniqueItems`, so the schema
    passes them and only this rule refuses them."""
    twice = _with_people(host, [{"person_uuid": PERSON, "role": "buddy"}, {"person_uuid": PERSON, "role": "guide"}])
    assert [str(issue) for issue in validate_document(twice)] == [
        f"{host}/0/people/1: person {PERSON} is already listed at {host}/0/people/0"
    ]


def test_a_certifications_instructor_resolves_in_people_though_it_is_not_named_after_them() -> None:
    card = {**REFERENCING["certifications"], "instructor_uuid": PERSON}
    document = _referencing("certifications", card)
    document["people"] = [{"uuid": PERSON, "name": "J. Harbord"}]
    assert validate_document(document) == []
    card["instructor_uuid"] = NOWHERE
    assert [str(issue) for issue in validate_document(document)] == [
        f"certifications/0/instructor_uuid: references {NOWHERE}, not present in people"
    ]


def test_a_person_shares_the_documents_one_identifier_space() -> None:
    document = _with_people("dives", [])
    document["people"][0]["uuid"] = CONTACT
    assert [str(issue) for issue in validate_document(document)] == [
        f"people/0: uuid {CONTACT} already used at contacts/0"
    ]


# -- §3 rule 1 reaches a dive's sightings, and a species is on a dive once -----------------------

SPECIES = "0198a6f0-9999-7040-8000-000000000040"


def _with_sightings(*sightings: list[dict]) -> dict:
    """One dive per list of sightings, and the one species they can reference."""
    dives = [
        {
            "uuid": f"0198a6f0-9999-7001-8000-00000000000{index}",
            "started_at": "2026-04-17T11:49:23+02:00",
            "sightings": listed,
        }
        for index, listed in enumerate(sightings)
    ]
    return {
        "format": "divejson",
        "version": "1.0",
        "exported_at": "2026-09-05T00:00:00+00:00",
        "dives": dives,
        "species": [{"uuid": SPECIES, "scientific_name": "Pterois volitans"}],
    }


def test_a_sighting_resolves_in_species() -> None:
    assert validate_document(_with_sightings([{"species_uuid": SPECIES, "count": 3, "notes": "Under the wreck"}])) == []
    dangling = _with_sightings([{"species_uuid": SPECIES}, {"species_uuid": NOWHERE}])
    assert [str(issue) for issue in validate_document(dangling)] == [
        f"dives/0/sightings/1/species_uuid: references {NOWHERE}, not present in species"
    ]


def test_a_species_is_sighted_on_a_dive_once_whatever_the_counts_say() -> None:
    """Two sightings differing in their count are two objects to `uniqueItems`, so the schema
    passes them and only this rule refuses them."""
    twice = _with_sightings([{"species_uuid": SPECIES, "count": 3}, {"species_uuid": SPECIES, "count": 1}])
    assert [str(issue) for issue in validate_document(twice)] == [
        f"dives/0/sightings/1: species {SPECIES} is already listed at dives/0/sightings/0"
    ]


def test_one_species_sighted_on_two_dives_is_two_sightings() -> None:
    """The rule is per list: a lionfish seen on Monday and on Tuesday was seen twice."""
    assert validate_document(_with_sightings([{"species_uuid": SPECIES}], [{"species_uuid": SPECIES}])) == []


# -- §3 rule 8: a tag is on a dive once, compared trimmed and case-folded ------------------------


def _with_tags(*tags: list[str]) -> dict:
    """One dive per list of tags."""
    dives = [
        {"uuid": f"0198a6f0-9999-7001-8000-00000000000{index}", "started_at": "2026-04-17T11:49:23+02:00", "tags": listed}
        for index, listed in enumerate(tags)
    ]
    return {"format": "divejson", "version": "1.0", "exported_at": "2026-09-05T00:00:00+00:00", "dives": dives}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("night", "Night"),
        # Full case folding, not lowercasing: `lower()` keeps the ß, and the two stay apart.
        ("Großes Riff", "GROSSES RIFF"),
        # Trimmed, and inner whitespace is the diver's.
        ("night", " night　"),
    ],
)
def test_a_tag_twice_on_a_dive_is_refused_where_the_schema_sees_two(first: str, second: str) -> None:
    """`uniqueItems` compares bytes, so only this rule refuses these."""
    assert [str(issue) for issue in validate_document(_with_tags([first, second]))] == [
        (
            f"dives/0/tags/1: tag {second!r} is already listed at dives/0/tags/0, compared trimmed and "
            "case-folded (spec §3 rule 8)"
        )
    ]


@pytest.mark.parametrize(
    "tags",
    [
        ["night", "night dive"],
        # U+001C is not White_Space, though `str.strip()` strips it.
        ["night", "\x1cnight"],
    ],
)
def test_tags_that_differ_after_the_fold_are_two_tags(tags: list[str]) -> None:
    assert validate_document(_with_tags(tags)) == []


def test_one_tag_on_two_dives_is_two_tags() -> None:
    """The rule is per dive: two night dives both say `night`."""
    assert validate_document(_with_tags(["night"], ["Night"])) == []


def test_the_trim_is_unicodes_white_space() -> None:
    """Every code point Python calls whitespace, less the four information separators it adds."""
    python = {chr(point) for point in range(0x110000) if chr(point).isspace()}
    assert set(WHITE_SPACE) == python - {"\x1c", "\x1d", "\x1e", "\x1f"}
