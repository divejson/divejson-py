# Reading Shearwater Cloud's database export into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](https://github.com/divejson/divejson/blob/main/spec/divejson.md); nothing
here changes what a conforming document is. The rules every converter follows whatever it
is reading are in [`converting.md`](converting.md) and are **not repeated here** — the note
kinds, identity, the way a zero reads, the representability bound, sample ordering, a
container's caps. This document carries what is this format's.

Every claim below is checked against **one diver's whole-account export** — eight
open-circuit dives off a Perdix 3, written by Shearwater Cloud Desktop 2.12.10 on macOS — and
the layout against Shearwater's own decoding of the same records, the XML export of one of
those dives ([`shearwater-xml-mapping.md`](shearwater-xml-mapping.md)). That export is not in
this repository; what *is* here is [`fixtures/shearwater_db/`](../fixtures/shearwater_db),
the same file reduced to two of its dives. The closed-circuit, decompression and
transmitter branches have **no file in hand**, and every rule that rests on one is marked
**untested** where it is stated.

## Why this format, and what makes it different

Shearwater Cloud Desktop's *File → Export Whole Database* writes the diver's whole account —
every dive of every computer the account has synced — as one SQLite file. It is the richest
export Shearwater offers: its UDDF export keeps the no-decompression time, the ppO₂, the
gradient factor, the CNS and the deco model, its XML export a different fraction, and only
the database carries the whole of what the computer wrote.

**The dives are blobs, not rows.** The file has tables named for a decoded header and decoded
samples, `dive_logs` and `dive_log_records`, and in every export examined both are empty. Each
dive is one row of `log_data`, whose `data_bytes_1` is the dive computer's own log — the
Petrel Native Format — compressed. A reader that reads the decoded tables reads nothing.

**The log is the device's**, so the reader decodes bytes rather than columns, and the layout
it decodes is stated here in full: every offset, scale and sentinel, written from this
document and the fixtures. The layout's public references are libdivecomputer's
`shearwater_predator_parser.c` and the `pnf` specification; both are cited as references and
no code is taken from either. What proves the reading is the oracle: Shearwater's own XML
export of the same dive, sample for sample.

**A database is a container**, so it unpacks past its own size, and it is a stranger's SQLite
file, which carries its own risks. *Opening the file* says how both are bounded.

## Parsing

### SQLite's header is claimed, and the format id is `shearwater_db`

`shearwater_db` — vendor then container, the pattern `suunto_json` and `suunto_xml` set. Not
`shearwater_cloud`, which names the application and would collide in a reader's head with
UDDF's generator rule for Shearwater Cloud Desktop, whose UDDF is also a Cloud export.

The sniff claims **any head opening on SQLite's sixteen-byte header**, `SQLite format 3\0`.
What would tell a Cloud database from another SQLite file — the text of `log_data`'s
declaration — is not in a bounded head: the schema's first page is an interior page in every
export measured, and the declaration first appears past byte 42 000, on leaf pages whose
position moves with the file. This is the only SQLite reader in the corpus, and no other
format's syntax begins with that header, so claiming the container is the way an archive is
claimed by its magic.

**`convert` decides.** A database with no `log_data` table carrying `log_id`, `file_name`,
`format`, `format_version` and `data_bytes_1` is refused with a sentence saying it is a SQLite
database but not a Shearwater Cloud export — which is what a Suunto DM4 or DM5 logbook or a
Seac Sync database gets: claimed, refused, never converted and never passed as empty. A Cloud
export holding no logs converts to a document with no dives.

### Opening the file, and its bounds

The reader takes bytes and opens them in memory with `sqlite3.Connection.deserialize`, which
is why the package needs Python 3.11. A header naming write-ahead-log mode (bytes 18 and 19
equal to 2), which an in-memory copy cannot open, is rewritten to rollback on the copy — it
has no `-wal` file beside it. Every file in hand is rollback already. A file that carries the
header and is not a database SQLite can read is refused as a sentence.

