"""Validation of DiveJSON documents.

Two passes, mirroring §3 of the specification: the JSON Schema (types, required members,
enums, ranges, lengths, and the structural rules like Position objects), then the
semantic requirements the schema cannot express — identifier uniqueness, referential
closure, cross-member arithmetic, profile-series integrity and span, the offset
requirement on ``exported_at``, and the member-order rule, checked on the parsed
document's key order (which JSON parsing preserves).

Null is not a spelling of absence in this format (spec §5.4): the schema rejects it, so
the semantic checks below simply treat a missing member as missing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match

from . import SPEC_VERSION


@dataclass
class Issue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '$'}: {self.message}"


class DuplicateMemberError(ValueError):
    """A JSON object in the input carries the same member name twice (spec §9)."""


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise DuplicateMemberError(f"duplicate member name {key!r}")
        obj[key] = value
    return obj


def parse_document(text: str) -> Any:
    """Parse document text as JSON, rejecting duplicate member names."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_members)


def load_document(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return parse_document(handle.read())


# A minor version is what names a schema directory, and `load_schema` takes one from its
# caller, so it is checked before it becomes a path component.
_MINOR_VERSION = re.compile(r"\A\d+\.\d+\Z")


def load_schema(minor: str = SPEC_VERSION) -> dict[str, Any]:
    """Read the JSON Schema for one minor version of the format.

    Spec §7 gives every minor its own schema and the directories are named for them, so
    the version is an argument rather than a constant: a built wheel carries every minor
    the vendored corpus had, not only the one this package validates against by default.

    Two locations, and they hold the same bytes. ``_schema/`` is the copy a wheel carries,
    which is the only one an installed package has; ``schema/`` is the vendored corpus
    that copy is built from, which is the only one a checkout has, because a
    ``force-include`` reaches a built wheel and not an editable install. CI checks the
    vendored corpus against the pinned specification commit, and then checks the wheel's
    copy against the vendored corpus, so neither can quietly become a different schema.
    """
    if not _MINOR_VERSION.match(minor):
        raise ValueError(f"{minor!r} is not a DiveJSON minor version")
    here = Path(__file__).resolve().parent
    candidates = [
        here / "_schema" / minor / "divejson.schema.json",
        here.parent / "schema" / minor / "divejson.schema.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            with open(candidate, encoding="utf-8") as handle:
                return json.load(handle)
    raise FileNotFoundError(
        f"no schema for DiveJSON {minor} in the package or the vendored corpus"
    )


def validate_document(doc: Any, raw: str | None = None) -> list[Issue]:
    """Validate one parsed document; an empty result means conforming.

    ``raw`` is accepted for compatibility and unused: ``json.loads`` preserves the
    text's member order in the parsed dict, so the member-order rule (spec §4) is
    checked against ``doc`` itself — which also cannot be fooled by the words
    "format" or "version" appearing inside some string value.
    """
    if not isinstance(doc, dict):
        return [Issue("$", "a DiveJSON document is a JSON object")]

    issues: list[Issue] = []

    declared = doc.get("version")
    if isinstance(declared, str) and declared != SPEC_VERSION:
        issues.append(
            Issue(
                "version",
                f"declares version {declared!r}; this validator implements {SPEC_VERSION} "
                "(readers tolerate newer minors per spec §5.6, validators do not)",
            )
        )
        major = declared.split(".", 1)[0]
        if major != SPEC_VERSION.split(".", 1)[0]:
            return issues

    issues.extend(_member_order_issues(doc))
    issues.extend(_schema_issues(doc))
    issues.extend(_semantic_issues(doc))
    return issues


def _member_order_issues(doc: dict[str, Any]) -> list[Issue]:
    keys = list(doc)
    if not keys:
        return []
    if keys[0] != "format":
        return [Issue("$", f'the first member is "{keys[0]}"; "format" MUST come first (spec §4)')]
    if len(keys) > 1 and keys[1] != "version":
        return [Issue("$", f'the second member is "{keys[1]}"; "version" MUST come second (spec §4)')]
    return []


def _schema_issues(doc: dict[str, Any]) -> list[Issue]:
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    issues = []
    for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        # A failure inside anyOf/if-then surfaces as a top-level error whose message
        # dumps the whole instance; best_match descends to the telling suberror.
        chosen = best_match([error]) or error
        path = "/".join(str(part) for part in chosen.absolute_path)
        issues.append(Issue(path, chosen.message))
    return issues


def _present(obj: dict[str, Any], member: str) -> bool:
    return obj.get(member) is not None


def _semantic_issues(doc: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []

    _check_datetime(doc, "exported_at", "", issues, require_offset=True)

    diver = doc.get("diver")
    seen_uuids: dict[str, str] = {}
    if isinstance(diver, dict):
        _claim_uuid(diver, "diver", seen_uuids, issues)
        _check_datetime(diver, "created_at", "diver", issues)

    collections = {
        name: [row for row in doc.get(name) or [] if isinstance(row, dict)]
        for name in (
            "dives",
            "trips",
            "courses",
            "sites",
            "species",
            "gear",
            "gear_sets",
            "gear_service_schedules",
            "gear_service_records",
            "certifications",
        )
    }

    for name, rows in collections.items():
        for index, row in enumerate(rows):
            here = f"{name}/{index}"
            _claim_uuid(row, here, seen_uuids, issues)
            _check_datetime(row, "created_at", here, issues)

    known = {
        name: {row["uuid"] for row in rows if isinstance(row.get("uuid"), str)}
        for name, rows in collections.items()
    }

    for index, dive in enumerate(collections["dives"]):
        here = f"dives/{index}"
        _check_datetime(dive, "started_at", here, issues)
        if _present(dive, "avg_depth") and _present(dive, "max_depth"):
            try:
                if dive["avg_depth"] > dive["max_depth"]:
                    issues.append(Issue(here, "avg_depth exceeds max_depth"))
            except TypeError:
                pass
        _check_reference(dive, "trip_uuid", known["trips"], "trips", here, issues)
        _check_reference(dive, "course_uuid", known["courses"], "courses", here, issues)
        _check_reference_list(dive, "site_uuids", known["sites"], "sites", here, issues)
        _check_reference_list(dive, "gear_uuids", known["gear"], "gear", here, issues)
        _check_reference_list(dive, "species_uuids", known["species"], "species", here, issues)

        for cyl_index, cylinder in enumerate(dive.get("cylinders") or []):
            if not isinstance(cylinder, dict):
                continue
            cyl_path = f"{here}/cylinders/{cyl_index}"
            if _present(cylinder, "oxygen") and _present(cylinder, "helium"):
                try:
                    if cylinder["oxygen"] + cylinder["helium"] > 100:
                        issues.append(Issue(cyl_path, "oxygen + helium exceeds 100 percent"))
                except TypeError:
                    pass
            if _present(cylinder, "start_pressure") and _present(cylinder, "end_pressure"):
                try:
                    if cylinder["end_pressure"] > cylinder["start_pressure"]:
                        issues.append(Issue(cyl_path, "end_pressure exceeds start_pressure"))
                except TypeError:
                    pass

        # §3 rules 3 and 4 are quantified over **every** recording, not over the primary
        # one. A validator that walked `recordings[0]` alone is the shape §6.4a's ordering
        # rule invites, and `fixtures/invalid/` keeps each of its three recording defects
        # off the first entry for exactly that reason.
        for rec_index, recording in enumerate(dive.get("recordings") or []):
            if not isinstance(recording, dict):
                continue
            rec_path = f"{here}/recordings/{rec_index}"
            _check_datetime(recording, "started_at", rec_path, issues)
            for file_index, stored in enumerate(recording.get("source_files") or []):
                if isinstance(stored, dict):
                    _claim_uuid(stored, f"{rec_path}/source_files/{file_index}", seen_uuids, issues)
            if not any(
                _present(recording, member) for member in ("device", "profile", "source_files")
            ):
                issues.append(
                    Issue(
                        rec_path,
                        "a recording carries at least one of device, profile and source_files "
                        "(spec §3, §6.4a)",
                    )
                )
            _check_profile(recording.get("profile"), f"{rec_path}/profile", issues)

    for index, trip in enumerate(collections["trips"]):
        here = f"trips/{index}"
        if _present(trip, "starts_on") and _present(trip, "ends_on"):
            try:
                if trip["ends_on"] < trip["starts_on"]:
                    issues.append(Issue(here, "ends_on precedes starts_on"))
            except TypeError:
                pass
        for loc_index, location in enumerate(trip.get("locations") or []):
            if not isinstance(location, dict):
                continue
            bbox = location.get("bbox")
            if isinstance(bbox, dict):
                try:
                    if bbox["south"] > bbox["north"]:
                        issues.append(
                            Issue(f"{here}/locations/{loc_index}/bbox", "south exceeds north")
                        )
                except (KeyError, TypeError):
                    pass

    for index, gear_set in enumerate(collections["gear_sets"]):
        _check_reference_list(
            gear_set, "gear_uuids", known["gear"], "gear", f"gear_sets/{index}", issues
        )

    for index, schedule in enumerate(collections["gear_service_schedules"]):
        _check_reference(
            schedule, "gear_uuid", known["gear"], "gear",
            f"gear_service_schedules/{index}", issues,
        )

    for index, record in enumerate(collections["gear_service_records"]):
        here = f"gear_service_records/{index}"
        _check_reference(record, "gear_uuid", known["gear"], "gear", here, issues)
        _check_reference(
            record, "gear_service_schedule_uuid", known["gear_service_schedules"],
            "gear_service_schedules", here, issues,
        )

    for index, course in enumerate(collections["courses"]):
        if _present(course, "starts_on") and _present(course, "ends_on"):
            try:
                if course["ends_on"] < course["starts_on"]:
                    issues.append(Issue(f"courses/{index}", "ends_on precedes starts_on"))
            except TypeError:
                pass

    for index, certification in enumerate(collections["certifications"]):
        here = f"certifications/{index}"
        _check_reference(certification, "course_uuid", known["courses"], "courses", here, issues)
        for member in ("front_file", "back_file"):
            stored = certification.get(member)
            if isinstance(stored, dict):
                _claim_uuid(stored, f"{here}/{member}", seen_uuids, issues)

    for index, item in enumerate(collections["gear"]):
        _check_datetime(item, "archived_at", f"gear/{index}", issues)

    return issues


def _claim_uuid(
    obj: dict[str, Any], path: str, seen: dict[str, str], issues: list[Issue]
) -> None:
    value = obj.get("uuid")
    if not isinstance(value, str):
        return
    if value in seen:
        issues.append(Issue(path, f"uuid {value} already used at {seen[value]}"))
    else:
        seen[value] = path


def _check_reference(
    obj: dict[str, Any],
    member: str,
    targets: set[str],
    collection: str,
    path: str,
    issues: list[Issue],
) -> None:
    value = obj.get(member)
    if isinstance(value, str) and value not in targets:
        issues.append(Issue(f"{path}/{member}", f"references {value}, not present in {collection}"))


def _check_reference_list(
    obj: dict[str, Any],
    member: str,
    targets: set[str],
    collection: str,
    path: str,
    issues: list[Issue],
) -> None:
    values = obj.get(member)
    if not isinstance(values, list):
        return
    for index, value in enumerate(values):
        if isinstance(value, str) and value not in targets:
            issues.append(
                Issue(f"{path}/{member}/{index}", f"references {value}, not present in {collection}")
            )


def _check_profile(profile: Any, path: str, issues: list[Issue]) -> None:
    """§3 rule 3 against one recording's profile: series integrity and the span.

    A function rather than a block inside the dive loop because the rule is quantified
    per recording (§6.4a) and a dive may carry several — the second of which is where
    `fixtures/invalid/non-increasing-samples.divejson` puts its defect.
    """
    if not isinstance(profile, dict):
        return
    latest = 0
    for channel in ("depth", "ceiling", "temperature"):
        series = profile.get(channel)
        if isinstance(series, dict):
            latest = max(latest, _check_series(series, f"{path}/{channel}", issues))
    for series_index, series in enumerate(profile.get("pressures") or []):
        if isinstance(series, dict):
            latest = max(latest, _check_series(series, f"{path}/pressures/{series_index}", issues))
    # Events are deliberately not folded into `latest`: `duration` spans the samples, and
    # an event after the last one is conforming (spec §6.4). A marker pressed at the
    # surface after the recorder's final sample is real logbook data, and requiring
    # `duration` to swallow it would make a writer invent a sample span the file never had.
    duration = profile.get("duration")
    if isinstance(duration, (int, float)) and duration < latest:
        issues.append(
            Issue(
                f"{path}/duration",
                f"duration {duration} does not cover the latest sample at {latest} (spec §6.4)",
            )
        )


def _check_series(series: dict[str, Any], path: str, issues: list[Issue]) -> int:
    """Check one channel; returns the latest sample time seen (0 if none)."""
    times, values = series.get("times"), series.get("values")
    if not (isinstance(times, list) and isinstance(values, list)):
        return 0
    if len(times) != len(values):
        issues.append(Issue(path, f"times has {len(times)} samples but values has {len(values)}"))
    numbers = [value for value in times if isinstance(value, (int, float))]
    if any(later <= earlier for earlier, later in zip(numbers, numbers[1:])):
        issues.append(Issue(path, "times is not strictly increasing"))
    return max(numbers, default=0)


# \Z, not $: Python's $ also matches just before a trailing newline, which would let
# "…T08:00:00Z\n" through the grammar check with the newline silently dropped.
_DATE_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})?\Z"
)


def _check_datetime(
    obj: dict[str, Any],
    member: str,
    path: str,
    issues: list[Issue],
    require_offset: bool = False,
) -> None:
    value = obj.get(member)
    if not isinstance(value, str):
        return
    where = f"{path}/{member}" if path else member
    match = _DATE_TIME.match(value)
    if not match:
        issues.append(Issue(where, f"{value!r} is not a DiveJSON date-time"))
        return
    base, fraction, offset = match.groups()
    # Normalize before the calendar check: fromisoformat is case-sensitive about Z
    # and, on Python 3.10, insists on exactly 3 or 6 fractional digits — both stricter
    # than the format's grammar.
    normalized = base
    if fraction:
        normalized += "." + (fraction[1:] + "000000")[:6]
    if offset:
        normalized += "+00:00" if offset in ("Z", "z") else offset
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        issues.append(Issue(where, f"{value!r} is not a real calendar date-time"))
        return
    if require_offset and offset is None:
        issues.append(Issue(where, "must carry a UTC offset — it is generated, not recorded history (spec §5.2)"))
