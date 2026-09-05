"""What the converter accepts, what it refuses, and what it declines to invent.

Grouped by the rule under test rather than by the element: the root shapes, the leniencies
that let a schema-invalid file through, the doctype refusal, identity, and the whole
"nothing invented" family — which is the half of this converter that is easiest to break
by being helpful.
"""

from __future__ import annotations

import json

import pytest
from helpers import STARTED_AT, before, one_dive, uddf

from divejson import DoctypeRefusedError, MalformedUddfError, convert
from divejson.validate import validate_document


def _refuse_non_json(token: str) -> None:
    raise AssertionError(f"the document carries the token {token}, which RFC 8259 has no room for")


def dive(body: str, **kwargs) -> dict:
    return convert(one_dive(body, **kwargs)).document["dives"][0]


def messages(data: bytes) -> list[str]:
    return [note.message for note in convert(data).notes]


# -- root shapes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "namespace"),
    [
        ("3.2.2", "http://www.streit.cc/uddf/3.2/"),
        ("3.1.0", "http://www.streit.cc/uddf/3.1/"),
        ("3.2.1", None),  # divelogs.de and APD DiveSight emit a bare <uddf>
        ("2.2.0", "urn:uddf:something:else"),  # an unknown namespace is still UDDF
    ],
)
def test_every_root_shape_is_read(version: str, namespace: str | None) -> None:
    document = convert(one_dive(STARTED_AT, version=version, namespace=namespace)).document
    assert len(document["dives"]) == 1
    assert document["extensions"]["divejson"]["uddf_version"] == version


def test_an_uppercase_root_is_read() -> None:
    """UDDF 2.x spelled its elements in upper case, attribute names included."""
    data = b'<UDDF VERSION="2.2.0"><PROFILEDATA><REPETITIONGROUP ID="g"><DIVE ID="d">'
    data += b"<INFORMATIONBEFOREDIVE><DATETIME>2002-06-18T09:00:00</DATETIME></INFORMATIONBEFOREDIVE>"
    data += b"</DIVE></REPETITIONGROUP></PROFILEDATA></UDDF>"
    document = convert(data).document
    assert document["dives"][0]["started_at"] == "2002-06-18T09:00:00"
    assert document["extensions"]["divejson"]["uddf_version"] == "2.2.0"


def test_a_root_that_is_not_uddf_is_refused() -> None:
    """`--from uddf`, because the sniffer would not have handed these bytes here at all.

    The two refusals are different answers to different questions and both have to keep
    working: "nothing reads this" is what a diver uploading a spreadsheet gets, and "this
    is not UDDF" is what someone who said it was gets.
    """
    with pytest.raises(MalformedUddfError, match="not <uddf>"):
        convert(b"<dives/>", format="uddf")


def test_input_that_is_not_xml_is_refused() -> None:
    with pytest.raises(MalformedUddfError, match="not well-formed"):
        convert(b"{}", format="uddf")


# -- the doctype refusal -------------------------------------------------------------


def test_a_doctype_is_refused() -> None:
    """Spec §9: a reader dereferences nothing it finds in a document.

    `ElementTree` blocks external entities on its own, but entity *amplification* is capped
    only by recent libexpat, which is a library-version property rather than one this
    package's `>=3.10` floor can promise. Refusing the declaration is the promise.
    """
    data = b'<!DOCTYPE uddf [<!ENTITY a "b">]><uddf version="3.2.2"/>'
    with pytest.raises(DoctypeRefusedError, match="DOCTYPE"):
        convert(data)


def test_an_entity_bomb_never_expands() -> None:
    """The refusal fires before expat expands a reference, not after."""
    entities = b"".join(
        b'<!ENTITY lol%d "&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;&lol%d;">'
        % ((index,) + (index - 1,) * 10)
        for index in range(1, 10)
    )
    data = b'<!DOCTYPE uddf [' + entities + b'<!ENTITY lol0 "lol">]><uddf version="3.2.2"><n>&lol9;</n></uddf>'
    with pytest.raises(DoctypeRefusedError):
        convert(data)


# -- leniency ------------------------------------------------------------------------