The file is a stranger's, so the reader takes sqlite.org's precautions for an untrusted
database:

- `PRAGMA trusted_schema=OFF` and `PRAGMA query_only=ON` on the connection, so the reader's
  own statements evaluate nothing the file's schema defines and write nothing.
- **Every table the reader queries is checked to be a plain table** — `log_data`,
  `dive_details`, `DeletedLogs`, `SWC_TableVersion`: its `sqlite_master` row has `type`
  `table` and a non-zero `rootpage`, its declaration does not begin `CREATE VIRTUAL TABLE`,
  and `PRAGMA table_xinfo` shows no generated column (`hidden` 2 or 3) and no column named
  `rowid`, `_rowid_` or `oid`, which would hide the row id the reader addresses rows by. The
  column names the file declares are quoted as identifiers wherever a statement lists them. A view, a virtual
  table or a generated column would run the file's own code inside the reader's `SELECT`, and
  a database declaring one where a Cloud export has a table is refused. This check is what
  the package's floor allows in place of switching views and triggers off outright, which
  `Connection.setconfig` does from Python 3.12.
- **No value may be longer than one log's ceiling plus a page** — 4 MiB plus 64 KiB — through
  `Connection.setlimit(SQLITE_LIMIT_LENGTH, …)`. A log stored longer than that is measured,
  not read, and reported like any other log past the ceiling; a sidecar longer than a page is
  read as no sidecar. Every other column the reader reads holds a short string, and one past
  the cap is a file SQLite refuses to hand over, refused as a sentence.
- **A statement is stopped when it runs long.** A progress handler is called every 1 000
  virtual-machine instructions and stops a statement after 10 000 calls, or one per hundred
  bytes of the file if that is more. The heaviest statement the reader runs takes under two
  thousand instructions on the 254 KB export in hand.
- Of the extra layers sqlite.org lists for security-sensitive applications, the reader runs
  **`PRAGMA quick_check`** before reading anything, refusing a file that fails it, and sets
  **`cell_size_check=ON`**, so a damaged page is a refusal rather than a read past its end.
  `mmap_size=0` is set and changes nothing: an in-memory database maps no file.

**What is inside the file is bounded twice.** A caller's byte cap bounds the file, and a log
unpacks to more than it is stored as:

- **Each log has a ceiling of its own, 4 MiB**, close to two hundred times the longest log in
  the files examined (22 400 bytes, a closed-circuit Petrel 3 dive) and far above a
  several-hour log at the device's 10 s default, which stays under 100 KB. A log declaring a
  length of zero or more than the ceiling is reported and left packed.
- **The declared lengths of every log the reader would unpack are summed first**, and spent
  against the caller's inflation budget — `divejson.convert`'s `max_inflated_size`, which the
  conversion carries as the scope's `InflationBudget`, one budget for the whole upload — before
  any log inflates. A file past it is refused whole. With no budget given, nothing is capped
  but the ceiling. `divejson.shearwater_db_declared_size` returns the same sum without
  unpacking anything, for a caller budgeting several files before converting any.
- **Inflation is bounded to the declared length plus one** and must end exactly at the
  declared length with the gzip member's checksum read; a log that unpacks to anything else
  is reported, and the file's other dives convert.

### The rows

One dive per `log_data` row whose `format` is `sw-pnf` and whose `format_version` is `0` —
every row seen. A row stored any other way — `sw-clouddb` and `sw-predator` are the tags
other sources report — a log that does not unpack to its declared length or to whole 32-byte
records, a log missing a required record, a body beginning `FF FF`, the older
Predator-style layout, and a row whose `log_id` is null — the column every export declares
`not null`, and the dive's identity — are each reported `dropped`, naming the row's `file_name` and the
reason, and the other rows convert.

