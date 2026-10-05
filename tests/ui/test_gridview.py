import datetime as dt
from types import SimpleNamespace

import pytest
from configobj.validate import VdtValueError
from freezegun import freeze_time

from khal.settings.exceptions import InvalidSettingsError
from khal.settings.utils import config_checks, is_time_of_day
from khal.ui import _add_alt_shades, _shade_color
from khal.ui.gridview import (
    GridView,
    _bar_lines,
    _wrap,
    assign_lanes,
    auto_rows_per_hour,
    layout_day,
    render_grid,
    render_horizontal,
    shaded_attrs,
    week_start,
)
from tests.utils import LOCALE_BERLIN, cal1

DAY = dt.date(2017, 6, 7)  # a Wednesday


def event(start, end, summary="event", calendar="work", allday=False):
    """a stand-in for khal.khalendar.event.Event with only what the grid needs"""
    if not allday:
        start = dt.datetime.combine(DAY, dt.time(*start)) if isinstance(start, tuple) else start
        end = dt.datetime.combine(DAY, dt.time(*end)) if isinstance(end, tuple) else end
    return SimpleNamespace(
        start_local=start, end_local=end, summary=summary, calendar=calendar, allday=allday
    )


def text(rows):
    return ["".join(segment for _, segment in row) for row in rows]


def attr_at(rows, row, col):
    """the attribute of the character in `col` of `row`"""
    pos = 0
    for attr, segment in rows[row]:
        if pos + len(segment) > col:
            return attr
        pos += len(segment)
    raise IndexError(col)


def test_layout_day_clips_and_counts_hidden_events():
    layout = layout_day(
        DAY,
        [
            event((7, 0), (8, 0), "early"),
            event((8, 30), (10, 0), "standup"),
            event((16, 0), (18, 0), "late"),
            event((18, 0), (19, 0), "evening"),
            event(DAY, DAY, "holiday", allday=True),
        ],
        start=540,
        end=1020,
    )
    assert [(b.event.summary, b.start, b.end) for b in layout.blocks] == [
        ("standup", 9 * 60, 10 * 60),
        ("late", 16 * 60, 17 * 60),
    ]
    assert layout.before == 1
    assert layout.after == 1
    assert [e.summary for e in layout.allday] == ["holiday"]


def test_layout_day_multi_day_event():
    yesterday = dt.datetime.combine(DAY - dt.timedelta(days=1), dt.time(22))
    tomorrow = dt.datetime.combine(DAY + dt.timedelta(days=1), dt.time(2))
    layout = layout_day(DAY, [event(yesterday, tomorrow)], start=0, end=1440)
    assert [(b.start, b.end) for b in layout.blocks] == [(0, 24 * 60)]


def test_layout_day_event_ending_at_midnight():
    midnight = dt.datetime.combine(DAY + dt.timedelta(days=1), dt.time(0))
    layout = layout_day(DAY, [event((23, 0), midnight)], start=0, end=1440)
    assert [(b.start, b.end) for b in layout.blocks] == [(23 * 60, 24 * 60)]


def test_assign_lanes():
    layout = layout_day(
        DAY,
        [
            event((9, 0), (11, 0), "a"),
            event((9, 30), (10, 0), "b"),
            event((10, 0), (10, 30), "c"),  # fits into b's lane after b ended
            event((12, 0), (13, 0), "alone"),
        ],
        start=540,
        end=1020,
    )
    lanes = {b.event.summary: (b.lane, b.lanes) for b in layout.blocks}
    assert lanes == {"a": (0, 2), "b": (1, 2), "c": (1, 2), "alone": (0, 1)}


def test_assign_lanes_three_way_overlap():
    blocks = layout_day(
        DAY,
        [event((9, 0), (10, 0), str(num)) for num in range(3)],
        start=540,
        end=1020,
    ).blocks
    assign_lanes(blocks)
    assert sorted(b.lane for b in blocks) == [0, 1, 2]
    assert {b.lanes for b in blocks} == {3}


def test_week_start():
    assert week_start(DAY, 0) == dt.date(2017, 6, 5)
    assert week_start(DAY, 6) == dt.date(2017, 6, 4)
    assert week_start(dt.date(2017, 6, 5), 0) == dt.date(2017, 6, 5)


