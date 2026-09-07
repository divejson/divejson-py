"""What the DM5 reader decides, one behaviour per test.

`docs/suunto-xml-mapping.md` is the prose these assert. The file is grouped the way that
document is: what a nil element means, what makes a document a dive this reader carries,
the dive's own scalars, the cylinders, the profile, and what the reader is asked to claim.

The unit factors are in `test_suunto_xml_units.py` and the whole-file answers in
`test_suunto_xml_fixtures.py`; nothing here duplicates either.
"""

from __future__ import annotations

import pytest
from helpers import EXPORTED_AT, suunto_mixtures, suunto_xml, suunto_xml_sample, suunto_xml_samples

from divejson import convert, sniff
from divejson.converter import DoctypeRefusedError, Scope
from divejson.registry import SNIFF_BYTES
from divejson.suunto_xml import SUUNTO_XML, SUUNTO_XML_ID_NAMESPACE, MalformedSuuntoXmlError


def one(body: str = "", **kwargs: object) -> dict:
    return convert(suunto_xml(body, **kwargs)).document["dives"][0]  # type: ignore[arg-type]


def dives(body: str = "", **kwargs: object) -> list[dict]:
    return convert(suunto_xml(body, **kwargs)).document.get("dives", [])  # type: ignore[arg-type]


def notes(body: str = "", **kwargs: object) -> list:
    return list(convert(suunto_xml(body, **kwargs)).notes)  # type: ignore[arg-type]


def reported(body: str, fragment: str, kind: str | None = None) -> bool:
    return any(
        fragment in note.message and (kind is None or note.kind == kind) for note in notes(body)
    )


# -- absence, which this serializer spells out ----------------------------------------


def test_a_nil_element_is_not_recorded_rather_than_zero() -> None:
    """The whole contract is emitted on every document, so presence says nothing.

    Read as text, `<MaxDepth i:nil="true" />` is empty and `<MaxDepth>0</MaxDepth>` is a
    zero, and the two mean the same thing here for different reasons — but a reader that
    treated the nil as a *reading* would put every dive in a logbook at the surface.
    """
    assert "max_depth" not in one('<MaxDepth i:nil="true" />')


def test_a_nil_element_carrying_text_is_still_not_recorded() -> None:
    """`i:nil` is the format's own word for absence and outranks whatever is inside it.

    No file in hand does this — the serializer never puts content in a nil element — which
    is exactly why the test exists: relying on the emptiness alone would pass every one of
    them and read this as a ceiling.
    """
    found = one(suunto_xml_samples('<Time>10</Time><Ceiling i:nil="true">0</Ceiling>'))
    assert "profile" not in found or "ceiling" not in found["profile"]


def test_an_unrecorded_member_is_silent_rather_than_reported() -> None:
    """A line per unfilled member would be the datacontract restated once per dive.

    Every one of the 384 exports in hand carries all seventy-odd elements, most of them
    nil, so the report would drown in them.
    """
    assert notes() == [
        note for note in notes() if "no UTC offset" in note.message or "no id" in note.message
    ]


def test_text_that_is_present_and_unreadable_is_reported() -> None:
    """The other side of the rule above: something was written and could not be carried."""
    assert reported("<MaxDepth>quite deep</MaxDepth>", "<MaxDepth> is not a number", "dropped")


# -- what this reader carries ---------------------------------------------------------


def test_a_freedive_is_skipped_and_reported() -> None:
    """`<Mode>3</Mode>`: DiveJSON has no member for the kind of a dive.

    Carrying it would put a freedive in a logbook indistinguishable from a scuba dive with
    no gas and no algorithm — 42 of them, in the corpus this was built against.
    """
    conversion = convert(suunto_xml("<Mode>3</Mode>"))
    assert "dives" not in conversion.document
    assert any(note.kind == "dropped" and "freedive" in note.message for note in conversion.notes)