`dive_details` is joined on `log_data.log_id = dive_details.DiveId`. A log with no
`dive_details` row converts from the log alone; a `dive_details` row with no log is reported.
A log whose `log_id` a `DeletedLogs.DiveID` names is reported `dropped` as deleted in
Shearwater Cloud rather than converted — **untested**, no file in hand deletes one.

Dives keep the order of `log_data`'s rows. A finding's `where` is `dive/<n>`, the row's place
in that order, and a sample's `dive/<n>/sample/<m>`; a `dive_details` row with no log is
`dive_details/<n>`.

### The blob

`data_bytes_1` is a four-byte little-endian length followed by one gzip member whose output
is exactly that many bytes, a whole number of 32-byte records. Bytes after the member exist
in most logs (831 and 2 788 in the two in the pair) and carry nothing.

### The records

Byte 0 of each record is its type. The log walks them in order:

| type | record | read |
| --- | --- | --- |
| `0x10`–`0x19` | opening records 0 to 9 | the header, below |
| `0x01` | sample | the profile, below |
| `0x20`–`0x29` | closing records 0 to 9 | the dive's end, below |
| `0xFF` | the final record | the device |
| `0x30` | an info event | reported, not carried |
| `0x70`–`0x75` | tissue snapshots | reported, not carried |
| `0x80`–`0x87`, `0x90`, `0xA0`, `0xA1`, `0x51` | a closed-circuit Petrel 3's validity maps and periodic records | reported, not carried |
| `0x02`, `0x03` | freedive and Avelo samples | reported, not carried — **no file in hand** |
| all thirty-two bytes zero | padding, before and after the final record | skipped silently |
| anything else | unknown | skipped, and counted once in the report |

**The required set is opening records 0 to 4, closing record 0 and the final record**; a log
missing one is reported and left out.

**Sample times are the sample's ordinal among the `0x01` records times the interval**, the
first sample at 0 — the convention Shearwater's own XML export uses (`currentTime` 0, 10000,
20000 …). The `0x30` info record that sits between the first two samples of every log in
hand does not count. The interval is opening 5's bytes 23–24 where the log version is 9 or
later, else the device's fixed 10 000 ms; every file in hand logs at 10 s.

## Units

Every multi-byte field is big-endian unless a row says otherwise.

| source | DiveJSON member | factor |
| --- | --- | --- |
| closing 0, bytes 4–5 | `max_depth` | tenths of a metre **÷ 10**; imperial, tenths of a foot × 0.03048 — **untested** |
| closing 0, bytes 6–8 | `submerged_time` | seconds, ×1 |
| opening 1, bytes 16–17 | the recording's `surface_pressure` | millibar ÷ 1 000 |
| opening 0 / closing 0, bytes 10–11 | the recording's `cns_start` / `cns_end` | percent, ×1 |
| opening 0, bytes 20–29; opening 0, 30–31 and opening 1, 1–8 | `cylinders[].oxygen`, `helium` | percent, ×1 |
| sample, bytes 1–2 | `profile.depth.values` | tenths of a metre **× 10** — centimetres; imperial, tenths of a foot × 3.048 — **untested** |
| sample, byte 14 | `profile.temperature.values` | whole °C **× 100** — hundredths; °F converted to °C first — **untested** |
| sample, byte 10 | `profile.ndl.values` | minutes **× 60** — seconds |
| sample, bytes 5–6 | `profile.tts.values` | minutes **× 60** — seconds |
| sample, byte 7 | `profile.ppo2.values` | hundredths of a bar, ×1 |
| sample, byte 23 | `profile.cns.values` | whole percent **× 10** — tenths |
| sample, byte 25 | `profile.gradient_factor.values` | whole percent, ×1 |
| sample, byte 24 | `profile.ceiling.values` | whole metres **× 100** — centimetres; imperial, feet × 30.48 — **untested** |
| sample, bytes 28–29 and 20–21 | `profile.pressures[].values` | low twelve bits × 2 psi, × 0.0689475729 to bar, **× 10** — tenths — **untested** |
| ordinal × interval | sample times | milliseconds, ×1 |