def _render_day(events, **kwargs):
    layout = layout_day(DAY, events, start=540, end=720)
    options = {
        "width": 30,
        "height": 7,
        "start": 9 * 60,
        "end": 12 * 60,
        "headers": ["Wednesday"],
        "today": DAY,
        "focus_day": DAY,
        "rows_per_hour": 2,
    }
    options.update(kwargs)
    return render_grid([layout], **options)


def test_render_day():
    rows = _render_day([event((9, 30), (10, 30), "ENG-41 Fix login", calendar="jira")])
    assert text(rows) == [
        "             Wednesday        ",
        "09:00 ────────────────────────",
        "      09:30 ENG-41 Fix login  ",
        "10:00                         ",
        "                              ",
        "11:00 ────────────────────────",
        "                              ",
    ]
    assert attr_at(rows, 2, 6) == "gridblock jira"
    assert attr_at(rows, 3, 6) == "gridblock jira"
    assert attr_at(rows, 1, 6) == "grid line"
    assert attr_at(rows, 0, 10) == "grid header focus"


def test_render_overlapping_events_side_by_side():
    rows = _render_day(
        [
            event((9, 0), (10, 0), "standup", calendar="meetings"),
            event((9, 0), (10, 0), "ENG-41", calendar="jira"),
        ]
    )
    # 24 columns for the day, split in two lanes of 12 with a gap after the first
    # lanes this narrow only show the title
    first, second = text(rows)[1][6:17], text(rows)[1][18:]
    assert {first.strip(), second.strip()} == {"standup", "ENG-41"}
    attrs = {attr_at(rows, 1, 6), attr_at(rows, 1, 18)}
    assert attrs == {"gridblock meetings", "gridblock jira"}
    assert attr_at(rows, 1, 17) == "grid line"  # the gap between the lanes shows the grid


def test_render_now_marker():
    rows = _render_day([], now=dt.datetime.combine(DAY, dt.time(10, 45)))
    assert text(rows)[4].startswith("10:45▶" + "─" * 24)
    assert attr_at(rows, 4, 0) == "grid now"
    assert attr_at(rows, 4, 10) == "grid now"


def test_render_now_marker_other_day():
    rows = _render_day([], now=dt.datetime(2017, 6, 8, 10, 45))
    assert "▶" not in "".join(text(rows))


def test_render_hidden_and_allday_rows():
    rows = _render_day(
        [
            event(DAY, DAY, "holiday", allday=True),
            event((7, 0), (8, 0), "gym"),
            event((13, 0), (14, 0), "lunch"),
        ],
        height=10,
    )
    lines = text(rows)
    assert lines[1].startswith("all   ▪ holiday")
    assert lines[2].startswith("      ▲ 1 earlier")
    assert lines[3].startswith("09:00 ─")
    assert lines[-1].startswith("      ▼ 1 later")


def test_render_hidden_rows_narrow_columns():
    rows = _render_day([event((7, 0), (8, 0), "gym")], width=12)
    assert text(rows)[1] == "      ▲ 1   "


def test_render_long_title_wraps_and_ellipsises():
    rows = _render_day(
        [event((9, 0), (10, 0), "a very long title that does not fit")],
        width=16,
    )
    assert text(rows)[1:3] == ["09:00 a very    ", "      long titl…"]


def test_wrap():
    assert _wrap("ENG-41 sample Jira task", 10, 3) == ["ENG-41", "sample", "Jira task"]
    assert _wrap("ENG-41 sample Jira task", 10, 2) == ["ENG-41", "sample…"]
    assert _wrap("supercalifragilistic", 8, 3) == ["supercal", "ifragili", "stic"]
    assert _wrap("", 8, 3) == [""]


def test_render_wide_characters():
    rows = _render_day([event((9, 0), (10, 0), "会議会議会議会議会議")], width=16)
    for line in text(rows):
        # every row must still be exactly as wide as the widget
        assert sum(2 if "一" <= c <= "鿿" else 1 for c in line) == 16