@pytest.mark.parametrize("mode", ["<Mode>0</Mode>", "<Mode>1</Mode>", '<Mode i:nil="true" />', ""])
def test_every_other_mode_is_a_dive_this_reader_carries(mode: str) -> None:
    """0 and 1 are air and nitrox; a document that states none makes no claim at all.

    `converting.md`'s first rule is that schema validity is never a precondition, so a
    missing `<Mode>` is read on rather than refused.
    """
    assert len(dives(mode)) == 1


def test_a_document_with_no_start_time_is_dropped() -> None:
    """§6.2 makes `started_at` REQUIRED and §5.4 forbids inventing one."""
    conversion = convert(suunto_xml(started_at=None))
    assert "dives" not in conversion.document
    assert any("no <StartTime>" in note.message for note in conversion.notes)


# -- the start time -------------------------------------------------------------------


def test_the_sub_second_fraction_is_preserved() -> None:
    """380 of the 384 exports in hand write one, and §5.2 makes it OPTIONAL not forbidden."""
    assert one(started_at="2021-04-06T11:16:42.6")["started_at"] == "2021-04-06T11:16:42.6"


def test_no_utc_offset_is_supplied_and_the_absence_is_reported() -> None:
    """The file records no zone anywhere, and the same dive's app JSON records `+02:00`.

    Taking it from there would be this converter asserting a zone this file does not, which
    is the failure §5.2 exists to prevent.
    """
    found = convert(suunto_xml(started_at="2021-04-06T11:16:42.6"))
    assert "+" not in found.document["dives"][0]["started_at"]
    assert any(note.kind == "absent" and "no UTC offset" in note.message for note in found.notes)


def test_a_recorded_offset_is_not_a_shape_this_reader_accepts() -> None:
    """The grammar is §5.2's minus the offset, because this writer has no zone to state.

    A `<StartTime>` carrying one is not something this serializer produces, and reading it
    would be this reader inventing a dialect rather than following a file.
    """
    conversion = convert(suunto_xml(started_at="2021-04-06T11:16:42.6+02:00"))
    assert "dives" not in conversion.document
    assert any("not a date and time" in note.message for note in conversion.notes)


def test_a_time_of_day_with_no_seconds_is_read_as_zero_and_reported() -> None:
    """§5.2's grammar requires the seconds; refusing here would cost the whole dive."""
    conversion = convert(suunto_xml(started_at="2025-03-27T12:06"))
    assert conversion.document["dives"][0]["started_at"] == "2025-03-27T12:06:00"
    assert any(note.kind == "absent" and "no seconds" in note.message for note in conversion.notes)


@pytest.mark.parametrize("written", ["2021-02-30T11:16:42", "not a time", "11:16:42", "2021-04-06"])
def test_a_start_time_that_is_not_one_drops_the_dive(written: str) -> None:
    """The calendar check is the one no pattern can make: 30 February matches and is not a date."""
    assert dives(started_at=written) == []


# -- the dive's own scalars -----------------------------------------------------------


def test_the_duration_is_the_logged_period_and_not_the_bottom_time() -> None:
    """`<BottomTime>` is the time at depth and runs a third shorter.

    `<DiveTime>`, which would be the in-water figure the app JSON's reader prefers, is
    `i:nil` on every one of the 384 exports in hand.
    """
    found = one("<Duration>2001</Duration><BottomTime>1028</BottomTime>")
    assert found["duration"] == 2001


def test_a_duration_below_a_whole_second_is_read_as_not_recorded() -> None:
    """Rounded before its constraint is asked about, or a 0.4 s dive fails our own validation.

    §6.2 admits a positive whole number of seconds, and 0.4 is above the schema's floor
    while the integer written from it is not.
    """
    conversion = convert(suunto_xml("<Duration>0.4</Duration>"))
    assert "duration" not in conversion.document["dives"][0]
    assert any(note.kind == "absent" and "<Duration>" in note.message for note in conversion.notes)


def test_a_zero_depth_is_a_placeholder_and_a_zero_oxygen_clock_is_a_reading() -> None:
    """Which way a zero reads follows the member's own constraint, not this module.

    §6.2 gives `max_depth` an exclusive floor and `cns_start` an inclusive one, and the
    difference is a dive at the surface against a diver's first dive of the day.
    """
    found = one("<MaxDepth>0</MaxDepth><CnsStart>0</CnsStart>")
    assert "max_depth" not in found
    assert found["cns_start"] == 0.0


