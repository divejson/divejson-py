# Reading Shearwater Cloud's XML export into DiveJSON

**Non-normative.** The specification is [`spec/divejson.md`](https://github.com/divejson/divejson/blob/main/spec/divejson.md); nothing
here changes what a conforming document is. The rules every converter follows whatever it
is reading are in [`converting.md`](converting.md) and are **not repeated here**. This
document carries what is this format's, and **points at
[`shearwater-db-mapping.md`](shearwater-db-mapping.md) for every column the two exports
share** rather than restating the table: the XML is Shearwater's own decoding of the same
device log, and one table written once is one table that cannot disagree with itself.

Every claim below is checked against two files — Shearwater Cloud Desktop 2.12.10's XML
exports of two Perdix 3 dives, one open-circuit with no decompression and one decompression
dive on two gases, whose 402 and 488 records were compared one for one with the same dives'
logs in the database exports. [`fixtures/shearwater_xml/`](../fixtures/shearwater_xml) holds
both, reduced.

## Why this format, and what makes it different

Shearwater Cloud Desktop exports a dive it shows as XML: a `<dive version="3">` root holding
one `<diveLog>`, whose children are the header fields under the names of the database's
`dive_logs` columns — `gfMin`, `gfMax`, `imperialUnits`, `startDate`, `decoModel`,
`computerSerial`, `logVersion`, `product` … — and a `<diveLogRecords>` list with one
`<diveLogRecord>` per sample, whose children are named after the `dive_log_records` columns:
`currentTime`, `currentDepth`, `ttsMins`, `averagePPO2`, `fractionO2`, `fractionHe`,
`firstStopDepth`, `firstStopTime`, `currentNdl`, `currentCircuitSetting`, `waterTemp`, the
three `sensorNMillivolts`, `batteryVoltage`, `tank0pressurePSI` to `tank3pressurePSI`,
`gasTime`, `sac` and `sad`.

It is a per-dive export a diver reaches from the dive itself, which is why it is read beside
the database rather than only used to check it. It is also poorer: it has **no CNS, no GF99
and no ceiling** element, three readouts only the database carries, and no gas list, salinity
or dive mode in its header.

**It is the database reader's oracle.** For every column both carry, the database reader's
figure equals the XML's at the same `currentTime`, at the column's own resolution and through
the same sentinel rules — depth, time to surface, average ppO₂, the gas fractions, the next
stop's depth and time, the no-decompression time, the cells, temperature and battery voltage.
Where a sentinel rule would hide a disagreement on both sides at once, the comparison goes
under it: the database's stop time against `firstStopTime`, its no-decompression time against
`currentNdl` wherever `firstStopDepth` is `0` past the first record, and a database ceiling
only on records whose `firstStopDepth` is not `0`. The package's tests hold the pairs to that,
record by record.

## Parsing

### The shape is a `<dive>` holding a `<diveLog>`, and the format id is `shearwater_xml`

`shearwater_xml`, vendor then container, beside `shearwater_db`.

Suunto's DM5 export claims the same lowercased root, `dive`, told apart by the datacontract
namespace it declares on it. This one declares **no namespace of its own** on its root —
only the `xsd` and `xsi` prefixes — and carries a `version` attribute and a `<diveLog>` child,
so the sniff asks for exactly that: a `dive` root with no default namespace, a `version`, and
`<diveLog>` after it in the head. A DM5 export sniffs as `suunto_xml` and nothing else, and
this one as `shearwater_xml`.

### The declaration lies about the encoding

The file declares `encoding="utf-16"` and its bytes are ASCII. An XML parser handed the bytes
refuses it ("encoding specified in XML declaration is incorrect"), and one handed the decoded
text reads it. So the reader **decodes the bytes as UTF-8**, a byte-order mark allowed,
**re-encodes the text as UTF-8 with the declaration's encoding rewritten to say so**, and
parses that — through the package's one XML entry point, which refuses a `<!DOCTYPE>` before
anything expands. A file that opens on a UTF-16 byte-order mark is UTF-16, its declaration
true, and is parsed as it is.

### The dates are culture-formatted

`startDate` is written the way the exporting machine writes a date and a time —
`9/21/2026 10:48:17 AM`, the en-US shape, on both files in hand — and the file carries no epoch
beside it. The reader reads the digit pattern: three slash-separated numbers, the year four
digits and last, then a time, with a meridiem where the culture writes one and read as
24-hour where it writes none; the seconds may be missing, reported and read as `:00`. Of these
shapes the files in hand exercise the month-first meridiem one alone.

- **A field above 12 settles which is the day**, in either order.
- **Both fields 12 or below** read either way round. The date is read **month first**, the
  shape of the files in hand, and the dive's start is reported `resolved` with the day-first
  reading named, so a diver whose machine writes day first sees the swap on the dive it
  happened to. Where the two fields are equal both readings are one date and nothing is said.
- **A date the pattern does not read**, or no `startDate` at all, refuses the file with a
  sentence naming the string: one file is one dive, and a dive cannot be logged without a start.

The start is a wall clock with **no offset**, as the database's is.

### Several columns are words

`tank0pressurePSI` to `tank3pressurePSI` read `AI is off`, `N/A` or a number; `gasTime`
`N/A` or a number; `sac` `Waiting for initial data`, `Not Diving` or a number. Each is a
reading only where it is a number. `AI is off` is the tank status the database writes as
`0xFFFF`, reported the same way; `N/A` is no tank at all and says nothing.

## Units

The figures are already scaled, so the reader brings each back to the log's own unit and the
database document's table does the rest: `currentDepth` is metres or feet as `imperialUnits`
says, read to the tenth; `ttsMins`, `currentNdl` and `firstStopTime` are minutes;
`averagePPO2` is bar and `batteryVoltage` volts, read to the hundredth; `fractionO2` and
`fractionHe` are fractions, read as whole percent; `waterTemp` is whole degrees in the unit
`imperialUnits` names; `currentTime` is already milliseconds. Shearwater writes several of
them as float32 strings — `21.8000011`, `0.669999957` — which round to the device's
resolution exactly.

| source | DiveJSON member | factor |
| --- | --- | --- |
| `<maxDepth>` | `max_depth` | metres, ×1; imperial, feet × 0.3048 — **untested** |
| `<maxTime>` | `submerged_time` | seconds, ×1 |
| `<startSurfacePressure>` | the recording's `surface_pressure` | millibar ÷ 1 000 |
| `<startCns>`, `<endCns>` | the recording's `cns_start`, `cns_end` | percent, ×1 |
| `<fractionO2>`, `<fractionHe>` | `cylinders[].oxygen`, `helium` | fraction × 100 |
| `<currentTime>` | sample times | milliseconds, ×1 |
| the samples' columns | the profile | as the database document's table, once read back to the log's unit |

`<maxDepth>` is written as the file states it — `21.8000011` — since nothing a source records
is quantized; the database's closing record gives the same dive as `218` tenths.

## Identity

**Namespace: `4ea587e4-cff2-5b16-8bd0-ba264823f5b6`**, which is
`uuid5(NAMESPACE_URL, "https://divejson.org/ns/shearwater_xml")`. Frozen forever.

The export **records no id for its dive** — `<number>` is the computer's counter — so a dive
takes `converting.md`'s positional stand-in and the report says so, prefixed by the archive
member's name when a folder of exports is read as one logbook.

## The member map

### Provenance

`extensions.divejson.converted_from` is `shearwater_xml`, and
`extensions.divejson.shearwater_xml_version` is the root's `version` — `3` on both files in hand.
The file names no application version, so the document carries no `source_generator`.

### Device

| source | into (§6.4b) |
| --- | --- |
| — | `brand`, the literal `Shearwater Research, Inc` — the database document's rule |
| `<computerSerial>` | `serial` |
| `<computerFirmware>`, read as hexadecimal | `firmware` |
| `<number>` | `dive_number`, the computer's counter |

**`<computerFirmware>` is written in hexadecimal**: `27` on both files in hand, for the
firmware the database's log and its per-computer table both give as 39 — and
`<computerSoftwareVersion>` reads `27` beside it. It is read as hexadecimal and reported
`resolved`. A value that is not hexadecimal digits is reported and left out.

**No model**: `<product>` is a model code (`14`), which is no model under `converting.md`'s
*The Devices table*, and the export names the product nowhere. `<computerModel>` reads `0`
and is not read.

### The dive

| source | DiveJSON member | notes |
| --- | --- | --- |
| `<startDate>` | `started_at` | above, under *The dates are culture-formatted* |
| `<maxTime>` | `submerged_time` | `0` is reported as not recorded |
| `<maxDepth>` | `max_depth` | `0` is reported as not recorded |

The export carries no `dive_details`, so the dive has no `number`; the counter is the device's.

### The recording

`<decoModel>` reads as the database's opening 2: `0` GF with `<gfMin>` and `<gfMax>`, `1`
VPM-B and `2` VPM-B/GFS with `<vpmbConservatism>`, `3` DCIEM, anything else reported.
`<startSurfacePressure>` and `<startCns>`/`<endCns>` are the readouts. **The export states no
dive mode and no water density**, so the recording has neither. `currentCircuitSetting` is the
circuit each sample was breathed on — `OC/BO`, which a closed-circuit dive also shows while
on bail-out — and so not the recording's mode; it is reported as a field with no member.

### Cylinders

The export carries **no header gas list**, so each distinct `fractionO2`/`fractionHe` pair the
records breathe is a cylinder, in the order it first appears, numbered from 0; a pair that is
both zero names no gas. A change of the pair between two records is a gas switch, the database
document's rule.

### The profile

Each `<diveLogRecord>` is read into the same sample the database reader decodes, and the
profile is built from it by the database document's rules — its sentinels included, so
`currentNdl` `0` with `firstStopDepth` `0` is an absence on the first record and a reading of
0 s on every other, `currentNdl` is no no-decompression time while `firstStopDepth` is not
`0`, and `averagePPO2` `0` is no ppO₂. Where `externalPPO2` is set, the ppO₂ averaged the cells and is reported with them, as
the status bit is in the log. A record with no `<currentTime>` has no place on the axis and is
reported.

The three readouts the export lacks are absent, and nothing is said of them: **CNS, GF99 and
the ceiling** are carried by the database alone. A decompression dive's obligation still
reaches the profile here through `ttsMins`.

## This format settles two ambiguities

- **A date reading either way round** is read month first and reported `resolved`, the day-first
  reading named.
- **The firmware's digits** are read as the hexadecimal they are and reported `resolved`.

## The kinds this reader's report emits

- **`absent`** — the dive carries no id of its own; a start with no time zone, or with no
  seconds; a dive time or maximum depth of zero; a tank whose readings are all `AI is off`.
- **`resolved`** — the two above.
- **`dropped`** — every field of the database document's *What waits for a file* that a record
  carries a reading in — here the next stop's depth and time, the cells, the battery voltage,
  the gas time, the SAC and the circuit the computer shows; a deco model with no value; a
  surface pressure out of range; a negative CNS; a firmware that is not hexadecimal; and the
  profile rules every reader shares.
- **`inferred`** — never.

A start date the reader cannot read is not a finding: it refuses the file.

## Personal data

An XML export is one dive. Beyond the dive it carries the computer's serial, which the reader
carries as §6.4b's `serial`, as the database reader does, and nothing else about the diver or
their devices. The fixture keeps the serial, which the UDDF pairs already publish.

## What waits for a file

The database document's lists, column for column, wherever this export carries the column:
`firstStopDepth` and `firstStopTime`, the three `sensorNMillivolts`, `batteryVoltage`,
`gasTime` and `sac` have no member and are reported once per dive that carries a reading; the
tank pressures map under the same **untested** positional rule, tank 0 on the first cylinder.
The export carries none of the logbook's columns — no site, buddy, notes or conditions — so
the database document's *The logbook* has nothing to read here.

## Deliberately not mapped

- **`<sad>`** — a figure Shearwater does not document, `0` on every record in hand.
- **`setPointType`, `currentCcrModeSettings`, `circuitSwitchType`, `gasSwitchNeeded`** — the
  status byte's flags, written out as words; the database document's reasons.
- **The header's `surfaceMins`, `startBatteryVoltage`, `endBatteryVoltage`, `endDate`,
  `startO2SensorStatus`, `endO2SensorStatus`, the low and high setpoints, `errorHistory`,
  `endSurfacePressure`, `sensorDisplay`, `features`, `logVersion` and
  `computerSoftwareVersion`** — a surface interval, battery and sensor states, the end of a
  dive the dive time already gives, and figures with no member.
- **`<product>` and `<computerModel>`** — codes, above.

## The pairs

[`fixtures/shearwater_xml/`](../fixtures/shearwater_xml) holds the conformance pairs for this
format, written by Shearwater Cloud Desktop 2.12.10, each hand-reduced to its header and the
records chosen for what they carry, its declaration left as the application wrote it:

- `perdix3-dive-8.xml`, the XML export of the dive the database pair's second log records,
  kept to twelve records — the surface record with its no-decompression time of `0`, the
  first `99`, the temperature changes, the time to surface at its maximum of six minutes, the
  deepest record, the dive's end and the last record.
- `perdix3-deco-dive-11.xml`, the XML export of the decompression dive the database's
  `perdix3-deco-dive-11.db` holds, kept to fourteen records — the surface record, the
  no-decompression time counting to `0` with no stop, the first stop, the deepest stop, the
  time to surface at its maximum, the two gas switches, the stop clearing and the last record.

What each pair covers is a row in [`fixtures/README.md`](../fixtures/README.md); the rules
they follow are this document, [`shearwater-db-mapping.md`](shearwater-db-mapping.md) and
[`converting.md`](converting.md).