**The channel conversions carry a scale the scalar ones do not**, as everywhere: a depth of
`218` tenths is 21.8 m on the dive and `2180` in the channel.

## Identity

**Namespace: `6235a5bc-a5d6-5b22-ac68-2da79dd6438b`**, which is
`uuid5(NAMESPACE_URL, "https://divejson.org/ns/shearwater_db")`. Frozen forever — changing it
renumbers every document any released version of this converter has produced from a Cloud
database.

A dive's source id is **`log_data.log_id`**, which equals `dive_details.DiveId`, the id the
file itself carries for the dive. Not the dive number, which is the computer's counter and
restarts on a new computer, and not `file_name`, which a later export can spell differently.

## The member map

### Provenance

`extensions.divejson.converted_from` is `shearwater_db`, and
`extensions.divejson.shearwater_db_version` is the highest `SWC_TableVersion.DbVersion` — `12`
on every file in hand — as text. The file names no application version anywhere, so the
document carries no `source_generator`.

### Device — the final record and `file_name`

| source | into (§6.4b) |
| --- | --- |
| — | `brand`, the literal `Shearwater Research, Inc` |
| `log_data.file_name`, before its `[` — `Perdix 3` | `model` |
| final record, bytes 2–5, as eight upper-case hexadecimal digits — `D9772626` | `serial` |
| final record, byte 10, binary-coded decimal — `0x39` is `39` | `firmware` |
| opening 0, bytes 2–3 | `dive_number`, the computer's own counter |

**The brand is the string Shearwater's own UDDF export writes for its computers**, and the
string the UDDF reader carries (`fixtures/uddf/shearwater-cloud.divejson`), so that one
computer read from either export is one device. Like the two literal `Suunto`s it is not a
fabrication: a vendor-proprietary export is the vendor saying so.

**The model is the name the source wrote**, the device's own name for its log —
`Perdix 3[D9772626]#8 2026-9-21 10-48-17.swlogzp` — and never the final record's byte 13,
which is a model code and so no model under *The Devices table* in `converting.md`. The code
is checked against the name, by the codes the layout's references agree on (2 Predator,
3 Petrel, 4 Nerd, 5 Perdix, 6 Perdix AI, 7 Nerd 2, 8 Teric, 9 Peregrine, 10 Petrel 3,
11 Perdix 2, 12 Tern, 14 Perdix 3), and a disagreement is reported with the name kept. A
firmware byte that is not binary-coded decimal is reported and left out.

The serial in `file_name`'s brackets, `dive_details.SerialNumber` and the final record's are
one serial; the final record's is the one read.

### The dive

| source | DiveJSON member | notes |
| --- | --- | --- |
| opening 0, bytes 12–15 | `started_at` | seconds on the device's own wall clock, written with **no offset** — §5.2's third state, and the reading `uddf-mapping.md` keeps for this generator's `Z` |
| `dive_details.DiveNumber` | `number` | the logbook's number, which Shearwater Cloud writes to UDDF as `<divenumber>`; the device counter is the device's `dive_number`, `converting.md`'s split between the two |
| closing 0, bytes 6–8 | `submerged_time` | the device's own dive time; `0` is reported as not recorded |
| closing 0, bytes 4–5 | `max_depth` | `0` is reported as not recorded |

**The start is the wall clock the diver read off the wrist.** A log of version 9 or later
also carries the time zone the computer was set to, opening 5's bytes 26–29 (minutes, signed)
with a daylight-saving flag at byte 30. It is **decoded and not applied**: the files in hand
write it two ways for one place — 180 minutes with the flag clear on two dives, 120 with it
set on six — and what the flag adds is settled by no file in hand, so the start keeps its
wall clock and the report says the zone was there and was not read. Where the zone is zero or
the log too old to carry it, the report says the file gives no zone.

