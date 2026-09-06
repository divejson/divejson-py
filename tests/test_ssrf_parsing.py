"""What the `.ssrf` reader makes of a Subsurface logbook, element by element.

The corpus in `fixtures/ssrf/` covers whole files as Subsurface writes them; these build
the smallest logbook that exhibits one behaviour, so a test about an identity reads as one
rather than as a diff of two documents.

Two properties of this format shape most of what is below. **A dive has no id**, so its
identity is its position and every dive says so in the report — which makes an archive of
per-dive files the interesting case rather than a corner one. And **nothing anywhere in the
format records a UTC offset**, so `started_at` is a bare wall clock in every document this
reader produces, and it is reported rather than filled in.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from helpers import SSRF_STARTED_AT, one_ssrf_computer, one_ssrf_dive, ssrf

from divejson import (
    ConverterError,
    DoctypeRefusedError,
    MalformedSsrfError,
    SsrfError,
    convert,
    sniff,
)
from divejson.ssrf import SSRF_ID_NAMESPACE


def dive(attributes: str = "", body: str = "", *, sites: str = "") -> dict:
    return convert(one_ssrf_dive(attributes, body, sites=sites)).document["dives"][0]


def messages(conversion) -> list[str]:
    return [note.message for note in conversion.notes]


# -- claiming the file ----------------------------------------------------------------


def test_the_root_element_is_what_claims_a_logbook() -> None:
    """`<divelog>`, not the format id: `ssrf` names the format, the tag names the file."""
    assert sniff(ssrf("<dives/>")[:512]) == "ssrf"


def test_the_program_attribute_is_not_consulted_by_the_sniff() -> None:
    """Subsurface-mobile writes its own name there, and reads identically."""
    assert sniff(ssrf("<dives/>", program="subsurface-mobile")[:512]) == "ssrf"
    assert sniff(ssrf("<dives/>", program="")[:512]) == "ssrf"


def test_a_doctype_is_refused_before_a_single_entity_is_expanded() -> None:
    """Spec §9, inherited from the shared parse target rather than re-implemented here.

    The refusal is not this format's error, so a caller asking "was this bad Subsurface?"
    gets the honest no — while a caller catching everything a converter raises still
    catches it.
    """
    hostile = b"<!DOCTYPE divelog [<!ENTITY a 'b'>]>\n<divelog><dives/></divelog>"
    with pytest.raises(DoctypeRefusedError) as raised:
        convert(hostile, format="ssrf")
    assert isinstance(raised.value, ConverterError)
    assert not isinstance(raised.value, SsrfError)


def test_something_that_is_not_a_logbook_is_refused_by_this_readers_own_error() -> None:
    with pytest.raises(MalformedSsrfError, match="not <divelog>"):
        convert(b"<uddf/>", format="ssrf")
    with pytest.raises(MalformedSsrfError, match="not well-formed"):
        convert(b"this is not XML at all", format="ssrf")


def test_what_the_file_says_about_itself_rides_in_the_provenance_block() -> None:
    """`@version` is the save format's, `@program` the application's; §4's `generator` is
    the converter, which is neither."""
    document = convert(ssrf("<dives/>", program="subsurface", version="3")).document
    assert document["extensions"]["divejson"] == {
        "converted_from": "ssrf",
        "ssrf_version": "3",
        "source_generator": {"name": "subsurface"},
    }
    assert document["generator"]["name"] == "divejson convert"


def test_a_logbook_with_nothing_in_it_still_produces_a_conforming_document() -> None:
    document = convert(ssrf("<settings></settings><divesites></divesites><dives></dives>")).document
    assert "dives" not in document and "sites" not in document


# -- identity -------------------------------------------------------------------------


def test_the_namespace_is_the_one_the_mapping_document_records() -> None:
    """Frozen forever: changing it renumbers every document this reader has ever produced."""
    import uuid

    assert SSRF_ID_NAMESPACE == uuid.uuid5(uuid.NAMESPACE_URL, "https://divejson.org/ns/ssrf")


def test_a_dive_has_no_id_so_its_identity_is_its_position_and_the_report_says_so() -> None:
    conversion = convert(one_ssrf_dive())
    assert any("derived from its position in the file" in message for message in messages(conversion))
    assert [note.kind for note in conversion.notes if "position in the file" in note.message] == ["absent"]


def test_a_dives_identity_moves_when_the_files_order_moves() -> None:
    """Which is exactly what the note above warns a diver about, made visible."""
    first = "<dive date='2026-04-17' time='11:49:23'/>"
    second = "<dive date='2026-04-19' time='08:12:00'/>"
    forwards = convert(ssrf(f"<dives>{first}{second}</dives>")).document["dives"]
    backwards = convert(ssrf(f"<dives>{second}{first}</dives>")).document["dives"]
    assert forwards[0]["uuid"] == backwards[0]["uuid"]
    assert forwards[0]["started_at"] != backwards[0]["started_at"]


def test_a_site_uuid_is_read_as_an_opaque_string_with_its_whitespace_stripped() -> None:
    """Subsurface writes one site id as `" ff47210"`, and the dives pointing at it carry
    the space too — so stripping on one side only would break every reference to it."""
    site = "<site uuid=' ff47210' name='Small Brother'/>"
    spaced = dive("divesiteid=' ff47210'", sites=site)
    bare = dive("divesiteid='ff47210'", sites=site)
    document = convert(one_ssrf_dive("divesiteid=' ff47210'", sites=site)).document
    assert spaced["site_uuids"] == bare["site_uuids"] == [document["sites"][0]["uuid"]]


def test_two_sites_sharing_one_uuid_is_a_source_defect() -> None:
    """Two records cannot share one identity (spec §5.3), so the second is dropped."""
    sites = "<site uuid='same' name='One'/><site uuid='same' name='Two'/>"
    conversion = convert(ssrf(f"<divesites>{sites}</divesites><dives/>"))
    assert [site["name"] for site in conversion.document["sites"]] == ["One"]
    assert any("two records cannot share one identity" in message for message in messages(conversion))


# -- the start time -------------------------------------------------------------------


def test_the_wall_clock_travels_alone_and_the_report_says_so() -> None:
    """`.ssrf` records no offset anywhere, and supplying one is §5.2's whole failure."""
    conversion = convert(one_ssrf_dive())
    assert conversion.document["dives"][0]["started_at"] == "2026-04-17T11:49:23"
    assert any("no UTC offset" in message for message in messages(conversion))


