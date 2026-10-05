# Copyright (c) 2013-2022 khal contributors
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""An hour-grid day and week view for ikhal.

The module is split in three layers, only the last one depends on urwid's
widget machinery:

* :func:`layout_day` turns the events of one day into :class:`Block`\\ s
  (start/end in minutes, plus a lane so overlapping events end up side by side)
* :func:`render_grid` paints those blocks onto a character grid and returns it
  as rows of ``(urwid attribute, text)`` segments
* :class:`GridView` is the box widget that ikhal puts next to the calendar
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import urwid

GUTTER = 6  # width of the "09:00 " hour labels
MAX_ALLDAY_ROWS = 3
MAX_ROWS_PER_HOUR = 8
MIN_WIDTH_FOR_TIME = 16  # narrower blocks only show the title


@dataclass
class Block:
    """one event (or the part of it falling on `day`) positioned on the time axis"""

    event: Any
    day: dt.date
    start: int  # minutes since midnight, already clipped to the visible range
    end: int
    lane: int = 0
    lanes: int = 1


@dataclass
class DayLayout:
    day: dt.date
    blocks: list[Block] = field(default_factory=list)
    allday: list[Any] = field(default_factory=list)
    before: int = 0  # timed events ending before the first visible hour
    after: int = 0  # timed events starting after the last visible hour


def _minutes(day: dt.date, moment: dt.datetime, *, end: bool) -> int:
    """minutes since midnight of `day`, clamped to 0 .. 24*60"""
    if moment.date() < day:
        return 0
    if moment.date() > day:
        return 24 * 60
    minutes = moment.hour * 60 + moment.minute
    if end and minutes == 0:
        return 24 * 60
    return minutes


def assign_lanes(blocks: list[Block]) -> None:
    """put overlapping blocks into lanes, in place

    Blocks are grouped into clusters of transitively overlapping events, every
    cluster is laid out on its own so a lonely event keeps the full width even
    if it shares the day with a crowded hour.
    """
    blocks.sort(key=lambda b: (b.start, -b.end))
    cluster: list[Block] = []
    cluster_end = -1

    def finish() -> None:
        if not cluster:
            return
        lane_ends: list[int] = []
        for block in cluster:
            for num, lane_end in enumerate(lane_ends):
                if lane_end <= block.start:
                    block.lane = num
                    lane_ends[num] = block.end
                    break
            else:
                block.lane = len(lane_ends)
                lane_ends.append(block.end)
        for block in cluster:
            block.lanes = len(lane_ends)

    for block in blocks:
        if block.start >= cluster_end:
            finish()
            cluster = []
            cluster_end = block.end
        else:
            cluster_end = max(cluster_end, block.end)
        cluster.append(block)
    finish()


def layout_day(
    day: dt.date,
    events: Iterable[Any],
    start: int,
    end: int,
) -> DayLayout:
    """sort the events of `day` into the all-day list and positioned blocks

    :param events: events with `allday`, `start_local`, `end_local`
        and `summary` attributes (i.e. :class:`khal.khalendar.event.Event`)
    :param start: start of the visible range in minutes since midnight
    :param end: end of the visible range (exclusive) in minutes, 24 * 60 for midnight
    """
    layout = DayLayout(day=day)
    for event in events:
        if event.allday:
            layout.allday.append(event)
            continue
        ev_start = _minutes(day, event.start_local, end=False)
        ev_end = _minutes(day, event.end_local, end=True)
        ev_end = max(ev_end, ev_start + 1)  # zero length events still deserve a row
        if ev_end <= start:
            layout.before += 1
        elif ev_start >= end:
            layout.after += 1
        else:
            layout.blocks.append(Block(event, day, max(ev_start, start), min(ev_end, end)))
    assign_lanes(layout.blocks)
    return layout


def _char_width(char: str) -> int:
    return urwid.calc_width(char, 0, 1)


class _Canvas:
    """a minimal grid of (attribute, character) cells"""

    def __init__(self, width: int, height: int, attr: str) -> None:
        self.width = width
        self.height = height
        self.cells = [[(attr, " ") for _ in range(width)] for _ in range(height)]

    def put(self, row: int, col: int, text: str, attr: str, maxwidth: int | None = None) -> int:
        """write `text`, clipped to `maxwidth` columns, return the columns used"""
        if not 0 <= row < self.height:
            return 0
        limit = self.width - col if maxwidth is None else min(maxwidth, self.width - col)
        used = 0
        for char in text:
            width = _char_width(char)
            if width == 0:
                continue
            if used + width > limit:
                break
            cell = col + used
            if cell < 0:
                used += width
                continue
            self.cells[row][cell] = (attr, char)
            for extra in range(1, width):
                self.cells[row][cell + extra] = (attr, "")
            used += width
        return used

    def fill(self, row0: int, row1: int, col0: int, col1: int, attr: str) -> None:
        for row in range(max(row0, 0), min(row1, self.height)):
            for col in range(max(col0, 0), min(col1, self.width)):
                self.cells[row][col] = (attr, " ")

    def rows(self) -> list[list[tuple[str, str]]]:
        """collapse the cells into runs of the same attribute"""
        result = []
        for line in self.cells:
            segments: list[tuple[str, str]] = []
            for attr, char in line:
                if segments and segments[-1][0] == attr:
                    segments[-1] = (attr, segments[-1][1] + char)
                else:
                    segments.append((attr, char))
            result.append(segments)
        return result


