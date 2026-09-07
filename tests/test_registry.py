"""What a source is, and what an archive of them is.

Two questions, and the second is the one with teeth. Deciding a single file's format is a
head of bytes and a table; deciding that seven files are one logbook means merging seven
documents without two of them claiming one identity, without a member's derived-value
label pointing at somebody else's dive, and without reading a member that said it was a
gigabyte.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from helpers import FIXTURES, uddf

from divejson import (
    Conversion,
    MalformedArchiveError,
    SourceTooLargeError,
    UnsupportedSourceError,
    compared,
    convert,
    sniff,
)
from divejson.converter import INFERRED, PRODUCER_KEY, Note, Scope
from divejson.registry import (
    WRITTEN,
    ZIP,
    adapter_for,
    known_formats,
    read_formats,
    write_formats,
    writer_for,
)

EXPORTED_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)

SUBSURFACE = (FIXTURES / "uddf" / "subsurface.uddf").read_bytes()
DIVELOGS = (FIXTURES / "uddf" / "divelogs.uddf").read_bytes()


def _zip(members: dict[str, bytes]) -> bytes:
    """An archive whose entries are written in a deliberately unhelpful order.

    Reversed, so that a test asserting member-name order is asserting the sort rather than
    the order the archive happened to be built in.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in reversed(list(members)):
            archive.writestr(name, members[name])
    return buffer.getvalue()


def _anonymous(count: int) -> bytes:
    """A UDDF whose dives carry no ids at all, so their identity is their position."""
    dives = "".join(
        f"<dive><informationbeforedive><datetime>2026-04-1{index}T09:00:00+02:00</datetime>"
        "</informationbeforedive></dive>"
        for index in range(count)
    )
    return uddf(f"<profiledata><repetitiongroup>{dives}</repetitiongroup></profiledata>")


# -- the registry ---------------------------------------------------------------------


def test_the_registered_formats_are_what_this_build_reads() -> None:
    assert read_formats() == ("uddf", "ssrf", "fit", "suunto_json")
    assert known_formats() == {"uddf", "ssrf", "fit", "suunto_json"}


def test_reading_a_format_and_writing_it_are_separate_registrations() -> None:
    """One id, two lists, and `known_formats` is their union.

    UDDF is the only format registered on both sides today, which is exactly why the lists
    have to be asked separately: a corpus's `write/ssrf/` directory is one this build cannot
    answer for, however fluently it answers for `ssrf/` two directories away.
    """
    assert write_formats() == ("uddf",)
    assert WRITTEN == {"uddf"}
    assert WRITTEN < set(read_formats())
    assert known_formats() == set(read_formats()) | WRITTEN


def test_a_format_this_build_does_not_write_has_no_writer() -> None:
    with pytest.raises(UnsupportedSourceError, match="not a format this build writes"):
        writer_for("ssrf")


def test_every_writer_pairs_a_document_with_a_file_of_its_own_extension() -> None:
    """The suffix is what pairs an expected file with its input in a `write/<id>/` directory,
    so a writer whose suffix was `.divejson` would pair a document with itself."""
    for fmt in write_formats():
        writer = writer_for(fmt)
        assert writer.format == fmt
        assert writer.suffix.startswith(".") and writer.suffix != ".divejson"


def test_zip_is_a_container_and_not_a_registered_format() -> None:
    """It has no adapter, no namespace and no pair directory, and nothing may ask for it."""
    assert ZIP not in known_formats()
    with pytest.raises(UnsupportedSourceError, match="not a format this build reads"):
        adapter_for(ZIP)


def test_every_registered_format_has_its_own_frozen_namespace() -> None:
    """One namespace per format, recorded in that format's mapping document and never moved."""
    import uuid

    namespaces = {}
    for fmt in read_formats():
        adapter = adapter_for(fmt)
        assert adapter.namespace == uuid.uuid5(uuid.NAMESPACE_URL, f"https://divejson.org/ns/{fmt}")
        namespaces[fmt] = adapter.namespace
    assert len(set(namespaces.values())) == len(namespaces)


