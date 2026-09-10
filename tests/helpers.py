"""Building source documents to convert, one section per format.

The corpora in `fixtures/` cover whole files as real writers produce them. These build
the smallest document that exhibits one behaviour, so that a test about a unit conversion
reads as a unit conversion rather than as a diff of two logbooks.

Not a `conftest.py`: nothing here is a pytest hook or fixture, and pytest puts this
directory on `sys.path` for the test modules either way.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

UDDF_NAMESPACE = "http://www.streit.cc/uddf/3.2/"

# The instant `fixtures/uddf/*.divejson` were generated with. Any would do — it lands in a
# member the comparison ignores — and pinning one only keeps the files from churning every
# time they are regenerated.
EXPORTED_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)


def recorded_by(dive: dict, index: int = 0) -> dict:
    """One dive's `recordings[index]`, or an empty dict where it has none.

    A profile sits inside a recording (§6.4a) and a test about a unit conversion should not
    have to say so two levels deep. Empty rather than raising, because "this dive has no
    recording at all" is the assertion in several tests here, and `{}` is the shape that
    lets `profile_of(dive) is None` say it.
    """
    recordings = dive.get("recordings") or []
    return recordings[index] if index < len(recordings) else {}


def profile_of(dive: dict, index: int = 0) -> dict | None:
    """The profile of one dive's `recordings[index]`, or nothing at all."""
    return recorded_by(dive, index).get("profile")


def device_of(dive: dict, index: int = 0) -> dict | None:
    """The device of one dive's `recordings[index]`, or nothing at all."""
    return recorded_by(dive, index).get("device")


def before(extra: str = "", *, datetime_text: str = "2026-04-17T11:49:23+02:00") -> str:
    """An `<informationbeforedive>` carrying a start time, which every dive needs.

    `started_at` is REQUIRED (spec §6.2), so a dive without one is dropped rather than
    converted — which would make every other assertion in a test vacuous.
    """
    return f"<informationbeforedive><datetime>{datetime_text}</datetime>{extra}</informationbeforedive>"


STARTED_AT = before()


def uddf(body: str, *, version: str = "3.2.2", namespace: str | None = UDDF_NAMESPACE) -> bytes:
    """One UDDF document around `body`, as the bytes `convert` takes."""
    declared = f' xmlns="{namespace}"' if namespace else ""
    return f'<?xml version="1.0" encoding="utf-8"?>\n<uddf{declared} version="{version}">{body}</uddf>'.encode()


def one_dive(
    body: str,
    *,
    header: str = "",
    version: str = "3.2.2",
    namespace: str | None = UDDF_NAMESPACE,
) -> bytes:
    """A document whose only dive is `body`, with `header` before `<profiledata>`."""
    return uddf(
        f'{header}<profiledata><repetitiongroup id="rg"><dive id="d1">{body}</dive></repetitiongroup></profiledata>',
        version=version,
        namespace=namespace,
    )


# -- Subsurface `.ssrf` ---------------------------------------------------------------

# The `@date`/`@time` pair every `.ssrf` dive needs, being the same instant the UDDF helper
# above uses — without the offset, which the format records nowhere.
SSRF_STARTED_AT = "date='2026-04-17' time='11:49:23'"


def ssrf(body: str, *, program: str = "subsurface", version: str = "3") -> bytes:
    """One Subsurface logbook around `body`, as the bytes `convert` takes."""
    declared = "".join(
        f" {name}='{value}'" for name, value in (("program", program), ("version", version)) if value
    )
    return f"<?xml version='1.0' encoding='utf-8'?>\n<divelog{declared}>{body}</divelog>".encode()


def one_ssrf_dive(attributes: str = "", body: str = "", *, sites: str = "") -> bytes:
    """A logbook whose only dive carries `attributes` and `body`, after `sites`.

    `attributes` is appended to the start time rather than replacing it: `started_at` is
    REQUIRED (spec §6.2), so a dive without one is dropped rather than converted — which
    would make every other assertion in a test vacuous.
    """
    divesites = f"<divesites>{sites}</divesites>" if sites else ""
    return ssrf(f"{divesites}<dives><dive {SSRF_STARTED_AT} {attributes}>{body}</dive></dives>")