def test_a_time_of_day_with_no_seconds_is_read_as_the_minute_and_reported() -> None:
    """§5.2's grammar requires the seconds, so a hand-edited file would otherwise produce a
    document that fails this converter's own validation and cost the whole logbook."""
    conversion = convert(ssrf("<dives><dive date='2026-04-17' time='11:49'/></dives>"))
    assert conversion.document["dives"][0]["started_at"] == "2026-04-17T11:49:00"
    assert any("records no seconds" in message for message in messages(conversion))


def test_a_dive_with_no_date_is_dropped_because_the_format_requires_a_start_time() -> None:
    conversion = convert(ssrf("<dives><dive number='1' time='08:00:00'/></dives>"))
    assert "dives" not in conversion.document
    assert any("the dive is dropped" in message for message in messages(conversion))


@pytest.mark.parametrize(
    ("date", "clock"),
    [("2026-02-30", "11:49:23"), ("17-04-2026", "11:49:23"), ("2026-04-17", "25:00:00"), ("2026-04-17", "noon")],
)
def test_a_date_and_time_that_are_not_one_drop_the_dive(date: str, clock: str) -> None:
    conversion = convert(ssrf(f"<dives><dive date='{date}' time='{clock}'/></dives>"))
    assert "dives" not in conversion.document
    assert any("are not a date and time" in message for message in messages(conversion))


# -- what is read and deliberately not carried ----------------------------------------


def test_visibility_is_a_star_rating_here_and_metres_in_this_format() -> None:
    """The two readers of one Subsurface export differ on this member by design: the UDDF
    export writes `<visibility>15</visibility>` for the same `5`, in metres."""
    conversion = convert(one_ssrf_dive("visibility='5'"))
    assert "visibility" not in conversion.document["dives"][0]
    dropped = [note for note in conversion.notes if "five-star rating" in note.message]
    assert [note.kind for note in dropped] == ["dropped"]


def test_a_surface_air_consumption_has_no_member_and_is_reported() -> None:
    conversion = convert(one_ssrf_dive("sac='6.471 l/min'"))
    assert any("surface air consumption" in message for message in messages(conversion))


def test_the_dive_computer_model_is_the_telemetry_source_rather_than_owned_gear() -> None:
    """§6.12 has a `computer` gear type, and this is not an item the diver's kit list holds."""
    conversion = convert(one_ssrf_dive(body="<divecomputer model='Suunto Ocean'/>"))
    assert "gear" not in conversion.document
    assert any("names the source of this dive's telemetry" in message for message in messages(conversion))