def test_children_are_taken_by_name_rather_than_by_position() -> None:
    """`diveType` swapped `tankdata` and `samples` between 3.2.1 and 3.2.2.

    The namespace did not move with it, so a 3.2.1-correct file is order-invalid under
    3.2.2 and the reverse. divelogs.de writes `<tankdata>` first; Subsurface writes it
    after `<informationbeforedive>`. Both have to read the same.
    """
    tank = '<tankdata><tankvolume>0.012</tankvolume></tankdata>'
    after = "<informationafterdive><greatestdepth>18.4</greatestdepth></informationafterdive>"
    first = dive(f"{tank}{STARTED_AT}{after}")
    second = dive(f"{STARTED_AT}{after}{tank}")
    assert first == second
    assert first["cylinders"][0]["volume"] == 12.0
    assert first["max_depth"] == 18.4


def test_ids_and_refs_are_stripped_of_whitespace() -> None:
    """Subsurface writes one site id as `" ff47210"`, and the links to it carry the space."""
    header = '<divesite><site id=" ff47210"><name>Small Brother</name></site></divesite>'
    document = convert(one_dive(before('<link ref=" ff47210"/>'), header=header)).document
    assert document["dives"][0]["site_uuids"] == [document["sites"][0]["uuid"]]


def test_an_id_that_is_not_an_ncname_is_read_anyway() -> None:
    """`mix(21/0)` fails `xs:ID`, and it is what Subsurface writes for air."""
    header = '<gasdefinitions><mix id="mix(21/0)"><name>air</name><o2>0.21</o2></mix></gasdefinitions>'
    found = dive(f'{STARTED_AT}<tankdata><link ref="mix(21/0)"/></tankdata>', header=header)
    assert found["cylinders"][0]["oxygen"] == 21.0


def test_an_empty_element_is_absent_rather_than_zero() -> None:
    """Subsurface writes `<latitude/>` for a site whose coordinates it does not have."""
    header = "<divesite><site id='s'><name>Small Brother</name><geography><location>Egypt</location><latitude/><longitude/></geography></site></divesite>"
    site = convert(one_dive(STARTED_AT, header=header)).document["sites"][0]
    assert "position" not in site
    assert site["location"] == "Egypt"


# -- nothing invented ----------------------------------------------------------------


def test_null_island_is_not_a_position() -> None:
    """Every site in a divelogs.de export carries an exact 0.000000 pair."""
    header = "<divesite><site id='s'><name>SS Thistlegorm</name><geography><location>Red Sea</location><latitude>0.000000</latitude><longitude>0.000000</longitude></geography></site></divesite>"
    conversion = convert(one_dive(STARTED_AT, header=header))
    assert "position" not in conversion.document["sites"][0]
    assert any("Null Island" in note.message for note in conversion.notes)


def test_a_cylinder_with_no_gas_does_not_get_air() -> None:
    """§6.3: absent oxygen means not recorded, not 21."""
    found = dive(f"{STARTED_AT}<tankdata><tankvolume>0.012</tankvolume></tankdata>")
    assert found["cylinders"][0] == {"volume": 12.0}


def test_a_zero_start_pressure_is_a_devices_absent_marker() -> None:
    """§6.3 says so outright: writers MUST NOT emit it."""
    found = dive(f"{STARTED_AT}<tankdata><tankpressurebegin>0</tankpressurebegin></tankdata>")
    assert "start_pressure" not in found["cylinders"][0]


def test_a_zero_greatest_depth_is_not_a_dive_to_the_surface() -> None:
    """UDDF makes `<greatestdepth>` mandatory, so writers put a 0 where they have nothing."""
    found = dive(f"{STARTED_AT}<informationafterdive><greatestdepth>0</greatestdepth></informationafterdive>")
    assert "max_depth" not in found


def test_a_zero_lead_quantity_is_a_recorded_no_lead() -> None:
    """The mirror case, and the reason the rule above is not "drop every zero".

    §6.2 makes `weight: 0` a recorded fact, distinct from absence — a diver who carried no
    lead recorded something. Subsurface writes a 0 here for a logbook it holds no weights
    for, which is a loss on its side rather than a licence to discard the member.
    """
    found = dive(before("<equipmentused><leadquantity>0</leadquantity></equipmentused>"))
    assert found["weight"] == 0.0


def test_an_offset_less_start_time_stays_offset_less() -> None:
    """§5.2: never assume an offset, and never convert to UTC."""
    data = one_dive(before(datetime_text="2026-04-17T11:49:23"))
    found = convert(data).document["dives"][0]
    assert found["started_at"] == "2026-04-17T11:49:23"
    assert any("no UTC offset" in message for message in messages(data))