`dive_details.DiveDate`, `Depth` and `DiveLengthTime` restate the log and are not consulted.
The dive's `avg_depth` is not stated by the log and is not derived: `converting.md` leaves it
absent until a file in hand needs the derivation.

### The recording — opening records 2, 3 and 4

| source | into (§6.4a, §6.4c) | rule |
| --- | --- | --- |
| opening 4, byte 1, where the log version (byte 16) is 8 or later | `mode` | 0 CC and 5 CC2 → `closed_circuit`; 1 OC Tec and 6 OC Rec → `open_circuit`; 4 SC → `semi_closed`; 2 Gauge → `gauge`; 7 Freedive → `freedive`; 3 PPO2, 12 Avelo and anything else → reported, left empty |
| opening 2, byte 18 | `deco_model` | 0 GF → `algorithm: buhlmann`, `gf_low` and `gf_high` from opening 0's bytes 4 and 5; 1 VPM-B and 2 VPM-B/GFS → `name` (`VPM-B`, `VPM-B/GFS`) and `conservatism` from opening 2's byte 19, no `algorithm`, the enum having none for it; 3 DCIEM → `name`; anything else reported |
| opening 3, bytes 3–4 | `salinity` | 1000 → `fresh`, 1020 → `en13319`, 1030 → `salt` — Shearwater's three settings are the enum's three values; anything else reported |

The surface pressure and the CNS at the dive's two ends are the recording's readouts. A
surface pressure outside §6.4a's 0.4 to 1.2 bar is reported and left out. The VPM
conservatism byte is written on a GF dive too — `3` on every dive in hand — and is not read
there: a Bühlmann model has no VPM conservatism.

### Cylinders — the gas list

One cylinder per **enabled** gas slot, in slot order, carrying `oxygen`, `helium` and
`gas_number`, which is the slot: ten slots, oxygen at opening 0's bytes 20–29, helium at
opening 0's 30–31 for the first two and opening 1's 1–8 for the rest, and opening 4's bytes
17–18 a mask with a bit per slot. Slots 5 to 9 are the diluents, and a diluent on a
closed-circuit dive keeps its slot. A slot that names a mix and is switched off in the mask
is reported and left out. A `99/0` is read as 99, never promoted to 100. Nothing else about a
cylinder is in the log.

### The profile — the sample record

| bytes | field | into | absence |
| --- | --- | --- | --- |
| 1–2 | depth, tenths | `depth` | — |
| 3–4 | the next stop's depth, whole metres or feet | no member | `0`: not in deco at that sample |
| 5–6 | time to surface, minutes | `tts` | — |
| 7 | average ppO₂, hundredths of a bar | `ppo2` | `0`; and the whole byte where the status's external-ppO₂ bit is set |
| 8, 9 | the breathed mix's O₂ and He percent | gas switches | both `0` |
| 10 | no-decompression time, minutes, where bytes 3–4 are `0`; else the next stop's time | `ndl`; the stop time has no member | `0` with no stop |
| 11 | battery percent | no member | — |
| 12 | status: `0x10` open circuit, `0x02` external ppO₂, `0x01` gas switch needed | read for the ppO₂ and the mix | — |
| 13, 15, 16 | O₂ cells 1 to 3, millivolts | no member | `0` with the cell's bit clear in opening 3's byte 6 |
| 14 | water temperature, whole degrees, signed | `temperature` | — |
| 17–18 | battery voltage, hundredths of a volt | no member | `0` |
| 19 | setpoint, hundredths of a bar | no member | `0` |
| 20–21 | tank 1 | `pressures` | a word at or above `0xFFF0` |
| 22 | gas time, minutes | no member | at or above `0xF0` |
| 23 | CNS, whole percent | `cns` | — |
| 24 | ceiling, whole metres | `ceiling` | `0` |
| 25 | GF99, whole percent | `gradient_factor` | `0xFF` |
| 26–27 | time to surface in five minutes, minutes | no member | `0xFFFF` |
| 28–29 | tank 0 | `pressures` | a word at or above `0xFFF0` |
| 30–31 | SAC | no member | at or above `0xFFF0` |