def test_a_dive_with_two_computers_keeps_one_profile_and_reports_the_rest() -> None:
    body = (
        "<divecomputer><depth max='18.4 m'/><sample time='0:00 min' depth='0.0 m'/></divecomputer>"
        "<divecomputer><depth max='99.0 m'/><sample time='0:00 min' depth='9.0 m'/></divecomputer>"
    )
    conversion = convert(one_ssrf_dive(body=body))
    found = conversion.document["dives"][0]
    assert found["max_depth"] == 18.4
    assert found["profile"]["depth"]["values"] == [0]
    assert any("more than one dive computer's record" in message for message in messages(conversion))


# -- sites and the references to them -------------------------------------------------


def test_a_site_with_no_name_goes_and_takes_the_references_to_it() -> None:
    """§6.10 makes the name REQUIRED, and §5.3 forbids a dangling reference."""
    conversion = convert(one_ssrf_dive("divesiteid='s1'", sites="<site uuid='s1'/>"))
    assert "sites" not in conversion.document
    assert "site_uuids" not in conversion.document["dives"][0]
    assert any("could not carry" in message for message in messages(conversion))


def test_a_reference_to_a_site_nothing_defines_is_reported_as_the_source_defect_it_is() -> None:
    """Told apart from the case above, because a diver looking for a typo deserves to know
    which of the two they have."""
    conversion = convert(one_ssrf_dive("divesiteid='deadbeef'", sites="<site uuid='s1' name='Blue Hole'/>"))
    assert any("<divesites> does not define" in message for message in messages(conversion))


# -- cylinders ------------------------------------------------------------------------


def test_a_cylinder_with_no_gas_is_not_air() -> None:
    """§6.3: absent oxygen is not recorded, never 21."""
    conversion = convert(one_ssrf_dive(body="<cylinder size='12.0 l' start='200.0 bar' end='80.0 bar'/>"))
    found = conversion.document["dives"][0]["cylinders"][0]
    assert "oxygen" not in found and found["volume"] == 12.0
    assert any("never air" in message for message in messages(conversion))


def test_a_cylinder_with_a_gas_and_no_vessel_keeps_the_gas() -> None:
    """The shape §6.3 names in as many words: a cylinder with its vessel members absent."""
    found = dive(body="<cylinder o2='32.0%'/>")["cylinders"][0]
    assert found == {"oxygen": 32.0}


def test_a_zero_start_pressure_is_a_devices_absent_marker() -> None:
    """§6.3 says writers must not emit one, so it is read as not recorded rather than as an
    empty cylinder — while a zero *end* pressure is a real "breathed it dry"."""
    conversion = convert(one_ssrf_dive(body="<cylinder start='0.0 bar' end='0.0 bar'/>"))
    found = conversion.document["dives"][0]["cylinders"][0]
    assert "start_pressure" not in found
    assert found["end_pressure"] == 0.0
    absent = [note for note in conversion.notes if "devices write to mean 'not recorded'" in note.message]
    assert [note.kind for note in absent] == ["absent"]


def test_an_end_pressure_above_the_start_cannot_be_and_is_dropped() -> None:
    found = dive(body="<cylinder start='80.0 bar' end='200.0 bar'/>")["cylinders"][0]
    assert found == {"start_pressure": 80.0}


def test_a_pressure_past_the_formats_ceiling_is_dropped() -> None:
    conversion = convert(one_ssrf_dive(body="<cylinder start='4000.0 bar' end='60.0 bar'/>"))
    assert "start_pressure" not in conversion.document["dives"][0]["cylinders"][0]
    assert any("outside the 0 to 350" in message for message in messages(conversion))


def test_a_mix_summing_past_a_hundred_percent_loses_both_halves() -> None:
    conversion = convert(one_ssrf_dive(body="<cylinder o2='71.0%' he='40.0%'/>"))
    assert conversion.document["dives"][0]["cylinders"][0] == {}
    assert any("sum above 100 percent" in message for message in messages(conversion))


def test_a_dive_carries_no_gas_numbering_because_nothing_in_the_file_needs_one() -> None:
    """§6.3 calls `gas_number` a label rather than an array index, so a converter does not
    assert a numbering where no channel and no switch depends on it."""
    found = dive(body="<cylinder size='12.0 l'/><cylinder size='7.0 l' o2='50.0%'/>")["cylinders"]
    assert all("gas_number" not in cylinder for cylinder in found)


# -- the profile ----------------------------------------------------------------------


def test_a_channel_is_not_padded_to_another_channels_length() -> None:
    """Subsurface writes a depth on every sample and a temperature only when it changes, so
    the two sit on their own axes — 431 depths beside 29 temperatures on the real export."""
    samples = (
        "<sample time='0:00 min' depth='1.45 m' temp='24.4 C'/>"
        "<sample time='0:10 min' depth='1.83 m'/>"
        "<sample time='0:20 min' depth='2.22 m'/>"
    )
    found = convert(one_ssrf_computer(samples)).document["dives"][0]["profile"]
    assert found["depth"]["times"] == [0, 10, 20]
    assert found["temperature"]["times"] == [0]


