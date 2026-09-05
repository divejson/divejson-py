"""Building UDDF documents to convert.

The corpus in `fixtures/uddf/` covers whole files as real writers produce them. These build
the smallest document that exhibits one behaviour, so that a test about a unit conversion
reads as a unit conversion rather than as a diff of two logbooks.

Not a `conftest.py`: nothing here is a pytest hook or fixture, and pytest puts this
directory on `sys.path` for the test modules either way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

UDDF_NAMESPACE = "http://www.streit.cc/uddf/3.2/"

# The instant `fixtures/uddf/*.divejson` were generated with. Any would do — it lands in a
# member the comparison ignores — and pinning one only keeps the files from churning every
# time they are regenerated.
EXPORTED_AT = datetime(2026, 9, 5, tzinfo=timezone.utc)


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