**A below-zero temperature** is a signed byte below zero, read by the wrap the layout's
references give: 102 is added, and a result above zero is zero — **untested**, no file in hand
is that cold. An imperial log's temperature is Fahrenheit, converted to Celsius.

**The ceiling and the gradient factor are mapped untested by a pair**: no file in hand dives
into decompression. The ceiling's `0` is absence, the channel being present only while a
ceiling existed (§6.5), and GF99's `0xFF` is the device saying it has no figure while it
on-gasses, where its `0` is a reading. Under the DCIEM model the two bytes are a safe-ascent
depth rather than a ceiling and a gradient factor, so a DCIEM dive's are reported and not
carried.

**A tank word** at or above `0xFFF0` is the transmitter's status — `0xFFFF` air integration
off, `0xFFFE` and `0xFFFD` no communication, `0xFFFC` not paired — reported once per tank and
status on a dive whose tank carries no pressure at all; below it, the low twelve bits are the
pressure in units of 2 psi and the high four the transmitter's battery, which has no member.
A pressure of zero is no reading. **Which cylinder a transmitter's series hangs on is
untested** until a file with a paired transmitter exists, and the rule written here is
positional: tank 0 on the first cylinder, tank 1 on the second, and a tank with no cylinder
in its place makes a cylinder of its own, carrying the series and nothing else. A reading past
350 bar is reported and left out.

**A gas switch is a change of the breathed mix between two samples**, a `gas_switch` event at
the later sample naming the cylinder whose slot holds that mix — a diluent's where the status
says closed circuit. The first sample's mix is the starting gas, not a switch. A mix no slot
holds is a switch naming no cylinder, reported.

**Nothing is derived.** A ppO₂ from depth and fraction, a ceiling from a stop depth, a SAC
from pressures: §6.4's computed readouts are the computer's, and the reader writes only what
the computer wrote.

## Sentinels and caps

What this device writes for "no figure", per quantity, each settled against the files in hand
unless marked:

- **No-decompression time `0` with a stop depth of `0`** — absence. The first sample of seven
  of the eight logs carries it, at the surface between 0 and 1.5 m, with `99` on the next
  sample; Shearwater's own UDDF export of the same dive writes no `<nodecotime>` on that
  waypoint, and a no-decompression time of nothing at the surface is no reading.
- **No-decompression time `99`** — a reading at the display cap, `5940` s, as
  `uddf-mapping.md` already rules for this device.
- **ppO₂ `0`** — absence. It occurs once in the eight logs, on one dive's first sample at the
  surface, with depth, time to surface and no-decompression time also `0`; a ppO₂ the computer
  computed is never nothing.
- **GF99 `0xFF`** — absence; `0` is a reading.
- **A ceiling of `0`** — absence — **untested** by a pair.
- **A stop depth of `0`** — the dive is not in deco at that sample.
- **A tank word at or above `0xFFF0`** — absence, the status named once per tank. Every tank
  word in hand is `0xFFFF`.
- **The gas-time byte at or above `0xF0`** — absence; a status, as the tank words are.
- **The SAC word at or above `0xFFF0`** — absence. The eight logs carry only `0xFFF9` and
  `0xFFFA`; a SAC has no member either way.
- **Cells `0` with the calibration mask `0`** — no cells. Every cell in hand is `0`.
- **`-1`, `2147483647` and `-2147483648` in `calculated_values_from_samples`** — that summary's
  own absences, never read.

## The sidecars are cross-checks