# -- sniffing -------------------------------------------------------------------------


def test_a_uddf_document_is_claimed_by_its_root_element() -> None:
    assert sniff(SUBSURFACE[:512]) == "uddf"


def test_an_archive_answers_zip() -> None:
    assert sniff(_zip({"one.uddf": SUBSURFACE})[:512]) == ZIP


def test_bytes_nothing_claims_answer_none() -> None:
    """A real answer, not a failure: it is what an application turns into a 415."""
    assert sniff(b"date,depth\n2026-04-17,45.91\n") is None


# -- one document ---------------------------------------------------------------------


def test_sniffing_and_naming_the_format_produce_the_same_document() -> None:
    """The invariant the registry has to hold: recognising a file changes nothing about it."""
    sniffed = convert(SUBSURFACE, exported_at=EXPORTED_AT).document
    named = convert(SUBSURFACE, format="uddf", exported_at=EXPORTED_AT).document
    expected = json.loads((FIXTURES / "uddf" / "subsurface.divejson").read_text(encoding="utf-8"))
    assert compared(sniffed) == compared(named) == compared(expected)


def test_a_file_object_is_accepted_as_well_as_bytes() -> None:
    with open(FIXTURES / "uddf" / "subsurface.uddf", "rb") as handle:
        from_handle = convert(handle, exported_at=EXPORTED_AT).document
    assert compared(from_handle) == compared(convert(SUBSURFACE, exported_at=EXPORTED_AT).document)


def test_a_source_nothing_claims_names_what_this_build_does_read() -> None:
    with pytest.raises(UnsupportedSourceError, match=r"uddf \(\.uddf\)"):
        convert(b"date,depth\n")


def test_a_format_this_build_does_not_read_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedSourceError, match="'unregistered'"):
        convert(SUBSURFACE, format="unregistered")


# -- an archive as one logbook --------------------------------------------------------


