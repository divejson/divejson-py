"""`Conversion.sources`: where in the source each record a conversion carried was read from.

A note's `where` counts the source's records and the document counts only the ones it
carries, so a reader that skips a dive moves every dive after it out of step. These tests
are about exactly the logbooks where the two counts part.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from helpers import EXPORTED_AT, FIXTURES, SSRF_STARTED_AT, before, ssrf, uddf

from divejson import convert, read_formats
from divejson.converter import carried

CORPUS = sorted(
    path
    for fmt in read_formats()
    for path in (FIXTURES / fmt).glob("*")
    if path.suffix != ".divejson"
)


@pytest.mark.parametrize("source", CORPUS, ids=lambda path: f"{path.parent.name}/{path.stem}")
def test_every_record_a_conversion_carries_says_where_it_came_from(source) -> None:
    conversion = convert(source.read_bytes(), exported_at=EXPORTED_AT)
    assert list(conversion.sources) == list(carried(conversion.document))
    # One path, one record: otherwise a note at it names no record in particular.
    assert len(set(conversion.sources.values())) == len(conversion.sources)
    for dive in conversion.document.get("dives", ()):
        assert conversion.sources[dive["uuid"]].startswith("dive/")


def _owner(name: str, owner_id: str) -> str:
    return f'<diver><owner id="{owner_id}"><personal><firstname>{name}</firstname></personal></owner></diver>'


def _dives(*dives: str) -> str:
    return f'<profiledata><repetitiongroup id="rg">{"".join(dives)}</repetitiongroup></profiledata>'


def test_a_uddf_dive_the_reader_skips_leaves_the_source_count_and_not_the_documents() -> None:
    data = uddf(
        _owner("Sam", "o1")
        + '<divesite><site id="s1"><name>Reef</name></site></divesite>'
        + _dives(
            '<dive id="d1"><informationafterdive><greatestdepth>18</greatestdepth></informationafterdive></dive>',
            f'<dive id="d2">{before()}</dive>',
            f'<dive id="d2">{before(datetime_text="2026-04-18T09:00:00+02:00")}</dive>',
            f"<dive>{before(datetime_text='2026-04-19T09:00:00+02:00')}</dive>",
        )
    )
    conversion = convert(data)
    document = conversion.document

    # The first dive has no start time and the third repeats the second's id: both are
    # reported against their place in the source, and neither has one in the document.
    dropped = {note.where for note in conversion.notes if note.kind == "dropped"}
    assert dropped == {"dive/0", "dive/2"}
    assert [conversion.sources[dive["uuid"]] for dive in document["dives"]] == ["dive/1", "dive/3"]
    assert conversion.sources[document["diver"]["uuid"]] == "diver"
    assert conversion.sources[document["sites"][0]["uuid"]] == "site/0"

    # The join a caller makes: the note the fourth source dive raised is about the
    # document's second dive.
    about = {where: uuid for uuid, where in conversion.sources.items()}
    no_id = next(note for note in conversion.notes if "gives this dive no id" in note.message)
    assert about[no_id.where] == document["dives"][1]["uuid"]


def test_an_ssrf_dive_the_reader_skips_leaves_the_source_count_and_not_the_documents() -> None:
    data = ssrf(
        "<divesites><site uuid='1' name='A'/><site uuid='1' name='B'/></divesites>"
        f"<dives><dive/><dive {SSRF_STARTED_AT}/>"
        "<dive date='2026-04-18' time='09:00:00'>"
        "<divemaster>Mona</divemaster><buddy>Karim, Sam</buddy><buddy>Ahmed</buddy></dive></dives>"
    )
    conversion = convert(data)
    document = conversion.document

    assert {note.where for note in conversion.notes if note.kind == "dropped"} == {"dive/0", "site/1"}
    assert [conversion.sources[dive["uuid"]] for dive in document["dives"]] == ["dive/1", "dive/2"]
    assert [conversion.sources[site["uuid"]] for site in document["sites"]] == ["site/0"]
    # A person is named inside a dive and is not that dive, so each has a path of its own.
    assert {person["name"]: conversion.sources[person["uuid"]] for person in document["people"]} == {
        "Karim": "dive/2/buddy/0",
        "Sam": "dive/2/buddy/1",
        "Ahmed": "dive/2/buddy/2",
        "Mona": "dive/2/divemaster",
    }


def test_an_archive_names_the_member_that_carried_each_record() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.uddf", uddf(_owner("Sam", "o1") + _dives(f'<dive id="shared">{before()}</dive>')))
        archive.writestr(
            "b.uddf",
            uddf(
                _owner("Alex", "o2")
                + _dives(
                    f'<dive id="shared">{before()}</dive>',
                    f'<dive id="own">{before(datetime_text="2026-04-18T09:00:00+02:00")}</dive>',
                )
            ),
        )
    conversion = convert(buffer.getvalue())
    document = conversion.document

    # The dive both members define is carried by the first, and `b.uddf`'s owner is not
    # the logbook's, so it carries no record and has no place here.
    assert conversion.sources == {
        document["diver"]["uuid"]: "a.uddf/diver",
        document["dives"][0]["uuid"]: "a.uddf/dive/0",
        document["dives"][1]["uuid"]: "b.uddf/dive/1",
    }