def test_an_average_deeper_than_the_maximum_drops_the_average() -> None:
    found = one("<MaxDepth>20.5</MaxDepth><AvgDepth>25.1</AvgDepth>")
    assert found["max_depth"] == 20.5
    assert "avg_depth" not in found


def test_a_surface_pressure_outside_the_barometric_range_is_dropped() -> None:
    """Which is also the backstop for reading it as Pascal rather than millibar."""
    assert "surface_pressure" not in one("<SurfacePressure>1063</SurfacePressure>")


def test_the_computers_own_dive_counter_is_read_and_not_carried() -> None:
    """`<DiveNumberInSerie>` restarts on a new or reset device.

    Carrying it would stamp a dive #1 onto somebody's three-hundredth dive, so it is
    reported rather than mapped onto §6.2's `dive_number`.
    """
    found = convert(suunto_xml("<DiveNumberInSerie>5</DiveNumberInSerie>"))
    assert "dive_number" not in found.document["dives"][0]
    assert any(note.kind == "dropped" and "<DiveNumberInSerie>" in note.message for note in found.notes)


@pytest.mark.parametrize("name", ["Visibility", "Weather", "Weight"])
def test_the_dive_conditions_panel_is_read_and_refused(name: str) -> None:
    """Three readings in units the export never states, arriving as one block.

    25 of the 384 exports in hand carry all three and every recorded value is `0`.
    """
    found = convert(suunto_xml(f"<{name}>0</{name}>"))
    assert set(found.document["dives"][0]) == {"uuid", "started_at"}
    assert any(note.kind == "dropped" and f"<{name}>" in note.message for note in found.notes)


def test_the_note_a_diver_typed_is_carried() -> None:
    assert one("<Note>  Shore entry  </Note>")["notes"] == "Shore entry"


# -- cylinders ------------------------------------------------------------------------


def test_a_cylinder_with_no_transmitter_has_both_pressures_as_the_absent_marker() -> None:
    """The pair, not one of them: 255 of the corpus's 353 mixtures write `0` for both.

    §6.3 settles the start outright. Reading the end alone as `0.0` would put "breathed the
    cylinder to nothing" on every untransmitted tank in a logbook.
    """
    found = convert(suunto_xml(suunto_mixtures("<StartPressure>0</StartPressure><EndPressure>0</EndPressure>")))
    assert found.document["dives"][0]["cylinders"][0] == {}
    assert any(note.kind == "absent" and "both 0 bar" in note.message for note in found.notes)


def test_a_zero_end_pressure_beside_a_recorded_start_is_a_reading() -> None:
    """The other half of the rule above, which §6.3's `minimum: 0` asks for.

    No DM5 export in hand writes this shape — the zeroes always arrive as a pair — so the
    branch is pinned here rather than in a fixture that would claim to be modelled on one.
    """
    found = convert(
        suunto_xml(suunto_mixtures("<StartPressure>200000</StartPressure><EndPressure>0</EndPressure>"))
    ).document["dives"][0]["cylinders"][0]
    assert found == {"start_pressure": 200.0, "end_pressure": 0.0}


def test_an_end_pressure_above_the_start_is_dropped() -> None:
    """One dive in the corpus does this: a pod reading 0.188 bar at the start."""
    found = convert(
        suunto_xml(suunto_mixtures("<StartPressure>188</StartPressure><EndPressure>61875</EndPressure>"))
    )
    assert "end_pressure" not in found.document["dives"][0]["cylinders"][0]
    assert any(note.kind == "dropped" and "above the start pressure" in note.message for note in found.notes)


def test_a_cylinder_with_no_gas_says_so_rather_than_reporting_air() -> None:
    found = convert(suunto_xml(suunto_mixtures("<Size>12</Size>")))
    assert found.document["dives"][0]["cylinders"][0] == {"volume": 12.0}
    assert any(note.kind == "absent" and "never air" in note.message for note in found.notes)