def test_a_sample_with_no_time_has_no_place_on_the_axis() -> None:
    conversion = convert(
        one_ssrf_computer("<sample time='0:00 min' depth='1.0 m'/><sample depth='9.0 m'/>")
    )
    assert conversion.document["dives"][0]["profile"]["depth"]["values"] == [100]
    assert any("no place on the profile's time axis" in message for message in messages(conversion))


def test_two_samples_on_one_second_keep_the_first() -> None:
    """§6.5's `times` are strictly increasing, and a source's are not."""
    conversion = convert(
        one_ssrf_computer("<sample time='0:30 min' depth='6.2 m'/><sample time='0:30 min' depth='6.4 m'/>")
    )
    assert conversion.document["dives"][0]["profile"]["depth"]["values"] == [620]
    assert any("share the second 30" in message for message in messages(conversion))


def test_samples_are_ordered_by_their_own_recorded_time() -> None:
    """Never by position: §6.5 requires increasing times and no writer guarantees its order."""
    samples = "<sample time='1:00 min' depth='9.0 m'/><sample time='0:00 min' depth='1.0 m'/>"
    found = convert(one_ssrf_computer(samples)).document["dives"][0]["profile"]
    assert found["depth"]["times"] == [0, 60]
    assert found["depth"]["values"] == [100, 900]


def test_samples_carrying_a_time_and_no_reading_produce_no_profile_at_all() -> None:
    """Rather than one with a bare `duration: 0`, which is a claim the source did not make."""
    conversion = convert(one_ssrf_computer("<sample time='0:00 min'/><sample time='1:00 min'/>"))
    assert "profile" not in conversion.document["dives"][0]
    assert any("no profile at all" in message for message in messages(conversion))


def test_a_dive_that_recorded_no_samples_says_nothing_at_all() -> None:
    """Unlike the case above: there the source recorded a profile this reader could not
    carry, here it recorded none."""
    conversion = convert(one_ssrf_computer("<depth max='18.4 m'/>"))
    assert "profile" not in conversion.document["dives"][0]
    assert not any("profile" in message for message in messages(conversion))


def test_the_profiles_duration_is_the_span_of_its_own_samples() -> None:
    """§6.4 defines it that way, so it is read off them rather than off `@duration` — and it
    is structural rather than derived, so it carries no finding of any kind."""
    samples = "<sample time='0:00 min' depth='1.0 m'/><sample time='71:40 min' depth='0.0 m'/>"
    conversion = convert(one_ssrf_dive("duration='66:50 min'", f"<divecomputer>{samples}</divecomputer>"))
    found = conversion.document["dives"][0]
    assert found["duration"] == 4010
    assert found["profile"]["duration"] == 4300


# -- trips ----------------------------------------------------------------------------


def test_a_trips_dives_are_carried_and_the_grouping_is_not() -> None:
    """Walking through the element rather than past it is what keeps a trip's dives from
    disappearing along with the record this reader will not invent dates for."""
    body = (
        "<dives>"
        f"<trip date='2026-07-07' location='Tenerife'><dive {SSRF_STARTED_AT} number='42'/></trip>"
        "<dive date='2026-07-11' time='09:45:00' number='44'/>"
        "</dives>"
    )
    conversion = convert(ssrf(body))
    assert [found["dive_number"] for found in conversion.document["dives"]] == [42, 44]
    assert "trips" not in conversion.document
    dropped = [note for note in conversion.notes if "groups these dives into a trip" in note.message]
    assert [(note.kind, note.where) for note in dropped] == [("dropped", "trip/0")]


# -- an archive of per-dive files -----------------------------------------------------


def test_an_archive_of_logbooks_does_not_collide_two_positional_identities() -> None:
    """The case a format whose dives carry no id makes ordinary rather than exotic.

    Every `.ssrf` dive is identified by its position, so two members of one archive would
    hand their first dives one UUID if the stand-in were not prefixed by the member name.
    The document would then fail its own validation on a duplicate uuid, which is how this
    would have been found — but only after an archive had been converted.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.ssrf", ssrf("<dives><dive date='2026-04-17' time='11:49:23'/></dives>"))
        archive.writestr("b.ssrf", ssrf("<dives><dive date='2026-04-19' time='08:12:00'/></dives>"))

    document = convert(buffer.getvalue()).document
    uuids = [found["uuid"] for found in document["dives"]]
    assert len(uuids) == len(set(uuids)) == 2