def test_a_recorded_offset_travels_unchanged() -> None:
    found = dive(before(datetime_text="2026-04-17T11:49:23+02:00"))
    assert found["started_at"] == "2026-04-17T11:49:23+02:00"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("2026-04-17T11:49:23-05:00", "2026-04-17T11:49:23-05:00"),
        ("2026-04-17T11:49:23Z", "2026-04-17T11:49:23Z"),
        ("2026-04-17T11:49:23+0200", "2026-04-17T11:49:23+02:00"),  # no colon
        ("2026-04-17T11:49:23+02", "2026-04-17T11:49:23+02:00"),  # hours only
        ("2026-04-17T11:49:23.510000+02:00", "2026-04-17T11:49:23.510000+02:00"),
        ("2026-04-17T11:49+02:00", "2026-04-17T11:49:00+02:00"),  # no seconds
    ],
)
def test_offset_spellings_are_normalized_without_being_moved(written: str, expected: str) -> None:
    assert dive(before(datetime_text=written))["started_at"] == expected


def test_a_truncated_midnight_is_read_as_midnight_and_reported() -> None:
    """Subsurface emits `<datetime>2002-06-18T</datetime>` for a dive logged at midnight.

    Dropping the dive would lose it over a writer's unguarded string concatenation, so the
    reading is stated in the report rather than made silently.
    """
    data = one_dive(before(datetime_text="2002-06-18T"))
    assert convert(data).document["dives"][0]["started_at"] == "2002-06-18T00:00:00"
    assert any("no time of day" in message for message in messages(data))


def test_a_dive_with_no_start_time_is_dropped_rather_than_dated() -> None:
    data = one_dive("<informationafterdive><greatestdepth>18.4</greatestdepth></informationafterdive>")
    conversion = convert(data)
    assert "dives" not in conversion.document
    assert any("the dive is dropped" in note.message for note in conversion.notes)


def test_a_site_with_no_name_is_dropped_along_with_the_reference_to_it() -> None:
    """§6.10 requires a name and §5.3 forbids a dangling reference, so both have to go."""
    header = "<divesite><site id='s'><name/><geography><location>Sinai, Red Sea</location></geography></site></divesite>"
    conversion = convert(one_dive(before("<link ref='s'/>"), header=header))
    assert "sites" not in conversion.document
    assert "site_uuids" not in conversion.document["dives"][0]
    assert any("cannot be invented" in note.message for note in conversion.notes)


def test_an_owner_with_nothing_recorded_is_no_diver_at_all() -> None:
    """§6.1: minting identity for an ownerless logbook is §5.4 applied to people."""
    header = "<diver><owner id='owner'><personal><firstname/><lastname/></personal></owner></diver>"
    conversion = convert(one_dive(STARTED_AT, header=header))
    assert "diver" not in conversion.document
    assert any("records nothing about the logbook's owner" in note.message for note in conversion.notes)


def test_an_owner_with_a_name_becomes_a_diver() -> None:
    header = (
        "<diver><owner id='owner'><personal><firstname>Sam</firstname><lastname>Reef</lastname></personal>"
        "<contact><email>sam@example.org</email></contact></owner></diver>"
    )
    diver = convert(one_dive(STARTED_AT, header=header)).document["diver"]
    assert diver["name"] == "Sam Reef"
    assert diver["email"] == "sam@example.org"


@pytest.mark.parametrize("written", ["n/a", "-", "Sam Reef", "not recorded", "sam at example org"])
def test_an_email_that_is_not_an_address_costs_one_member_and_not_the_logbook(written: str) -> None:
    """`email` is the one member whose *type* constrains the text a source can put in it.

    Everything else a source writes reaches a free-text member. So an unusable value here
    is the one that could make the converter's own output fail validation — which it
    treats as its own bug and refuses to write — and a single junk header field would
    discard an entire logbook rather than costing it one member and a line in the report.
    """
    header = (
        "<diver><owner id='owner'><personal><firstname>Sam</firstname><lastname>Reef</lastname></personal>"
        f"<contact><email>{written}</email></contact></owner></diver>"
    )
    conversion = convert(one_dive(STARTED_AT, header=header))
    assert conversion.document["diver"] == {"uuid": conversion.document["diver"]["uuid"], "name": "Sam Reef"}
    assert len(conversion.document["dives"]) == 1
    assert any("is not an address" in note.message for note in conversion.notes)