def one_ssrf_computer(body: str, *, attributes: str = "") -> bytes:
    """A logbook whose only dive carries one `<divecomputer>` holding `body`."""
    return one_ssrf_dive(attributes, f"<divecomputer>{body}</divecomputer>")


# -- Suunto app JSON ------------------------------------------------------------------

# The instant every helper below starts its dive at, being the same one the UDDF and
# `.ssrf` helpers use — this exporter records an offset where those two record none, and a
# sub-second fraction where neither does.
SUUNTO_STARTED_AT = "2026-04-17T11:49:23.510+02:00"


def suunto_json(header: dict, samples: list[dict] | None = None) -> bytes:
    """One `DeviceLog` around `header` and `samples`, as the bytes `convert` takes.

    `DateTime` and `ActivityType` are filled in unless the caller states them: `started_at`
    is REQUIRED (spec §6.2) and an activity that is not a dive is skipped, so a helper that
    left either out would make every other assertion in a test vacuous.
    """
    return json.dumps(
        {
            "DeviceLog": {
                "Header": {"ActivityType": 51, "DateTime": SUUNTO_STARTED_AT, **header},
                "Samples": samples or [],
            }
        }
    ).encode()


def suunto_sample(seconds: float, **members: object) -> dict:
    """One `Samples[]` entry `seconds` after `SUUNTO_STARTED_AT`, carrying `members`."""
    at = datetime.fromisoformat(SUUNTO_STARTED_AT) + timedelta(seconds=seconds)
    return {"TimeISO8601": at.isoformat(timespec="milliseconds"), **members}


def suunto_slots(*pressures: int | None) -> list[dict]:
    """The `Cylinders[]` array an Ocean writes: every slot numbered, most of them empty."""
    return [{"GasNumber": number, "Pressure": pascal} for number, pascal in enumerate(pressures)]


# -- Suunto DM5 XML -------------------------------------------------------------------

SUUNTO_XML_NAMESPACE = "http://schemas.datacontract.org/2004/07/Suunto.Diving.Dal"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

# The instant every helper below starts its dive at. The same wall clock the three helpers
# above use, with the sub-second fraction this exporter writes on 380 of the 384 files in
# hand and **no offset**, which is the one thing a DM5 export records nowhere.
SUUNTO_XML_STARTED_AT = "2026-04-17T11:49:23.6"


def suunto_xml(
    body: str = "",
    *,
    started_at: str | None = SUUNTO_XML_STARTED_AT,
    namespace: str | None = SUUNTO_XML_NAMESPACE,
) -> bytes:
    """One `<Dive>` around `body`, as the bytes `convert` takes.

    `<StartTime>` is filled in unless the caller states otherwise: `started_at` is REQUIRED
    (spec §6.2), so a dive without one is dropped rather than converted — which would make
    every other assertion in a test vacuous.
    """
    declared = f' xmlns="{namespace}"' if namespace else ""
    start = "" if started_at is None else f"<StartTime>{started_at}</StartTime>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Dive{declared} xmlns:i="{XSI_NAMESPACE}">{start}{body}</Dive>'
    ).encode()


def suunto_mixtures(*mixtures: str) -> str:
    """A `<DiveMixtures>` block, one `<DiveMixture>` per argument."""
    return "<DiveMixtures>" + "".join(f"<DiveMixture>{body}</DiveMixture>" for body in mixtures) + "</DiveMixtures>"


def suunto_xml_samples(*samples: str) -> str:
    """A `<DiveSamples>` block, one `<Dive.Sample>` per argument."""
    return "<DiveSamples>" + "".join(f"<Dive.Sample>{body}</Dive.Sample>" for body in samples) + "</DiveSamples>"


def suunto_xml_sample(second: int, **members: object) -> str:
    """One `<Dive.Sample>` at `second`, carrying `members` as child elements.

    A member given `None` is written as `i:nil="true"`, which is this serializer's spelling
    of a reading the sensor did not take.
    """
    written = "".join(
        f"<{name} i:nil=\"true\" />" if value is None else f"<{name}>{value}</{name}>"
        for name, value in members.items()
    )
    return f"<Time>{second}</Time>{written}"