Beside each log, `data_bytes_2` is a JSON sidecar (`DIVE_NUMBER_KEY`, `DIVE_START_TIME`,
`DB_VERSION`), `data_bytes_3` the device's manifest entry (`StartTime`, `EndTime`,
`DiveTimeInSeconds`, `MaxDepth` …), `data_bytes_4` is null, and
`calculated_values_from_samples` is Shearwater Cloud's own figures for the dive. **None is
read into the document**: every figure in them is in the log, and the log is what the device
wrote. Five are compared with it — `DIVE_START_TIME` with opening 0's ticks,
`DiveTimeInSeconds` and `MaxDepth` with closing 0's, `dive_details.SerialNumber` with the final
record's, and the first `TankProfileData.TankData` entry's `SurfacePressureMBar` and `Salinity`
with opening 1's pressure and opening 3's density — and a disagreement is reported, the log's
figure kept.

## This format settles no ambiguity

Every field of the log has one unit, stated by the log's own units byte (opening 0, byte 8:
`0` metric, `1` imperial; anything else and the log is reported and left out). The reader emits
no `resolved` finding.

## The kinds this reader's report emits

- **`absent`** — a start with no time zone in the file; a dive time or maximum depth of zero;
  a tank whose readings are all a transmitter status; tank readings no cylinder sits in the
  place of, arriving as a cylinder of their own.
- **`dropped`** — a log stored another way, empty, past the ceiling, not unpacking to its
  length, missing a required record, in the Predator layout, deleted or with no `log_id`; a logbook row with no
  log; the time zone the computer holds; a mode, deco model or water setting with no value; a
  surface pressure out of range; a switched-off gas; a firmware byte that is not decimal; a
  model code that disagrees with the name; a summary that disagrees with the log; every field
  and record of *What waits for a file*; records of an unknown kind; a gas switch to no slot;
  a tank pressure past 350 bar; and the profile rules every reader shares.
- **`inferred`** — never. The reader computes nothing, and `extensions.divejson.inferred` is
  never written.
- **`resolved`** — never, as above.

## Personal data

