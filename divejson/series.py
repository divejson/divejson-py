"""Building a §6.5 profile out of whatever a source recorded, once for every format.

Sources disagree about almost everything here and DiveJSON does not. UDDF puts every
reading taken at one instant inside one `<waypoint>`; Subsurface's `.ssrf` writes a
`<sample>` with the attributes it has; FIT writes a `record` message per second with
invalid-value sentinels for the channels a device did not carry; the Suunto app's JSON
writes an array of objects with their own timestamps; Suunto's DM5 XML writes a
`<Dive.Sample>` carrying every channel, `i:nil` where the sensor had nothing. What §6.5
wants out of all five is the same: channels sampled on their own axes, with strictly
increasing integer times.

The rules that survive that translation are the ones in here, and no adapter re-derives
them:

* **Samples are ordered by their own recorded time, never by their position in the file.**
  §6.5 requires strictly increasing `times` and no writer guarantees it emitted them in
  order.
* **A sample with no time has no place on the axis**, and is dropped and reported. Nothing
  else can put a reading anywhere.
* **Two samples on one second keep the first and report the second**, because the times
  are integers and strictly increasing while a source's are usually neither.
* **The samples set the time axis and each channel takes only the samples that carried a
  reading for it.** No channel is padded to another's length: a Subsurface dive keeps 431
  depths beside 29 temperatures rather than inventing 402 readings.
* **Samples that carry a time and no usable reading produce no profile at all**, rather
  than one with a bare `duration: 0`. A zero-length sampled record is a claim the source
  did not make. That is reported, unlike a dive that recorded no samples in the first
  place: there the source said nothing, here it said something this converter could not
  carry.

`noun` and `time_member` exist so the report keeps speaking the source's language — a UDDF
report says "the waypoint records no `<divetime>`" where a FIT report says "the record
records no timestamp". The rule is shared; the words a diver reads are the format's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .converter import Reporter

__all__ = ["Channel", "SampleAxis"]


class Channel:
    """One sampled quantity: the seconds it has readings at, and the readings.

    Values are integers in the units §6.5 fixes for each channel — centimetres of depth,
    tenths of a degree, tenths of a bar — so the scaling is the adapter's and the ordering
    is this class's.
    """

    __slots__ = ("times", "values")

    def __init__(self) -> None:
        self.times: list[int] = []
        self.values: list[int] = []

    def record(self, second: int, value: int) -> bool:
        """Take one reading, unless this channel already has one at that second.

        The axis has already deduplicated the samples, so a repeat here means two readings
        of one channel inside one sample — two tank pressures resolving to one cylinder,
        say. The first wins and the caller reports the second, because the caller is what
        knows which reading it was.
        """
        if self.times and self.times[-1] == second:
            return False
        self.times.append(second)
        self.values.append(value)
        return True

    def __len__(self) -> int:
        return len(self.times)

    def member(self) -> dict[str, list[int]]:
        return {"times": self.times, "values": self.values}


class SampleAxis:
    """The time axis of one dive's profile, and the profile built on it.

    An adapter offers every sample it found with the second it was recorded at, then walks
    `ordered()` to fill its channels, then asks for `profile()`. What is dropped on the way
    is reported through the `note` this was built with.
    """

    __slots__ = ("_note", "_where", "_noun", "_time_member", "_seen", "_offered", "_ordered")

    def __init__(
        self,
        note: Reporter,
        where: str,
        *,
        noun: str = "sample",
        time_member: str = "time",
    ) -> None:
        self._note = note
        self._where = where
        self._noun = noun
        self._time_member = time_member
        # Every sample offered, not only the ones that found a place: a note's path counts
        # samples in document order, because it is a path back into the file a diver would
        # open. Counting the kept ones would number a dropped sample after the last good
        # one, which is somebody else's element.
        self._seen = 0
        self._offered: list[tuple[int, Any]] = []
        self._ordered: list[tuple[int, Any]] | None = None

    def offer(self, second: int | None, payload: Any) -> None:
        """One of the source's samples, with the second it recorded — or `None` for none.

        `payload` is whatever the adapter needs to read the sample's channels off again on
        the second pass; this class never looks inside it.
        """
        at = f"{self._where}/{self._noun}/{self._seen}"
        self._seen += 1
        if second is None:
            self._note(
                at,
                f"the {self._noun} records no {self._time_member}, so it has no place on "
                "the profile's time axis; dropped",
                "dropped",
            )
        elif second < 0:
            self._note(at, f"the {self._noun} is at {second} s, before the dive began; dropped", "dropped")
        else:
            self._offered.append((second, payload))

    def ordered(self) -> list[tuple[int, Any]]:
        """The samples that have a place on the axis, in recorded-time order."""
        if self._ordered is None:
            kept: list[tuple[int, Any]] = []
            for second, payload in sorted(self._offered, key=lambda pair: pair[0]):
                if kept and kept[-1][0] == second:
                    self._note(
                        self._where,
                        f"two {self._noun}s share the second {second}; the later one is dropped, because "
                        "the format's sample times are strictly increasing (spec §6.5)",
                        "dropped",
                    )
                    continue
                kept.append((second, payload))
            self._ordered = kept
        return self._ordered

    def profile(
        self,
        channels: Mapping[str, Channel],
        *,
        pressures: Sequence[tuple[int, Channel]] = (),
        events: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any] | None:
        """§6.5's Profile from the channels an adapter filled, or nothing at all.

        `channels` are the named single channels — `depth`, `temperature`, `ceiling` — in
        the order the document should carry them; `pressures` are `(gas_number, channel)`
        pairs, emitted in gas-number order. `duration` is the span of the samples
        themselves, which §6.4 defines it as: the largest sample time across every channel,
        a structural member rather than a reading the source failed to record, so it
        carries no note and is never listed as inferred.
        """
        filled: list[tuple[str, Channel]] = [(name, channel) for name, channel in channels.items() if len(channel)]
        pressed = sorted(((number, channel) for number, channel in pressures if len(channel)), key=lambda pair: pair[0])
        if not self.ordered():
            # Nothing had a place on the axis. Every one of those samples was reported as
            # it was dropped, so there is nothing further to say.
            return None
        if not (filled or pressed or events):
            count = len(self.ordered())
            subject = f"{self._noun} carries" if count == 1 else f"{self._noun}s carry"
            self._note(
                self._where,
                f"the dive's {count} {subject} a time but no reading this format can hold, so it "
                "arrives with no profile at all rather than one of zero length",
                "dropped",
            )
            return None

        latest = max(
            (channel.times[-1] for _, channel in (*filled, *pressed)),
            default=0,
        )
        profile: dict[str, Any] = {"duration": latest}
        for name, channel in filled:
            profile[name] = channel.member()
        if pressed:
            profile["pressures"] = [
                {"times": channel.times, "values": channel.values, "gas_number": number}
                for number, channel in pressed
            ]
        if events:
            profile["events"] = sorted(events, key=lambda event: event["time"])
        return profile
