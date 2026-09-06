"""A minimal FIT *writer*, so a binary fixture can be declared inline like every other.

Every other format this package reads is text, and its unit tests build the smallest
document that exhibits one behaviour. FIT would otherwise force a choice between
committing opaque blobs and not testing the interesting cases at all, and neither is
good: a blob cannot be edited to say "a session whose only `max_depth` is a developer
field" or "a file with no `activity` message", which is exactly what has to be pinned.

It is also the only way several messages get tested at all. `dive_summary`,
`tank_summary`, `tank_update` and most of `dive_gas` are Garmin's, no Garmin file exists
in this project, and `docs/fit-mapping.md` says so in its first paragraph. What is below
is what stands in until one arrives.

Messages are encoded through `fitdecode`'s own copy of the global FIT profile — field
numbers, base types, scale factors and enum members are all looked up rather than
hardcoded — so a fixture says `message("session", max_depth=32.41)` and cannot drift out
of step with the profile the reader decodes through. Nothing here is copied out of
Garmin's SDK; see the notice in `README.md`.

Only what these tests need: little endian, one definition per data message, no compressed
timestamp headers and no accumulators.

Not a `conftest.py`, and not part of `helpers.py`: nothing here is a pytest hook, and an
encoder is a different kind of thing from the string builders that module holds.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fitdecode.profile import BASE_TYPES, MESSAGE_TYPES
from fitdecode.types import FieldType

__all__ = ["DevField", "FLOAT32", "Message", "STARTED_AT", "dive_file", "fit_file", "message", "record_stream"]

# FIT timestamps count seconds from this instant, not the Unix epoch.
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

# The instant the Suunto Ocean fixture's dive began, so a hand-built file and the recorded
# one describe the same moment and a test can be read beside `fixtures/fit/`.
STARTED_AT = datetime(2026, 4, 17, 9, 49, 23, tzinfo=timezone.utc)

# Base type identifiers, from the profile's own table. Only the ones a developer field or
# a hand-written definition names: everything else is looked up per field.
_STRING = 0x07
_BYTE = 0x0D
FLOAT32 = 0x88
_ENUM = 0x00
_SINT8 = 0x01
_UINT32 = 0x86

# Record-header bits, in the "normal" header form (bit 7 clear).
_DEFINITION_MESSAGE = 0x40
_DEVELOPER_DATA = 0x20

# Developer data is announced once per file under this index, which every
# `field_description` and every developer field then refers back to.
_DEV_DATA_INDEX = 0

# The 16-entry nibble table FIT defines its CRC-16 by.
_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            table = _CRC_TABLE[crc & 0x0F]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ table ^ _CRC_TABLE[nibble]
    return crc


@dataclass(frozen=True, slots=True)
class DevField:
    """A field the file defines for itself, through `field_description`.

    Here chiefly to reproduce the collision Suunto's exporter creates, where a developer
    field carries the same *name* as a profile field on the same message — and where the
    two disagree, the developer one being a `float32` rendering of a scaled integer.
    """

    name: str
    value: Any
    base_type: int = FLOAT32
    field_number: int = 0
    units: str | None = None


@dataclass(frozen=True, slots=True)
class Message:
    """One data message, named as the FIT profile names it."""

    name: str
    values: dict[str, Any] = field(default_factory=dict)
    dev_fields: tuple[DevField, ...] = ()


def message(name: str, *dev_fields: DevField, **values: Any) -> Message:
    """Declare one message: `message("session", sport="diving", max_depth=32.41)`.

    Values are given the way a human reads them — metres, seconds, `"diving"` — and the
    encoder applies the profile's scale factor and enum mapping, so a fixture never states
    a raw count.
    """
    return Message(name=name, values=values, dev_fields=dev_fields)


def _message_type(name: str) -> tuple[int, Any]:
    for global_number, message_type in MESSAGE_TYPES.items():
        if message_type.name == name:
            return global_number, message_type
    raise LookupError(f"no such FIT message in the profile: {name}")


def _field_def(message_type: Any, name: str) -> tuple[int, Any]:
    for number, profile_field in message_type.fields.items():
        if profile_field.name == name:
            return number, profile_field
    raise LookupError(f"no such field on FIT message {message_type.name}: {name}")


def _encode(base_type: int, raw: Any) -> bytes:
    if base_type == _STRING:
        return str(raw).encode() + b"\x00"
    if base_type == _BYTE and isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return struct.pack("<" + BASE_TYPES[base_type].fmt, raw)


def _raw_value(profile_field: Any, value: Any) -> Any:
    """A human-facing value as the number the file stores.

    The three transformations the profile describes, in the order a decoder undoes them: a
    `date_time` counts from the FIT epoch, an enum stores its numeric member, and a scaled
    field stores `(value + offset) * scale`.
    """
    field_type = profile_field.type
    if isinstance(value, datetime):
        return int((value - FIT_EPOCH).total_seconds())
    if isinstance(field_type, FieldType) and field_type.enum and isinstance(value, str):
        for number, member in field_type.enum.items():
            if member == value:
                return number
        raise LookupError(f"no such member of FIT enum {field_type.name}: {value}")
    if profile_field.scale:
        return round((value + (profile_field.offset or 0)) * profile_field.scale)
    return value


def _base_type_of(profile_field: Any) -> int:
    field_type = profile_field.type
    base = field_type.base_type if isinstance(field_type, FieldType) else field_type
    return int(base.identifier)


def _definition(
    local_type: int,
    global_number: int,
    fields: list[tuple[int, int, int]],
    dev: list[tuple[int, int, int]],
) -> bytes:
    """A definition record: what the bytes of the data record after it mean."""
    header = _DEFINITION_MESSAGE | (_DEVELOPER_DATA if dev else 0) | local_type
    out = bytearray([header, 0, 0])  # reserved, then architecture (0 = little endian)
    out += struct.pack("<H", global_number)
    out.append(len(fields))
    for number, size, base_type in fields:
        out += bytes([number, size, base_type])
    if dev:
        out.append(len(dev))
        for number, size, index in dev:
            out += bytes([number, size, index])
    return bytes(out)


def _encode_message(msg: Message, local_type: int) -> bytes:
    global_number, message_type = _message_type(msg.name)

    definitions: list[tuple[int, int, int]] = []
    payload = bytearray()
    for name, value in msg.values.items():
        number, profile_field = _field_def(message_type, name)
        base_type = _base_type_of(profile_field)
        encoded = _encode(base_type, _raw_value(profile_field, value))
        definitions.append((number, len(encoded), base_type))
        payload += encoded

    dev_definitions: list[tuple[int, int, int]] = []
    for dev_field in msg.dev_fields:
        encoded = _encode(dev_field.base_type, dev_field.value)
        dev_definitions.append((dev_field.field_number, len(encoded), _DEV_DATA_INDEX))
        payload += encoded

    return _definition(local_type, global_number, definitions, dev_definitions) + bytes([local_type]) + bytes(payload)


def _dev_declarations(messages: tuple[Message, ...]) -> list[Message]:
    """The preamble a file needs before it may carry developer fields at all.

    Emitted automatically, so a fixture only has to say which developer fields it wants.
    """
    declared = [dev for msg in messages for dev in msg.dev_fields]
    if not declared:
        return []

    preamble = [
        message(
            "developer_data_id",
            application_id=b"divejson-py tests",
            developer_data_index=_DEV_DATA_INDEX,
        )
    ]
    seen: set[int] = set()
    for dev in declared:
        if dev.field_number in seen:
            continue
        seen.add(dev.field_number)
        preamble.append(
            message(
                "field_description",
                developer_data_index=_DEV_DATA_INDEX,
                field_definition_number=dev.field_number,
                fit_base_type_id=dev.base_type,
                field_name=dev.name,
                **({"units": dev.units} if dev.units is not None else {}),
            )
        )
    return preamble


def _wrap(body: bytes) -> bytes:
    """The 12-byte header, the records, and the file CRC over everything before it."""
    header = bytearray([12, 0x20])
    header += struct.pack("<H", 2140)  # the profile version, cosmetic here
    header += struct.pack("<I", len(body))
    header += b".FIT"
    out = bytes(header) + body
    return out + struct.pack("<H", _crc16(out))


def fit_file(*messages: Message) -> bytes:
    """Assemble messages into a complete, CRC-correct FIT file, in the order given."""
    body = bytearray()
    for index, msg in enumerate(_dev_declarations(messages) + list(messages)):
        # A fresh local message type per message, cycling through the 16 available.
        # Simpler than tracking which definition is bound to which slot, and
        # indistinguishable to a reader.
        body += _encode_message(msg, index % 16)
    return _wrap(bytes(body))


def dive_file(*messages: Message, sport: str | None = "diving", session: dict[str, Any] | None = None) -> bytes:
    """The common shape: a `file_id`, whatever is passed, and one diving `session` last.

    The session comes last because that is where a device writes it — after the samples it
    summarises — and because the reader cuts the sample stream at the first one. `session`
    keywords are merged over a plausible dive, so a test that cares about one field says
    that field and inherits the rest; passing `None` for a member drops it, which is how a
    file that recorded no depth at all is built.

    `sport=None` leaves the member off entirely, which is the non-diving case.
    """
    fields: dict[str, Any] = {
        "start_time": STARTED_AT,
        "total_elapsed_time": 4301.72,
        "max_depth": 45.91,
        "avg_depth": 19.43,
    }
    if sport is not None:
        fields["sport"] = sport
    fields.update(session or {})
    return fit_file(
        message("file_id", type="activity", manufacturer="suunto"),
        *messages,
        message("session", **{name: value for name, value in fields.items() if value is not None}),
    )


def record_stream(count: int, *, start: datetime = STARTED_AT) -> bytes:
    """A file of `count` bare `record` messages, the way a device really writes one.

    `fit_file` emits a definition record per message, which is right for a fixture and
    wrong for the shape that decides cost: one definition followed by a long run of 10-byte
    data records. That is how a real file comes to hold half a million samples, and it is
    the shape `MAX_MESSAGES` exists for — so the encoder for it lives here rather than in a
    benchmark script nobody runs.
    """
    file_id_global, _ = _message_type("file_id")
    record_global, _ = _message_type("record")

    body = bytearray()
    # file_id: type = activity (4), manufacturer = suunto (23).
    body += _definition(0, file_id_global, [(0, 1, _ENUM), (1, 2, 0x84)], [])
    body += bytes([0]) + bytes([4]) + struct.pack("<H", 23)

    # timestamp (253, uint32), depth (92, uint32), temperature (13, sint8): a one-byte
    # record header and nine bytes of payload.
    body += _definition(1, record_global, [(253, 4, _UINT32), (92, 4, _UINT32), (13, 1, _SINT8)], [])
    origin = int((start - FIT_EPOCH).total_seconds())
    for index in range(count):
        body += bytes([1])
        body += struct.pack("<I", origin + index)
        body += struct.pack("<I", 1000 + (index % 40000))
        body += struct.pack("<b", 22 + (index % 4))
    return _wrap(bytes(body))