def test_render_week():
    days = [week_start(DAY, 0) + dt.timedelta(days=num) for num in range(7)]
    layouts = [
        layout_day(day, [event((9, 0), (10, 0), "x")] if day == DAY else [], 9 * 60, 12 * 60)
        for day in days
    ]
    rows = render_grid(
        layouts,
        width=6 + 7 * 4 + 6,
        height=7,
        start=540,
        end=720,
        headers=[day.strftime("%a") for day in days],
        today=DAY,
        focus_day=dt.date(2017, 6, 5),
        rows_per_hour=2,
    )
    assert text(rows)[0] == "      Mon  Tue  Wed  Thu  Fri  Sat  Sun "
    assert attr_at(rows, 0, 6) == "grid header focus"
    assert attr_at(rows, 0, 16) == "grid header today"
    # Wednesday is the third column, starting at 6 + 2 * 5
    assert attr_at(rows, 1, 16) == "gridblock work"
    assert attr_at(rows, 1, 6) == "grid line"


CONF = {
    "locale": LOCALE_BERLIN,
    "keybindings": {
        "left": ["h"],
        "right": ["l"],
        "up": ["k"],
        "down": ["j"],
        "today": ["t"],
        "new": ["n"],
    },
    "view": {"grid_start": 9 * 60, "grid_end": 12 * 60, "grid_rows_per_hour": 2},
}

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ENG-41
SUMMARY:Fix login
DTSTART;TZID=Europe/Berlin:20170607T093000
DTEND;TZID=Europe/Berlin:20170607T103000
END:VEVENT
END:VCALENDAR
"""


@freeze_time("2017-6-7 08:00")
def test_gridview_with_collection(coll_vdirs):
    collection, _ = coll_vdirs
    collection.insert(collection.create_event_from_ics(ICS, cal1), cal1)
    moved = []
    grid = GridView(collection, CONF, mode="day", on_date_change=moved.append)
    canvas = grid.render((30, 7))
    lines = [line.decode() for line in canvas.text]
    assert lines[2].startswith("      09:30 Fix login")

    assert grid.keypress((30, 7), "l") is None
    assert grid.date == dt.date(2017, 6, 8)
    assert grid.keypress((30, 7), "j") is None
    assert grid.date == dt.date(2017, 6, 15)
    assert grid.keypress((30, 7), "t") is None
    assert moved == [dt.date(2017, 6, 8), dt.date(2017, 6, 15), dt.date(2017, 6, 7)]
    assert grid.keypress((30, 7), "x") == "x"

    grid.set_mode("week")
    assert grid.days[0] == dt.date(2017, 6, 5)
    assert len(grid.days) == 7
    grid.render((76, 7))


@freeze_time("2017-6-7 08:00")
def test_gridview_refresh_picks_up_new_events(coll_vdirs):
    collection, _ = coll_vdirs
    grid = GridView(collection, CONF, mode="day")
    assert "Fix login" not in "".join(line.decode() for line in grid.render((30, 7)).text)
    collection.insert(collection.create_event_from_ics(ICS, cal1), cal1)
    grid.refresh()
    assert "Fix login" in "".join(line.decode() for line in grid.render((30, 7)).text)


def test_gridview_new_event():
    created = []
    grid = GridView(None, CONF, date=DAY, on_new=lambda *args: created.append(args))
    assert grid.keypress((30, 7), "n") is None
    assert created == [(DAY, None)]


@pytest.mark.parametrize(("start", "end"), [(17 * 60, 9 * 60), (9 * 60, 9 * 60)])
def test_config_checks_grid_hours(start, end):
    config = {
        "calendars": {},
        "sqlite": {"path": "/tmp"},
        "locale": {"default_timezone": "Europe/Berlin", "local_timezone": "Europe/Berlin"},
        "default": {"default_calendar": None},
        "view": {"grid_start": start, "grid_end": end},
    }
    with pytest.raises(InvalidSettingsError):
        config_checks(config)


def test_render_day_starting_on_the_half_hour():
    layout = layout_day(DAY, [event((9, 30), (10, 30), "standup")], 9 * 60 + 30, 11 * 60 + 30)
    rows = render_grid(
        [layout],
        width=30,
        height=5,
        start=9 * 60 + 30,
        end=11 * 60 + 30,
        headers=["Wednesday"],
        today=DAY,
        focus_day=DAY,
        rows_per_hour=2,
    )
    assert text(rows)[1:] == [
        "09:30 09:30 standup           ",
        "10:00                         ",
        "                              ",
        "11:00 ────────────────────────",
    ]
    assert attr_at(rows, 2, 6) == "gridblock work"


@pytest.mark.parametrize(
    ("string", "minutes"),
    [("09:30", 570), ("9:30", 570), ("00:00", 0), ("17:30", 1050), ("24:00", 1440)],
)
def test_is_time_of_day(string, minutes):
    assert is_time_of_day(string) == minutes


@pytest.mark.parametrize("string", ["9", "09:60", "25:00", "24:30", "noon", "-1:00"])
def test_is_time_of_day_invalid(string):
    with pytest.raises(VdtValueError):
        is_time_of_day(string)


@pytest.mark.parametrize(
    ("start", "end", "available", "rows"),
    [
        (9 * 60, 17 * 60, 25, 3),  # full hours, anything goes
        (9 * 60 + 30, 17 * 60 + 30, 25, 2),  # 3 rows of 20 minutes would split 09:30
        (9 * 60 + 30, 17 * 60 + 30, 40, 4),
        (9 * 60, 17 * 60, 200, 8),  # capped
        (9 * 60, 17 * 60, 4, 1),  # does not fit, one row per hour and clip
        (9 * 60 + 20, 17 * 60, 25, 3),  # 20 minute rows fit 09:20
    ],
)
def test_auto_rows_per_hour(start, end, available, rows):
    assert auto_rows_per_hour(start, end, available) == rows


def test_shaded_attrs_alternate_per_calendar():
    blocks = layout_day(
        DAY,
        [
            event((9, 0), (10, 0), "a", calendar="jira"),
            event((9, 30), (10, 30), "standup", calendar="meetings"),
            event((10, 0), (11, 0), "b", calendar="jira"),
            event((11, 0), (12, 0), "c", calendar="jira"),
        ],
        9 * 60,
        17 * 60,
    ).blocks
    assert shaded_attrs(blocks, lambda event: "gridblock " + event.calendar) == [
        "gridblock jira",
        "gridblock meetings",
        "gridblock jira alt",
        "gridblock jira",
    ]


def test_render_vertical_gap_between_back_to_back_events():
    rows = _render_day(
        [
            event((9, 0), (10, 0), "first", calendar="jira"),
            event((10, 0), (11, 0), "second", calendar="jira"),
        ]
    )
    # 2 rows per hour: the first event gives up its last row to keep a gap
    assert [attr_at(rows, row, 6) for row in range(1, 5)] == [
        "gridblock jira",
        "grid",
        "gridblock jira alt",
        "gridblock jira alt",
    ]


def _render_horizontal(events, **kwargs):
    options = {
        "width": 50,
        "height": 6,
        "start": 9 * 60 + 30,
        "end": 11 * 60 + 30,
        "headers": ["Wed 07"],
        "title": "Jun 2017",
        "today": DAY,
        "focus_day": DAY,
    }
    options.update(kwargs)
    layout = layout_day(DAY, events, options["start"], options["end"])
    return render_horizontal([layout], **options)


def test_render_horizontal_day():
    rows = _render_horizontal(
        [
            event((9, 30), (10, 30), "ENG-38 incident", calendar="jira"),
            event((10, 30), (11, 0), "ENG-39", calendar="jira"),
            event((10, 0), (11, 0), "standup", calendar="meetings"),
            event(DAY, DAY, "holiday", allday=True),
            event((7, 0), (8, 0), "gym"),
        ],
        now=dt.datetime.combine(DAY, dt.time(11, 15)),
    )
    assert text(rows) == [
        # the ▼ marker would cut into the 11:00 label, so the header skips it
        "Jun 2017   09:30    10:00             11:00       ",
        "Wed 07  ◀1 09:30–10:30       10:30–1… │   │       ",
        "▪ holid    ENG-38 incident   ENG-39   │   │       ",
        "           ┊        10:00–11:00       │   │       ",
        "           ┊        standup           │   │       ",
        "                                                  ",
    ]
    # back to back events of one calendar alternate shades and keep a gap column
    assert attr_at(rows, 1, 11) == "gridblock jira"
    assert attr_at(rows, 1, 28) == "grid"
    assert attr_at(rows, 1, 29) == "gridblock jira alt"
    # overlapping events stack in lanes
    assert attr_at(rows, 3, 20) == "gridblock meetings"
    assert attr_at(rows, 1, 0) == "grid header focus"
    assert attr_at(rows, 1, 42) == "grid now"  # 11:15 on a 36 column axis


def test_render_horizontal_now_marker_in_header():
    rows = _render_horizontal([], now=dt.datetime.combine(DAY, dt.time(10, 30)))
    col = text(rows)[0].index("▼")
    assert attr_at(rows, 0, col) == "grid now"
    assert text(rows)[1][col] == "│"


def test_render_horizontal_after_counter_and_hour_labels():
    rows = _render_horizontal([event((12, 0), (13, 0), "late")], width=60)
    assert text(rows)[1].endswith("1▶")
    assert "09:30" in text(rows)[0]
    assert "10:00" in text(rows)[0]


def test_render_horizontal_week_bands():
    days = [week_start(DAY, 0) + dt.timedelta(days=num) for num in range(5)]
    layouts = [
        layout_day(day, [event((10, 0), (11, 0), "x")] if day == DAY else [], 570, 690)
        for day in days
    ]
    rows = render_horizontal(
        layouts,
        width=60,
        height=12,
        start=570,
        end=690,
        headers=[day.strftime("%a") for day in days],
        title="Week 23",
        today=DAY,
        focus_day=days[0],
    )
    lines = text(rows)
    labels = [line[:3] for line in lines if line[:3] in ("Mon", "Tue", "Wed", "Thu", "Fri")]
    assert labels == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    wednesday = next(num for num, line in enumerate(lines) if line.startswith("Wed"))
    assert attr_at(rows, wednesday, 0) == "grid header today"
    assert "gridblock work" in {attr for attr, _ in rows[wednesday]}
    # 5 bands of 1 lane and 4 separators fit 11 rows, so separators are drawn
    assert lines[2].startswith("─")


def test_render_horizontal_drops_separators_when_short():
    days = [week_start(DAY, 0) + dt.timedelta(days=num) for num in range(7)]
    layouts = [layout_day(day, [], 570, 690) for day in days]
    rows = render_horizontal(
        layouts,
        width=60,
        height=8,
        start=570,
        end=690,
        headers=[day.strftime("%a") for day in days],
        title="Week 23",
        today=DAY,
        focus_day=DAY,
    )
    assert [line[:3] for line in text(rows)[1:]] == [
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
        "Sun",
    ]


def test_bar_lines():
    block = layout_day(DAY, [event((9, 30), (10, 15), "ENG-38 Incident rings fade")], 0, 1440)
    block = block.blocks[0]
    assert _bar_lines(block, 30, 1) == ["09:30 ENG-38 Incident rings…"]
    assert _bar_lines(block, 10, 1) == ["ENG-38…"]
    assert _bar_lines(block, 12, 3) == ["09:30–10:15", "ENG-38", "Incident…"]


@freeze_time("2017-6-7 08:00")
def test_gridview_week_days_and_orientation(coll_vdirs):
    collection, _ = coll_vdirs
    collection.insert(collection.create_event_from_ics(ICS, cal1), cal1)
    conf = {**CONF, "view": {**CONF["view"], "grid_week_days": 5, "grid_orientation": "horizontal"}}
    grid = GridView(collection, conf, mode="week")
    assert grid.days == [dt.date(2017, 6, 5) + dt.timedelta(days=num) for num in range(5)]
    lines = [line.decode() for line in grid.render((80, 12)).text]
    assert lines[0].startswith("Week 23")
    assert any(line.startswith("Wed 07") for line in lines)
    assert "Fix login" in "".join(lines)


def test_shade_color():
    assert _shade_color("#000000") == "#333333"
    assert _shade_color("#ffffff") == "#d1d1d1"
    assert _shade_color("light magenta") == "light magenta"


def test_add_alt_shades():
    palette = [
        ("gridblock jira", "", "", "", "#eeeeee", "#000000"),
        ("gridblock meetings", "", "", "", "#eeeeee", "#202020"),
        ("gridblock meetings alt", "", "", "", "#eeeeee", "#ff0000"),
        ("grid", "", ""),
    ]
    names = {entry[0]: entry for entry in _add_alt_shades(palette, "gridblock ")}
    assert names["gridblock jira alt"] == ("gridblock jira alt", "", "", "", "#eeeeee", "#333333")
    # explicitly configured shades are kept
    assert names["gridblock meetings alt"][5] == "#ff0000"
    assert "grid alt" not in names