def test_an_archive_of_two_logbooks_converts_to_one_document() -> None:
    conversion = convert(_zip({"a.uddf": DIVELOGS, "b.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)
    document = conversion.document

    separately = [
        convert(DIVELOGS, exported_at=EXPORTED_AT).document,
        convert(SUBSURFACE, exported_at=EXPORTED_AT).document,
    ]
    assert len(document["dives"]) == sum(len(doc["dives"]) for doc in separately)
    assert len(document["sites"]) == sum(len(doc.get("sites", ())) for doc in separately)
    uuids = [dive["uuid"] for dive in document["dives"]]
    assert len(set(uuids)) == len(uuids)


def test_the_members_are_read_in_member_name_order() -> None:
    """Not the order the archive stores them in, which is whatever wrote it decided."""
    first = convert(_zip({"a.uddf": DIVELOGS, "b.uddf": SUBSURFACE}), exported_at=EXPORTED_AT).document
    alone = convert(DIVELOGS, exported_at=EXPORTED_AT).document
    assert first["dives"][0]["uuid"] == alone["dives"][0]["uuid"]


def test_two_members_whose_dives_carry_no_ids_do_not_collide() -> None:
    """The reason a positional identity is prefixed by the member it came from.

    Two files whose dives have no ids each derive identity from position, so without the
    prefix the second file's first dive would claim the first file's first dive's UUID and
    be dropped as a duplicate — an archive of watch exports losing every dive but one.
    """
    conversion = convert(_zip({"a.uddf": _anonymous(2), "b.uddf": _anonymous(2)}), exported_at=EXPORTED_AT)
    uuids = [dive["uuid"] for dive in conversion.document["dives"]]
    assert len(uuids) == 4
    assert len(set(uuids)) == 4


def test_a_notes_path_says_which_member_raised_it() -> None:
    conversion = convert(_zip({"a.uddf": _anonymous(1)}), exported_at=EXPORTED_AT)
    assert [note.where for note in conversion.notes] == ["a.uddf/dive/0"]


def test_the_same_logbook_twice_is_one_logbook() -> None:
    """Two members naming one id are naming one record, so it is carried once."""
    twice = convert(_zip({"a.uddf": DIVELOGS, "b.uddf": DIVELOGS}), exported_at=EXPORTED_AT)
    once = convert(_zip({"a.uddf": DIVELOGS}), exported_at=EXPORTED_AT)
    assert compared(twice.document) == compared(once.document)


def test_a_record_two_members_share_is_carried_once_and_referred_to_by_both() -> None:
    """The ordinary shape of a per-dive export, and the one that has to keep working.

    A watch writes one file per dive and every one of them repeats the site it was at. The
    site is one record, so it is written once — and the second file's dive still has to
    point at it, rather than being told the site is not carried when it plainly is.
    """
    def logbook(dive_id: str) -> bytes:
        return uddf(
            "<divesite><site id='s1'><name>Um El Faroud</name></site></divesite>"
            f"<profiledata><repetitiongroup><dive id='{dive_id}'><informationbeforedive>"
            "<link ref='s1'/><datetime>2026-04-17T09:00:00+02:00</datetime>"
            "</informationbeforedive></dive></repetitiongroup></profiledata>"
        )

    conversion = convert(_zip({"a.uddf": logbook("d1"), "b.uddf": logbook("d2")}), exported_at=EXPORTED_AT)
    document = conversion.document
    site = document["sites"][0]["uuid"]
    assert len(document["sites"]) == 1
    assert [dive["site_uuids"] for dive in document["dives"]] == [[site], [site]]
    # Nothing was lost, so nothing is reported as lost.
    assert not any("not a dive site this converter carries" in note.message for note in conversion.notes)


def test_one_file_naming_two_records_the_same_is_still_a_source_defect() -> None:
    """The opposite case, and the one the collision rule was written for.

    Two records in one file cannot share an identity (spec §5.3), and unlike the archive
    case there is no other record for a reference to resolve to.
    """
    data = uddf(
        "<divesite><site id='s1'><name>Um El Faroud</name></site>"
        "<site id='s1'><name>Blue Hole</name></site></divesite>"
        "<profiledata><repetitiongroup><dive id='d1'><informationbeforedive>"
        "<datetime>2026-04-17T09:00:00+02:00</datetime></informationbeforedive></dive>"
        "</repetitiongroup></profiledata>"
    )
    conversion = convert(data, exported_at=EXPORTED_AT)
    assert len(conversion.document["sites"]) == 1
    assert any("cannot share one identity" in note.message for note in conversion.notes)


def test_a_member_nothing_claims_refuses_the_whole_archive() -> None:
    """"We read three of your seven dives" is not an answer a diver can act on."""
    with pytest.raises(UnsupportedSourceError, match="notes.txt"):
        convert(_zip({"a.uddf": SUBSURFACE, "notes.txt": b"nothing reads this"}))


def test_a_nested_archive_is_a_member_nothing_claims() -> None:
    with pytest.raises(UnsupportedSourceError, match="inner.zip"):
        convert(_zip({"inner.zip": _zip({"a.uddf": SUBSURFACE})}))


def test_an_archive_with_nothing_convertible_in_it_says_so() -> None:
    with pytest.raises(UnsupportedSourceError, match="holds no files"):
        convert(_zip({"__MACOSX/._a.uddf": b"resource fork", ".DS_Store": b"finder"}))


def test_the_packaging_a_mac_adds_is_ignored_rather_than_refused() -> None:
    """Refusing an archive because the Finder put a `.DS_Store` in it refuses real uploads."""
    conversion = convert(
        _zip({"a.uddf": SUBSURFACE, "__MACOSX/._a.uddf": b"resource fork", ".DS_Store": b"finder"}),
        exported_at=EXPORTED_AT,
    )
    assert len(conversion.document["dives"]) == 2


def test_bytes_that_open_like_an_archive_and_are_not_one_say_so() -> None:
    with pytest.raises(MalformedArchiveError):
        convert(b"PK\x03\x04 and then nothing that is a zip")


# -- the caps -------------------------------------------------------------------------


def test_more_members_than_the_cap_is_refused_before_anything_is_read() -> None:
    with pytest.raises(SourceTooLargeError, match="at most 1 are read"):
        convert(_zip({"a.uddf": SUBSURFACE, "b.uddf": DIVELOGS}), max_members=1)


def test_a_member_that_declares_more_than_the_cap_is_never_opened() -> None:
    with pytest.raises(SourceTooLargeError, match="says it is"):
        convert(_zip({"a.uddf": SUBSURFACE}), max_member_size=10)


def test_an_oversized_member_is_refused_without_being_opened(monkeypatch) -> None:
    """The declared size decides, so nothing oversized ever reaches memory.

    Proven by making opening a member fail outright: if the walk read first and measured
    afterwards, this would raise the sabotage rather than the cap's refusal.
    """

    archive = _zip({"a.uddf": SUBSURFACE})

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a member was opened before its size was checked")

    monkeypatch.setattr(zipfile.ZipFile, "open", refuse)
    with pytest.raises(SourceTooLargeError, match="says it is"):
        convert(archive, max_member_size=10)


def test_the_caps_do_not_fire_when_the_archive_fits() -> None:
    conversion = convert(
        _zip({"a.uddf": SUBSURFACE}), exported_at=EXPORTED_AT, max_members=4, max_member_size=len(SUBSURFACE)
    )
    assert len(conversion.document["dives"]) == 2


# -- what the merged document says about itself ---------------------------------------


def test_provenance_the_members_agree_on_survives_the_merge() -> None:
    conversion = convert(_zip({"a.uddf": SUBSURFACE, "b.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)
    provenance = conversion.document["extensions"][PRODUCER_KEY]
    alone = convert(SUBSURFACE, exported_at=EXPORTED_AT).document["extensions"][PRODUCER_KEY]
    assert provenance["converted_from"] == "uddf"
    assert provenance["source_generator"] == alone["source_generator"]


def test_provenance_the_members_disagree_about_is_dropped_and_reported() -> None:
    """Picking the first file's would assert something no file in the archive said."""
    conversion = convert(_zip({"a.uddf": SUBSURFACE, "b.uddf": DIVELOGS}), exported_at=EXPORTED_AT)
    provenance = conversion.document["extensions"][PRODUCER_KEY]
    assert "source_generator" not in provenance
    assert any("disagree about source_generator" in note.message for note in conversion.notes)


def test_a_uddf_archive_lists_nothing_as_inferred() -> None:
    conversion = convert(_zip({"a.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)
    assert INFERRED not in conversion.document["extensions"][PRODUCER_KEY]


def test_an_inferred_path_is_re_indexed_for_its_place_in_the_merged_document(monkeypatch) -> None:
    """A path that still named its own file's dive 0 would label somebody else's dive.

    No reader in this build infers anything — UDDF's two ambiguities report as `resolved`,
    the kind for a scale decided rather than a value computed, and the Subsurface reader
    settles no scale at all — so the adapter under test is a stand-in for one that does: a
    reader taking a maximum depth off the samples of a file that recorded none. It sets both
    halves the coupling requires, the note and the listed member, because a reader that set
    only one would be the bug rather than the fixture.
    """

    real = adapter_for("uddf")

    class Inferring:
        format = "uddf"
        suffixes = (".uddf",)
        namespace = real.namespace

        def sniff(self, head: bytes) -> bool:
            return real.sniff(head)

        def convert(self, data: bytes, *, exported_at: datetime, scope: Scope) -> Conversion:
            conversion = real.convert(data, exported_at=exported_at, scope=scope)
            provenance = conversion.document["extensions"][PRODUCER_KEY]
            provenance[INFERRED] = ["dives/0/max_depth"]
            notes = (*conversion.notes, Note(scope.where("dive/0"), "read off the samples", "inferred"))
            return Conversion(conversion.document, notes)

    monkeypatch.setattr("divejson.registry.ADAPTERS", (Inferring(),))
    conversion = convert(_zip({"a.uddf": DIVELOGS, "b.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)

    # `divelogs.uddf` contributes two dives, so the second file's dive 0 is dive 2.
    assert conversion.document["extensions"][PRODUCER_KEY][INFERRED] == [
        "dives/0/max_depth",
        "dives/2/max_depth",
    ]
    assert [note.kind for note in conversion.notes].count("inferred") == 2


def test_the_merged_document_is_validated_like_any_other() -> None:
    from divejson import validate_document

    conversion = convert(_zip({"a.uddf": DIVELOGS, "b.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)
    assert validate_document(conversion.document) == []
    assert list(conversion.document)[:2] == ["format", "version"]


def test_the_archives_own_exported_at_is_the_one_the_caller_passed() -> None:
    conversion = convert(_zip({"a.uddf": SUBSURFACE}), exported_at=EXPORTED_AT)
    assert conversion.document["exported_at"] == "2026-09-05T00:00:00+00:00"


def _owned(first_name: str, owner_id: str, dive_id: str, *, email: str = "") -> bytes:
    contact = f"<contact><email>{email}</email></contact>" if email else ""
    return uddf(
        f"<diver><owner id='{owner_id}'><personal><firstname>{first_name}</firstname></personal>"
        f"{contact}</owner></diver>"
        f"<profiledata><repetitiongroup><dive id='{dive_id}'><informationbeforedive>"
        "<datetime>2026-04-17T09:00:00+02:00</datetime></informationbeforedive></dive>"
        "</repetitiongroup></profiledata>"
    )


@pytest.mark.parametrize("second_owner_id", ["owner", "owner2"])
def test_a_second_persons_diver_is_reported_rather_than_silently_dropped(second_owner_id: str) -> None:
    """The one record that does not take the shared-record path, and why.

    Every UDDF writer in the corpus spells the owner's id `owner`, so two *different*
    people's exports collide on it by convention rather than by being one person. Treating
    that the way a shared site is treated would discard the second person's name and email
    without a line in the report — which is why the parametrization runs the colliding id
    as well as the distinct one, and expects the same answer from both.
    """
    conversion = convert(
        _zip({"a.uddf": _owned("Sam", "owner", "d1"), "b.uddf": _owned("Alex", second_owner_id, "d2")}),
        exported_at=EXPORTED_AT,
    )
    assert conversion.document["diver"]["name"] == "Sam"
    assert [note.where for note in conversion.notes if "a logbook has one" in note.message] == ["b.uddf"]


def test_a_member_recording_more_about_the_owner_than_the_merge_keeps_is_reported() -> None:
    """The same person, and something about them is still dropped.

    The report says what happened — the merged logbook does not carry what this file
    recorded about the owner — and not which of the two cases it was. Deciding that would
    mean deciding when two names are one person.
    """
    conversion = convert(
        _zip(
            {
                "a.uddf": _owned("Sam", "owner", "d1"),
                "b.uddf": _owned("Sam", "owner", "d2", email="sam@example.com"),
            }
        ),
        exported_at=EXPORTED_AT,
    )
    assert "email" not in conversion.document["diver"]
    assert [note.where for note in conversion.notes if "a logbook has one" in note.message] == ["b.uddf"]


def test_one_persons_export_repeating_its_owner_is_not_a_report_line() -> None:
    """The shape the archive walk was written for: one diver, one file per dive.

    Nothing is lost when every file names the same person, so nothing is reported. A
    `dropped` line per file would be the report saying, once per dive, that the archive is
    shaped the way archives are.
    """
    conversion = convert(
        _zip({f"dive-{index}.uddf": _owned("Sam", "owner", f"d{index}") for index in range(5)}),
        exported_at=EXPORTED_AT,
    )
    assert conversion.document["diver"]["name"] == "Sam"
    assert len(conversion.document["dives"]) == 5
    assert [note.message for note in conversion.notes if "a logbook has one" in note.message] == []