def _text_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _wrap(text: str, width: int, rows: int) -> list[str]:
    """break `text` into at most `rows` lines of `width` columns, ellipsising the rest

    Lines are broken between words, words longer than `width` are split.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if _text_width(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for char in word:
            if _text_width(current + char) > width:
                lines.append(current)
                current = ""
            current += char
    lines.append(current)
    if len(lines) > rows:
        lines = lines[:rows]
        tail = lines[-1]
        while tail and _text_width(tail) > width - 1:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return lines


def _fitting(text: str, short: str, width: int) -> str:
    return text if _text_width(text) <= width else short


def auto_rows_per_hour(start: int, end: int, available: int) -> int:
    """the most rows per hour fitting into `available` rows

    Prefers a number that keeps the start, the end and every full hour on a row
    boundary (e.g. an even number for a grid starting at 09:30).
    """
    fitting = max(min(int(available // ((end - start) / 60)), MAX_ROWS_PER_HOUR), 1)
    for rows in range(fitting, 0, -1):
        if (start % 60) * rows % 60 == 0 and (end % 60) * rows % 60 == 0:
            return rows
    return fitting


def render_grid(
    layouts: Sequence[DayLayout],
    *,
    width: int,
    height: int,
    start: int,
    end: int,
    headers: Sequence[str],
    today: dt.date,
    focus_day: dt.date,
    now: dt.datetime | None = None,
    rows_per_hour: int = 0,
    calendar_attr: Callable[[Any], str] = lambda event: "gridblock " + event.calendar,
) -> list[list[tuple[str, str]]]:
    """paint `layouts` (one per day) onto a `width` x `height` grid

    :param start: start of the visible range in minutes since midnight
    :param end: end of the visible range (exclusive) in minutes
    :param rows_per_hour: terminal rows per hour, 0 to scale to the available height
    :param now: where to draw the current time marker (only shown if its date is shown)
    :returns: one list of ``(attribute, text)`` segments per terminal row
    """
    canvas = _Canvas(width, height, "grid")
    ndays = len(layouts)
    day_width = max((width - GUTTER - (ndays - 1)) // ndays, 1)
    day_cols = [GUTTER + num * (day_width + 1) for num in range(ndays)]

    # header, all-day and out-of-range indicator rows around the hour grid
    allday_rows = min(max((len(lay.allday) for lay in layouts), default=0), MAX_ALLDAY_ROWS)
    before_row = any(lay.before for lay in layouts)
    after_row = any(lay.after for lay in layouts)
    top = 1 + allday_rows + (1 if before_row else 0)
    bottom = 1 if after_row else 0

    for num, (lay, col) in enumerate(zip(layouts, day_cols)):
        if lay.day == focus_day:
            attr = "grid header focus"
        elif lay.day == today:
            attr = "grid header today"
        else:
            attr = "grid header"
        canvas.fill(0, 1, col, col + day_width, attr)
        text = headers[num]
        canvas.put(0, col + max((day_width - len(text)) // 2, 0), text, attr, day_width)
        for row, event in enumerate(lay.allday[:MAX_ALLDAY_ROWS]):
            canvas.put(1 + row, col, "▪ " + event.summary, "grid allday", day_width)
        if before_row and lay.before:
            label = _fitting(f"▲ {lay.before} earlier", f"▲ {lay.before}", day_width)
            canvas.put(top - 1, col, label, "grid more", day_width)
        if after_row and lay.after:
            label = _fitting(f"▼ {lay.after} later", f"▼ {lay.after}", day_width)
            canvas.put(height - 1, col, label, "grid more", day_width)
    if allday_rows:
        canvas.put(1, 0, "all", "grid hour", GUTTER - 1)

    hours = (end - start) / 60
    available = max(height - top - bottom, 1)
    if rows_per_hour <= 0:
        rows_per_hour = auto_rows_per_hour(start, end, available)
    grid_rows = min(math.ceil(hours * rows_per_hour), available)

    def row_of(minutes: int, *, up: bool = False) -> int:
        scaled = (minutes - start) * rows_per_hour / 60
        return top + (math.ceil(scaled) if up else int(scaled))

    # a line for every full hour, plus one at the start if that is not a full hour
    marks = sorted({start} | set(range(math.ceil(start / 60) * 60, end, 60)))
    for mark in marks:
        row = row_of(mark)
        if row >= top + grid_rows:
            break
        canvas.put(row, 0, f"{mark // 60:02d}:{mark % 60:02d}", "grid hour", GUTTER - 1)
        for col in day_cols:
            canvas.put(row, col, "─" * day_width, "grid line")

    for lay, col in zip(layouts, day_cols):
        for block in lay.blocks:
            row0 = row_of(block.start)
            row1 = min(max(row_of(block.end, up=True), row0 + 1), top + grid_rows)
            lane_width = day_width // block.lanes
            # leave a column between neighbouring lanes so same-colored events stay apart
            gap = 1 if block.lane < block.lanes - 1 and lane_width > 3 else 0
            left = col + block.lane * lane_width
            right = col + day_width if block.lane == block.lanes - 1 else left + lane_width
            inner = max(right - left - gap, 1)
            attr = calendar_attr(block.event)
            canvas.fill(row0, row1, left, left + inner, attr)
            label = block.event.summary
            if inner >= MIN_WIDTH_FOR_TIME:
                label = f"{block.start // 60:02d}:{block.start % 60:02d} {label}"
            for num, line in enumerate(_wrap(label, inner, row1 - row0)):
                canvas.put(row0 + num, left, line, attr, inner)

    if now is not None:
        for lay, col in zip(layouts, day_cols):
            if lay.day != now.date():
                continue
            minutes = now.hour * 60 + now.minute
            if not start <= minutes < end:
                continue
            row = row_of(minutes)
            if row >= top + grid_rows:
                continue
            canvas.put(row, 0, f"{now:%H:%M}", "grid now", GUTTER - 1)
            canvas.put(row, GUTTER - 1, "▶", "grid now")
            for cell in range(col, col + day_width):
                if canvas.cells[row][cell][0] in ("grid", "grid line"):
                    canvas.cells[row][cell] = ("grid now", "─")
    return canvas.rows()


def week_start(day: dt.date, firstweekday: int) -> dt.date:
    """the first day of the week `day` is in"""
    return day - dt.timedelta(days=(day.weekday() - firstweekday) % 7)


class GridView(urwid.Widget):
    """box widget showing one day or one week on an hourly axis

    Moving around: `left`/`right` move by a day, `up`/`down` by a week,
    `today` jumps to today, `new` creates an event on the selected day.
    """

    _sizing = frozenset([urwid.BOX])
    _selectable = True

    def __init__(
        self,
        collection,
        conf,
        mode: str = "day",
        date: dt.date | None = None,
        on_date_change: Callable[[dt.date], None] | None = None,
        on_new: Callable[[dt.date, dt.date | None], None] | None = None,
    ) -> None:
        super().__init__()
        self.collection = collection
        self._conf = conf
        self.mode = mode
        self.date = date or dt.date.today()
        self.on_date_change = on_date_change
        self.on_new = on_new
        self._layouts: dict[dt.date, DayLayout] = {}

    @property
    def days(self) -> list[dt.date]:
        if self.mode == "week":
            first = week_start(self.date, self._conf["locale"]["firstweekday"])
            return [first + dt.timedelta(days=num) for num in range(7)]
        return [self.date]

    def refresh(self) -> None:
        """forget everything loaded, e.g. after events changed"""
        self._layouts.clear()
        self._invalidate()

    def set_date(self, date: dt.date, notify: bool = True) -> None:
        if date == self.date:
            return
        self.date = date
        self._invalidate()
        if notify and self.on_date_change is not None:
            self.on_date_change(date)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._invalidate()

    def _layout(self, day: dt.date) -> DayLayout:
        if day not in self._layouts:
            view = self._conf["view"]
            events = sorted(self.collection.get_events_on(day))
            self._layouts[day] = layout_day(day, events, view["grid_start"], view["grid_end"])
        return self._layouts[day]

    def render(self, size: tuple[()] | tuple[int] | tuple[int, int], focus: bool = False):
        assert len(size) == 2, "GridView is a box widget"
        width, height = size
        view = self._conf["view"]
        days = self.days
        if self.mode == "week":
            headers = [day.strftime("%a %d") for day in days]
        else:
            headers = [day.strftime(self._conf["locale"]["longdateformat"]) for day in days]
        localize = self._conf["locale"]["local_timezone"]
        rows = render_grid(
            [self._layout(day) for day in days],
            width=width,
            height=height,
            start=view["grid_start"],
            end=view["grid_end"],
            headers=headers,
            today=dt.date.today(),
            focus_day=self.date,
            now=dt.datetime.now(localize).replace(tzinfo=None),
            rows_per_hour=view["grid_rows_per_hour"],
        )
        canvases = [
            (urwid.Text(segments or [("grid", "")], wrap="clip").render((width,)), None, False)
            for segments in rows
        ]
        return urwid.CanvasCombine(canvases)

    def keypress(self, size: tuple[()] | tuple[int] | tuple[int, int], key: str) -> str | None:
        binds = self._conf["keybindings"]
        step = None
        if key in binds["left"]:
            step = -1
        elif key in binds["right"]:
            step = 1
        elif key in binds["up"]:
            step = -7
        elif key in binds["down"]:
            step = 7
        if step is not None:
            self.set_date(self.date + dt.timedelta(days=step))
            return None
        if key in binds["today"]:
            self.set_date(dt.date.today())
            return None
        if key in binds["new"] and self.on_new is not None:
            self.on_new(self.date, None)
            return None
        return key