@pytest.mark.parametrize("written", ["1e999", "1e999999999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_a_number_too_large_to_carry_is_not_a_number(written: str) -> None:
    """`Decimal` parses all of these and calls the first three finite. Neither survives.

    `1e999` reaches the output as a float infinity, which `json.dumps` writes as a bare
    `Infinity` token that no JSON parser will read back — and the converter's own
    validation does not object, since `jsonschema` is happy to call infinity a number
    greater than zero. `1e999999999` overflows `Decimal`'s own arithmetic at the next
    multiplication, raising something outside this module's errors, so the command dies
    with a traceback and abandons every file after it in a batch.
    """
    header = f'<gasdefinitions><mix id="m"><name>Gas</name><o2>{written}</o2></mix></gasdefinitions>'
    body = (
        f'{STARTED_AT}<tankdata><link ref="m"/><tankvolume>{written}</tankvolume></tankdata>'
        f"<samples><waypoint><depth>{written}</depth><divetime>0</divetime>"
        f"<temperature>{written}</temperature></waypoint></samples>"
        f"<informationafterdive><greatestdepth>{written}</greatestdepth>"
        f"<diveduration>{written}</diveduration></informationafterdive>"
    )
    conversion = convert(one_dive(body, header=header))
    assert validate_document(conversion.document) == []
    found = conversion.document["dives"][0]
    assert found["cylinders"][0] == {}
    assert "max_depth" not in found and "duration" not in found and "profile" not in found
    # `parse_constant` fires for exactly the three tokens RFC 8259 has no room for, which
    # are what `json.dumps` emits for a float that overflowed.
    json.loads(json.dumps(conversion.document), parse_constant=_refuse_non_json)


def test_hostile_source_strings_still_produce_a_conforming_document() -> None:
    """Every string a source can put anywhere, at once, over every length the format caps.

    The guard for the *class* rather than for one member: the converter validates its own
    output and treats a failure as its own bug, so any source-supplied string reaching a
    member with a constraint it does not clear takes the whole file down with it. That is
    a mistake available to every mapping added after this one, and this is the test that
    fails when someone makes it.
    """
    long_name = "N" * 400
    long_note = "note. " * 3000
    header = (
        f"<diver><owner id='owner'><personal><firstname>{long_name}</firstname></personal>"
        "<contact><email>whatever they typed</email></contact>"
        f"<equipment><mask id='g'><name>{long_name}</name>"
        f"<manufacturer id='m'><name>{long_name}</name></manufacturer>"
        f"<notes><para>{long_note}</para></notes></mask></equipment></owner></diver>"
        f"<divesite><site id='s'><name>{long_name}</name>"
        f"<geography><location>{long_name}</location></geography>"
        f"<notes><para>{long_note}</para></notes></site></divesite>"
        f"<divetrip><trip id='t'><name>{long_name}</name><trippart><name>{long_name}</name>"
        "<dateoftrip startdate='2026-04-18T00:00:00' enddate='2026-04-25T00:00:00'/>"
        f"<geography><location>{long_name * 2}</location></geography>"
        f"<notes><para>{long_note}</para></notes></trippart></trip></divetrip>"
    )
    body = (
        before("<link ref='s'/><tripmembership ref='t'/><equipmentused><link ref='g'/></equipmentused>")
        + f"<samples><waypoint><depth>1.0</depth><divetime>0</divetime><setmarker>{long_name}</setmarker></waypoint></samples>"
        + f"<informationafterdive><notes><para>{long_note}</para></notes></informationafterdive>"
    )
    conversion = convert(one_dive(body, header=header))
    assert validate_document(conversion.document) == []
    assert "email" not in conversion.document["diver"]
    assert len(conversion.document["dives"][0]["notes"]) == 10_000
    assert len(conversion.document["sites"][0]["name"]) == 255


# -- identity ------------------------------------------------------------------------


def test_converting_the_same_file_twice_gives_the_same_identities() -> None:
    """§5.3 asks for identifiers stable across exports of the same data."""
    data = one_dive(STARTED_AT)
    assert convert(data).document["dives"][0]["uuid"] == convert(data).document["dives"][0]["uuid"]


def test_a_dive_and_its_repetition_group_do_not_share_an_identity() -> None:
    """Every `<dive>` in a Subsurface export reuses its group's id.

    Hashing the bare id would give the dive and the group one uuid; hashing the record kind
    with it is what keeps them apart — the "Identity is derived, never invented fresh" rule
    in `divejson/uddf.py`'s module docstring, and `docs/uddf-mapping.md`'s *Identity*.
    """
    shared = "idp5747615184931662520"
    data = uddf(
        f'<divesite><site id="{shared}"><name>Small Brother</name></site></divesite>'
        f'<profiledata><repetitiongroup id="{shared}"><dive id="{shared}">{STARTED_AT}</dive>'
        "</repetitiongroup></profiledata>"
    )
    document = convert(data).document
    assert document["dives"][0]["uuid"] != document["sites"][0]["uuid"]


def test_two_records_of_one_kind_sharing_an_id_is_reported_and_the_second_dropped() -> None:
    header = (
        "<divesite><site id='s'><name>One</name></site><site id='s'><name>Two</name></site></divesite>"
    )
    conversion = convert(one_dive(STARTED_AT, header=header))
    assert [site["name"] for site in conversion.document["sites"]] == ["One"]
    assert any("cannot share one identity" in note.message for note in conversion.notes)


def test_a_record_with_no_id_gets_a_positional_identity_and_says_so() -> None:
    header = "<divesite><site><name>Unnamed by its writer</name></site></divesite>"
    conversion = convert(one_dive(STARTED_AT, header=header))
    assert len(conversion.document["sites"]) == 1
    assert any("derived from its position" in note.message for note in conversion.notes)


# -- the profile ---------------------------------------------------------------------


def test_channels_keep_their_own_time_axes() -> None:
    """A temperature is not padded to the depth channel's length, nor the reverse.

    This is the shape that makes a converted Subsurface dive keep 431 depth samples beside
    29 temperatures rather than inventing 402 readings.
    """
    samples = (
        "<waypoint><depth>1.0</depth><divetime>0</divetime><temperature>297.15</temperature></waypoint>"
        "<waypoint><depth>2.0</depth><divetime>10</divetime></waypoint>"
        "<waypoint><depth>3.0</depth><divetime>20</divetime></waypoint>"
    )
    found = dive(f"{STARTED_AT}<samples>{samples}</samples>")["profile"]
    assert found["depth"]["times"] == [0, 10, 20]
    assert found["temperature"]["times"] == [0]
    assert found["duration"] == 20


def test_profile_duration_spans_the_samples_rather_than_the_logged_duration() -> None:
    """§6.4 makes it REQUIRED and at least the largest sample time.

    `<diveduration>` is shorter than the sample span in both real exports on record — 4010
    against 4300, and 4001 against 4288 — so trimming samples to make the logged duration
    fit would be the §5.4 violation this converter exists to avoid.
    """
    samples = "<waypoint><depth>1.0</depth><divetime>4300</divetime></waypoint>"
    found = dive(
        f"{STARTED_AT}<samples>{samples}</samples>"
        "<informationafterdive><diveduration>4010</diveduration></informationafterdive>"
    )
    assert found["duration"] == 4010
    assert found["profile"]["duration"] == 4300


def test_waypoints_are_ordered_by_their_recorded_time() -> None:
    samples = (
        "<waypoint><depth>3.0</depth><divetime>20</divetime></waypoint>"
        "<waypoint><depth>1.0</depth><divetime>0</divetime></waypoint>"
    )
    found = dive(f"{STARTED_AT}<samples>{samples}</samples>")["profile"]
    assert found["depth"]["times"] == [0, 20]
    assert found["depth"]["values"] == [100, 300]


def test_a_dropped_waypoint_is_reported_at_its_own_position_in_the_file() -> None:
    """The path is where a diver would open the file, so it counts every waypoint.

    A path that counted only the waypoints that survived would send someone to the one
    element on the dive that converted perfectly.
    """
    samples = (
        "<waypoint><depth>1.0</depth></waypoint>"
        "<waypoint><depth>2.0</depth><divetime>10</divetime></waypoint>"
        "<waypoint><depth>3.0</depth></waypoint>"
    )
    conversion = convert(one_dive(f"{STARTED_AT}<samples>{samples}</samples>"))
    dropped = [note.where for note in conversion.notes if "no place on the profile" in note.message]
    assert dropped == ["dive/0/waypoint/0", "dive/0/waypoint/2"]


def test_waypoints_landing_on_one_second_keep_the_first() -> None:
    """`<divetime>` is `xs:float`, and §6.5's `times` are strictly increasing integers."""
    samples = (
        "<waypoint><depth>1.0</depth><divetime>30</divetime></waypoint>"
        "<waypoint><depth>2.0</depth><divetime>30.4</divetime></waypoint>"
    )
    data = one_dive(f"{STARTED_AT}<samples>{samples}</samples>")
    found = convert(data).document["dives"][0]["profile"]
    assert found["depth"]["times"] == [30]
    assert found["depth"]["values"] == [100]
    assert any("strictly increasing" in message for message in messages(data))


def test_waypoints_with_no_usable_reading_produce_no_profile_and_say_so() -> None:
    """Not the same silence as a dive with no `<samples>` at all.

    The source recorded a profile here; the converter could not carry it. That is the class
    a dropped waypoint and a dropped coordinate pair are in, and both of those report — so
    dropping a whole sampled record without a word would be the one place this converter
    loses recorded structure quietly. A bare `duration: 0` is not the alternative: it
    asserts a zero-length sampled record the source never claimed.
    """
    samples = (
        "<waypoint><depth/><divetime>0</divetime><temperature/></waypoint>"
        "<waypoint><depth/><divetime>10</divetime></waypoint>"
    )
    data = one_dive(f"{STARTED_AT}<samples>{samples}</samples>")
    assert "profile" not in convert(data).document["dives"][0]
    assert "the dive's 2 waypoints carry a time but no reading this format can hold" in " ".join(messages(data))


def test_the_report_counts_one_waypoint_in_the_singular() -> None:
    """A diver reads these lines. `1 waypoints` is the tell that nobody did."""
    data = one_dive(f"{STARTED_AT}<samples><waypoint><depth/><divetime>0</divetime></waypoint></samples>")
    assert "the dive's 1 waypoint carries a time but no reading this format can hold" in " ".join(messages(data))


def test_a_dive_with_no_samples_at_all_is_not_reported() -> None:
    """The mirror, and why the note above is not noise: nothing was recorded to lose.

    A dive that never had a profile is not a dive that lost one, so the report stays empty
    — which is what keeps the note above meaning something when it does appear.
    """
    assert messages(one_dive(STARTED_AT)) == []


def test_a_waypoint_with_no_time_has_no_place_on_the_axis() -> None:
    samples = "<waypoint><depth>1.0</depth></waypoint><waypoint><depth>2.0</depth><divetime>10</divetime></waypoint>"
    data = one_dive(f"{STARTED_AT}<samples>{samples}</samples>")
    assert convert(data).document["dives"][0]["profile"]["depth"]["times"] == [10]
    assert any("no place on the profile's time axis" in message for message in messages(data))


def test_two_cylinders_on_one_blend_keep_separate_pressure_channels() -> None:
    """A sidemount pair links one `<mix>`, so the reference alone cannot tell them apart.

    Repeated references on one waypoint take the linking cylinders in order; collapsing
    them would put two readings on one second, which §6.5 forbids, and would say the diver
    carried one bottle.
    """
    header = '<gasdefinitions><mix id="m"><name>Air</name><o2>0.21</o2></mix></gasdefinitions>'
    tanks = '<tankdata><link ref="m"/></tankdata><tankdata><link ref="m"/></tankdata>'
    samples = (
        "<waypoint><depth>1.0</depth><divetime>0</divetime>"
        '<tankpressure ref="m">20000000</tankpressure><tankpressure ref="m">18000000</tankpressure></waypoint>'
    )
    found = dive(f"{STARTED_AT}{tanks}<samples>{samples}</samples>", header=header)
    assert [channel["gas_number"] for channel in found["profile"]["pressures"]] == [0, 1]
    assert [channel["values"] for channel in found["profile"]["pressures"]] == [[2000], [1800]]
    assert [cylinder["gas_number"] for cylinder in found["cylinders"]] == [0, 1]


def test_a_marker_naming_an_event_type_comes_back_as_that_type() -> None:
    """`<setmarker>` is a bare string, so a round trip through UDDF has nothing else to go on."""
    samples = (
        "<waypoint><depth>1.0</depth><divetime>0</divetime><setmarker>safety_stop</setmarker></waypoint>"
        "<waypoint><depth>2.0</depth><divetime>10</divetime><setmarker>NoDecoTime</setmarker></waypoint>"
    )
    events = dive(f"{STARTED_AT}<samples>{samples}</samples>")["profile"]["events"]
    assert events == [
        {"time": 0, "type": "safety_stop"},
        {"time": 10, "type": "other", "label": "NoDecoTime"},
    ]