A Cloud database is the diver's whole account, and carries more about the diver and their
devices than any dive does: the computer's serial in three places (`dive_details.SerialNumber`,
the log's final record, and `StoredDiveComputer`'s decimal form of it), the diver's own name
for the computer (`CustomDiveComputer.JsonData`'s `CustomerComputerName`), the exporting
installation's device identifiers (`IdToDiveComputer`), and the sync metadata naming which
device created and last changed each dive (the `SyncV3Metadata*` tables). §9 calls a document
a personal dossier and the serial a stable hardware identifier.

The reader carries **the serial**, as §6.4b's `serial` — as the UDDF and FIT readers carry
it — and **none of the rest**: it reads no per-computer or sync table at all. The fixture is
reduced on the same line: it keeps the serial, already published in the UDDF pairs, and drops
every `StoredDiveComputer`, `CustomDiveComputer`, `IdToDiveComputer` and `SyncV3Metadata*` row.

## What waits for a file

A member lands only where a real file carries it. In the files in hand every logbook column
of `dive_details` is empty, every tank word is a status, every stop depth and ceiling is zero
and every cell is zero, so the reader decodes all of it and does this with each.

**No member yet** — reported `dropped` under the field's own name, once per dive that carries
a reading, and never mapped. This is the set a follow-on version of the format would add
members for: the next stop's depth and time; the setpoint; the three cells' millivolts; the
battery voltage and percent; the gas-time and SAC words; the time to surface in five minutes;
the tissue snapshots; the info events; and a closed-circuit Petrel 3's validity maps and
periodic records. So are the freedive and Avelo samples, which have no file in hand.

**A member, no file** — `profile.ceiling` and `profile.pressures` are mapped by the rules
above, marked **untested by a pair**. The `dive_details` columns are not mapped at all: each
one that is filled in is reported `dropped` by its own name until a file shows what Shearwater
Cloud puts in it, and is then mapped or listed below with its reason. The shape of each is
unknown until then — whether Buddy is one name or several, how a GNSS fix is spelled, what
unit a typed pressure carries — which is why none is mapped on a guess. The list is
`PRAGMA table_info(dive_details)` read against the schema's dive and cylinder properties:

- Site and Location → the dive site and its locality; Buddy → a person; Notes → `notes`;
  GnssEntryLocation and GnssExitLocation → the dive's positions; Tank1PressureStart and
  Tank1PressureEnd → the cylinder's pressures; TankSize → a cylinder's `volume`;
  AirTemperature → `air_temperature`; Platform → `entry_type`; Weather → `weather`;
  Visibility → `visibility`; Weight → `weight`; Environment → `water_type`, where its
  vocabulary turns out to be one.
- Conditions, EnvironmentNotes, GasNotes, GearNotes, Dress, Apparatus, ThermalComfort,
  Workload, Problems, Malfunctions, Symptoms, ExposureToAltitude, IssueNotes, AveloNotes,
  Other1 to Other3, DateAndTime, GnssCustomWaypoints, AverageSAC and the Tank2 to Tank4
  pressures → nothing yet; each is mapped or refused against the file that fills it.

So does the **entry and exit position** a log of version 17 or later can carry in opening and
closing record 9 — a fix type at byte 16 (2 or 3 for a fix), latitude at bytes 21–24 and
longitude at 25–28 in hundred-thousandths of a degree. No file in hand has a fix, so a fix is
reported and not read.

## Deliberately not mapped

- **`dive_logs` and `dive_log_records`** — empty in every export examined. A database that
  carried rows in them is read from its logs regardless.
- **`data_bytes_2`, `data_bytes_3`, `data_bytes_4` and `calculated_values_from_samples`** —
  cross-checks only, above.
- **`dive_details`' housekeeping** — `DataVersion`, `FileName`, `LastModified`,
  `MainTimestampReliable` — and **the five floats Shearwater Cloud computes**, `AverageDepth`,
  `AverageTemp`, `MinTemp`, `MaxTemp` and `EndGF99`, each `0.0` on every row in hand.
- **`TankProfileData`** — a JSON gas list with each gas's span and six transmitter entries; the
  log's own gas list is what is read, and two of its figures are cross-checks.
- **The status byte's other bits** — the gas-switch-needed flag and a setpoint type: the
  first is a prompt the computer showed, the second describes the setpoint, which has no
  member.
- **The final record's model code** — a code is no model; it is checked against the name.
- **`StoredDiveComputer`, `CustomDiveComputer`, `IdToDiveComputer` and `SyncV3Metadata*`** —
  the account's devices and its sync history, not a dive. *Personal data* above.
- **Opening 0's other bytes, the other closing records and the other opening records** — the
  layout's references do not agree on them, and no member waits for any.
- **A gear item for the computer** — the database has no equipment list to make one from, and
  the FIT reader makes none either.

Shearwater Cloud's mobile *Export account* writes a zip holding `dive_data.db` beside a
`SyncReport.txt`. The database inside is read by the same rules, with no pair; the zip itself
is refused by the container rule in `converting.md`, since a sync report is no dive log, and
an application that wants the database reads the archive member by member.

## The pairs

[`fixtures/shearwater_db/`](../fixtures/shearwater_db) holds the conformance pairs for this
format — an input, and the document a correct reader produces from it. Their writer is
Shearwater Cloud Desktop 2.12.10. `perdix3-dives-1-and-8.db` is that export reduced to
two dives: dive 1, the shortest (23 samples, 33 % nitrox, 4.0 m), and dive 8, the one
[`fixtures/shearwater_xml/`](../fixtures/shearwater_xml) holds the XML export of. A log cannot
be reduced by hand, so the two are committed whole, as recorded; the reduction is the
database's — the other six `log_data` and `dive_details` rows deleted, every per-computer and
sync row deleted, every table and index kept, and `VACUUM` run. What each pair covers, and how
it was built, is one row per pair in [`fixtures/README.md`](../fixtures/README.md); the rules
those expectations follow are this document and [`converting.md`](converting.md).