def test_a_mix_summing_past_a_hundred_percent_drops_both_halves() -> None:
    found = convert(suunto_xml(suunto_mixtures("<Oxygen>50</Oxygen><Helium>60</Helium>")))
    assert found.document["dives"][0]["cylinders"][0] == {}
    assert any("sum above 100 percent" in note.message for note in found.notes)


def test_the_mixture_type_is_not_read_as_the_cylinders_role() -> None:
    """`<Type>1</Type>` on all 353 mixtures in hand, back gas and deco bottle alike.

    Whatever it encodes, it is not what the cylinder was carried for, and mapping it would
    confidently label a deco bottle "bottom".
    """
    assert "role" not in one(suunto_mixtures("<Type>1</Type><Oxygen>50</Oxygen>"))["cylinders"][0]


def test_at_most_sixteen_cylinders_are_read_and_the_rest_are_reported() -> None:
    found = convert(suunto_xml(suunto_mixtures(*["<Oxygen>21</Oxygen>"] * 17)))
    assert len(found.document["dives"][0]["cylinders"]) == 16
    assert any(note.kind == "dropped" and "at most 16" in note.message for note in found.notes)


# -- gas switches ---------------------------------------------------------------------


def test_a_gas_switch_names_the_cylinder_it_was_found_in() -> None:
    """`<DiveGasChanges>` is nested inside each `<DiveMixture>`, so no join is needed.

    A marker on the profile and the row in the cylinder list name the same cylinder by
    construction rather than by agreement.
    """
    found = one(
        suunto_mixtures(
            "<DiveGasChanges><DiveGasChange><GasChangeTime>0</GasChangeTime></DiveGasChange></DiveGasChanges>"
            "<Oxygen>21</Oxygen>",
            "<DiveGasChanges><DiveGasChange><GasChangeTime>1592</GasChangeTime></DiveGasChange>"
            "</DiveGasChanges><Oxygen>52</Oxygen>",
        )
        + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5"))
    )
    assert found["profile"]["events"] == [
        {"time": 0, "type": "gas_switch", "gas_number": 0},
        {"time": 1592, "type": "gas_switch", "gas_number": 1},
    ]
    assert [cylinder["gas_number"] for cylinder in found["cylinders"]] == [0, 1]


def test_a_switch_at_second_zero_is_kept() -> None:
    """342 of the corpus's 363 switches are at 0, and §6.5 gives an event time a floor of 0.

    On a single-gas dive it is the one marker saying the dive was breathed on that gas
    throughout, and the samples of most of these exports begin at second 1 regardless.
    """
    found = one(
        suunto_mixtures(
            "<DiveGasChanges><DiveGasChange><GasChangeTime>0</GasChangeTime></DiveGasChange></DiveGasChanges>"
        )
        + suunto_xml_samples(suunto_xml_sample(1, Depth="1.86"))
    )
    assert found["profile"]["events"] == [{"time": 0, "type": "gas_switch", "gas_number": 0}]


def test_a_switch_before_the_dive_began_is_dropped() -> None:
    found = convert(
        suunto_xml(
            suunto_mixtures(
                "<DiveGasChanges><DiveGasChange><GasChangeTime>-30</GasChangeTime></DiveGasChange>"
                "</DiveGasChanges>"
            )
            + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5"))
        )
    )
    assert "events" not in found.document["dives"][0]["profile"]
    assert any(note.kind == "dropped" and "before the dive began" in note.message for note in found.notes)


def test_no_gas_number_is_asserted_where_nothing_in_the_profile_needs_one() -> None:
    """§6.3 calls it a label rather than an array index, so it is written only when used."""
    found = one(suunto_mixtures("<Oxygen>21</Oxygen>") + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5")))
    assert found["cylinders"] == [{"oxygen": 21.0}]


# -- the profile ----------------------------------------------------------------------


def test_each_channel_takes_only_the_samples_that_carried_a_reading_for_it() -> None:
    """A nil `<Pressure>` is a transmitter dropout and must not truncate the depth channel."""
    found = one(
        suunto_mixtures("<TransmitterId>2411100050</TransmitterId>")
        + suunto_xml_samples(
            suunto_xml_sample(10, Depth="1.42", Pressure="198000"),
            suunto_xml_sample(20, Depth="8.61", Pressure=None),
            suunto_xml_sample(30, Depth="17.35", Pressure="187000"),
        )
    )["profile"]
    assert found["depth"]["times"] == [10, 20, 30]
    assert found["pressures"][0]["times"] == [10, 30]


def test_the_averaged_temperature_is_not_the_temperature_channel() -> None:
    """A smoothed reading sits beside the raw one in the same element; smoothing is a chart's job."""
    found = one(
        suunto_xml_samples(suunto_xml_sample(10, AveragedTemperature="30", Temperature="22.4"))
    )["profile"]
    assert found["temperature"]["values"] == [224]


def test_a_zero_ceiling_is_not_a_ceiling() -> None:
    """Zero says the diver may surface, and a gap in the times is how §6.5 spells that."""
    found = one(suunto_xml_samples(suunto_xml_sample(10, Depth="4.5", Ceiling="0")))["profile"]
    assert "ceiling" not in found


def test_a_sample_with_no_time_is_dropped_and_reported() -> None:
    found = convert(
        suunto_xml(
            suunto_xml_samples(
                '<Time i:nil="true" /><Depth>20.5</Depth>', suunto_xml_sample(20, Depth="8.61")
            )
        )
    )
    assert found.document["dives"][0]["profile"]["depth"]["times"] == [20]
    assert any(note.kind == "dropped" and "no <Time>" in note.message for note in found.notes)


def test_two_samples_on_one_second_keep_the_first_and_report_the_second() -> None:
    """A real collision here, unlike the app JSON's separately appended sensor streams.

    Every `<Dive.Sample>` carries every channel, so two of them on one second are two
    readings competing for it. It fires on 37 exports in the corpus, all of them freedives
    this reader skips before it reaches the samples.
    """
    found = convert(
        suunto_xml(
            suunto_xml_samples(suunto_xml_sample(1, Depth="1.86"), suunto_xml_sample(1, Depth="2.14"))
        )
    )
    assert found.document["dives"][0]["profile"]["depth"]["values"] == [186]
    assert any(note.kind == "dropped" and "share the second 1" in note.message for note in found.notes)


def test_samples_carrying_a_time_and_nothing_else_produce_no_profile() -> None:
    """A zero-length sampled record is a claim the source did not make."""
    found = convert(suunto_xml(suunto_xml_samples("<Time>10</Time>", "<Time>20</Time>")))
    assert "profile" not in found.document["dives"][0]
    assert any(note.kind == "dropped" and "no reading this format can hold" in note.message for note in found.notes)


@pytest.mark.parametrize(
    "samples",
    [
        "",
        "<DiveSamples />",
        suunto_xml_samples('<Time i:nil="true" /><Depth>4.5</Depth>'),
    ],
    ids=["no DiveSamples", "an empty one", "every sample dropped"],
)
def test_gas_switches_with_no_profile_to_sit_on_are_reported(samples: str) -> None:
    """This reader's events come from outside the sample stream, so they can be lost.

    Every other reader in this package builds its events out of the samples, which makes
    "no samples, no events" a tautology there and a real loss here: the switch times are on
    the `<DiveMixture>` elements and survive a dive whose profile does not.
    """
    found = convert(
        suunto_xml(
            suunto_mixtures(
                "<DiveGasChanges><DiveGasChange><GasChangeTime>0</GasChangeTime></DiveGasChange>"
                "</DiveGasChanges><Oxygen>21</Oxygen>"
            )
            + samples
        )
    )
    assert "profile" not in found.document["dives"][0]
    assert any(
        note.kind == "dropped" and "no profile for a marker to sit on" in note.message
        for note in found.notes
    )


def test_a_switch_survives_samples_that_carry_a_time_and_nothing_else() -> None:
    """The one shape where the axis keeps the markers: something was on the axis.

    §6.4 makes `duration` the span of the samples, and here that span is zero — but the
    source did record a marker, and `series.SampleAxis.profile` emits a profile for events
    alone rather than dropping them, which is every reader in this package's answer.
    """
    found = convert(
        suunto_xml(
            suunto_mixtures(
                "<DiveGasChanges><DiveGasChange><GasChangeTime>0</GasChangeTime></DiveGasChange>"
                "</DiveGasChanges>"
            )
            + suunto_xml_samples("<Time>10</Time>")
        )
    ).document["dives"][0]["profile"]
    assert found == {"duration": 0, "events": [{"time": 0, "type": "gas_switch", "gas_number": 0}]}


def test_the_marks_block_is_not_read() -> None:
    """29 undocumented numeric types across 4 095 marks, one of them in all 384 exports.

    Mapping one onto §6.5's vocabulary would be a confident label over a number nobody has
    decoded. It is not reported either: it is a block this format has and this reader has
    never claimed, which `docs/suunto-xml-mapping.md` lists rather than saying per dive.
    """
    marks = "<Marks><Mark><MarkTime>0</MarkTime><Type>257</Type></Mark></Marks>"
    found = convert(suunto_xml(marks + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5"))))
    assert "events" not in found.document["dives"][0]["profile"]
    assert not any("Mark" in note.message for note in found.notes)


# -- which cylinder the pressure channel belongs to -----------------------------------


def test_the_transmitting_cylinder_claims_the_channel() -> None:
    """`<TransmitterId>` is nil on exactly the cylinders that had no pod.

    The pod is on the *second* cylinder here, which is the case a reader counting from the
    first would get wrong — and would get wrong invisibly, since the back gas is exactly the
    cylinder with no pressures of its own to disagree with.
    """
    found = one(
        suunto_mixtures("<Oxygen>21</Oxygen>", "<Oxygen>52</Oxygen><TransmitterId>2411100050</TransmitterId>")
        + suunto_xml_samples(suunto_xml_sample(10, Pressure="198000"))
    )
    assert found["profile"]["pressures"][0]["gas_number"] == 1


def test_readings_no_cylinder_claims_become_a_cylinder_of_their_own() -> None:
    """Evidence of a tank is evidence of a tank (`converting.md`, *Cylinders*).

    It carries the channel and nothing else: this file records no gas, no size and no
    start or end pressure for a cylinder it never listed.
    """
    found = convert(
        suunto_xml(
            suunto_mixtures("<Oxygen>21</Oxygen><Size>12</Size>")
            + suunto_xml_samples(suunto_xml_sample(10, Pressure="198000"))
        )
    )
    cylinders = found.document["dives"][0]["cylinders"]
    assert cylinders[1] == {"gas_number": 1}
    assert found.document["dives"][0]["profile"]["pressures"][0]["gas_number"] == 1
    assert any(note.kind == "absent" and "cylinder of their own" in note.message for note in found.notes)


def test_readings_two_cylinders_both_claim_are_dropped() -> None:
    """One `<Pressure>` per sample and no key on it: the file cannot say which are whose.

    Attaching them to whichever came first would put a stage bottle's pressure drop on the
    back gas, which is the shape a gas-consumption figure is derived from.
    """
    found = convert(
        suunto_xml(
            suunto_mixtures(
                "<TransmitterId>2411100050</TransmitterId>", "<TransmitterId>2411100051</TransmitterId>"
            )
            + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5", Pressure="198000"))
        )
    )
    assert "pressures" not in found.document["dives"][0]["profile"]
    assert any(note.kind == "dropped" and "2 cylinders record" in note.message for note in found.notes)


def test_a_sample_pressure_outside_the_allowed_range_is_dropped_and_counted_once() -> None:
    """A pod reporting out of range does it for a run of samples, not for one."""
    found = convert(
        suunto_xml(
            suunto_mixtures("<TransmitterId>2411100050</TransmitterId>")
            + suunto_xml_samples(
                suunto_xml_sample(10, Pressure="400000"),
                suunto_xml_sample(20, Pressure="410000"),
                suunto_xml_sample(30, Pressure="198000"),
            )
        )
    )
    assert found.document["dives"][0]["profile"]["pressures"][0]["times"] == [30]
    out_of_range = [note for note in found.notes if "outside the 0 to 350" in note.message]
    assert len(out_of_range) == 1
    assert "2 samples record" in out_of_range[0].message


# -- identity -------------------------------------------------------------------------


def test_a_dive_takes_a_positional_identity_and_the_report_says_so() -> None:
    """The file records no id of any kind for its dive."""
    found = convert(suunto_xml())
    assert any(note.kind == "absent" and "no id" in note.message for note in found.notes)
    assert convert(suunto_xml()).document["dives"][0]["uuid"] == found.document["dives"][0]["uuid"]


def test_a_positional_identity_is_scoped_to_the_archive_member_it_came_from() -> None:
    """Which is what keeps a directory of one-dive exports from collapsing into one dive."""
    identities = [
        SUUNTO_XML.convert(suunto_xml(), exported_at=EXPORTED_AT, scope=Scope(member=member))
        .document["dives"][0]["uuid"]
        for member in ("Dive_2021-04-06-1116.xml", "Dive_2021-04-06-1231.xml")
    ]
    assert identities[0] != identities[1]


def test_the_identity_namespace_is_the_frozen_one() -> None:
    """Changing it renumbers every document this reader has ever produced."""
    assert str(SUUNTO_XML_ID_NAMESPACE) == "cefc9278-1124-5d83-8af6-7f386f03a061"
    assert SUUNTO_XML.namespace == SUUNTO_XML_ID_NAMESPACE


# -- what the reader claims -----------------------------------------------------------


def test_the_sniff_wants_the_namespace_as_well_as_the_root_element() -> None:
    """`<dive>` is a name any dive-log format might reach for, unlike `<uddf>` or `<divelog>`."""
    assert sniff(suunto_xml()[:SNIFF_BYTES]) == "suunto_xml"
    assert sniff(suunto_xml(namespace=None)[:SNIFF_BYTES]) is None


def test_a_document_the_sniff_declines_is_still_read_when_the_caller_names_the_format() -> None:
    """Deciding what a file is wants the tighter test; reading a named one wants leniency."""
    assert len(convert(suunto_xml(namespace=None), format="suunto_xml").document["dives"]) == 1


def test_a_root_element_that_is_not_a_dive_is_refused() -> None:
    with pytest.raises(MalformedSuuntoXmlError):
        convert(b"<Logbook />", format="suunto_xml")


def test_bytes_that_are_not_xml_are_refused() -> None:
    with pytest.raises(MalformedSuuntoXmlError):
        convert(b"not xml at all", format="suunto_xml")


def test_a_doctype_is_refused_outright() -> None:
    """Spec §9, through the parse target every XML reader in this package shares."""
    document = b'<!DOCTYPE Dive [<!ENTITY a "b">]>\n' + suunto_xml()
    with pytest.raises(DoctypeRefusedError):
        convert(document, format="suunto_xml")


def test_the_provenance_names_the_computer_rather_than_the_desktop_application() -> None:
    """`<Source>` is what the computer calls itself and `<Software>` is its firmware.

    `<SerialNumber>` identifies one physical device rather than the software that produced
    the readings, so it is not carried.
    """
    found = convert(
        suunto_xml(
            "<Source>Suunto D5</Source><Software>3.0.2143</Software>"
            "<SerialNumber>192410004212</SerialNumber>"
        )
    ).document["extensions"]["divejson"]
    assert found == {
        "converted_from": "suunto_xml",
        "source_generator": {"name": "Suunto D5", "version": "3.0.2143"},
    }


def test_nothing_this_reader_produces_is_inferred() -> None:
    """It computes no value from other readings, so it has no derived member to label."""
    found = convert(suunto_xml("<MaxDepth>32.41</MaxDepth>" + suunto_xml_samples(suunto_xml_sample(10, Depth="4.5"))))
    assert "inferred" not in found.document["extensions"]["divejson"]
    assert not any(note.kind == "inferred" for note in found.notes)
