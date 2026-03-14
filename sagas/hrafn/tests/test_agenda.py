from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from cli.calendar.stack import read_agenda
from cli.models import EventRecord
from cli.runtime import _compute_next_meeting, upcoming_events


class ReadAgendaTests(unittest.TestCase):
    @patch("cli.calendar.stack.get_main_calendar_names")
    @patch("cli.calendar.stack.ensure_binary")
    @patch("cli.calendar.stack.ensure_khal_ready")
    @patch("cli.calendar.stack._run_command")
    def test_read_agenda_uses_repeated_json_fields_and_skips_empty_rows(
        self,
        run_command,
        _ensure_khal_ready,
        _ensure_binary,
        get_main_calendar_names,
    ) -> None:
        get_main_calendar_names.return_value = ["main"]
        run_command.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='[{"title":"Dinner","start-long-full":"2026-03-14 18:30","end-long-full":"2026-03-14 21:00","calendar":"work","location":"LBM"}]\n[{}]\n',
            stderr="",
        )

        events = read_agenda(days=30)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Dinner")
        self.assertEqual(
            run_command.call_args.args[0],
            [
                "khal",
                "list",
                "today",
                "30d",
                "-a",
                "main",
                "--json",
                "title",
                "--json",
                "start-long-full",
                "--json",
                "end-long-full",
                "--json",
                "calendar",
                "--json",
                "location",
            ],
        )

    @patch("cli.calendar.stack.get_main_calendar_names")
    @patch("cli.calendar.stack.ensure_binary")
    @patch("cli.calendar.stack.ensure_khal_ready")
    @patch("cli.calendar.stack._run_command")
    def test_read_agenda_reads_all_calendars_when_no_main_is_configured(
        self,
        run_command,
        _ensure_khal_ready,
        _ensure_binary,
        get_main_calendar_names,
    ) -> None:
        get_main_calendar_names.return_value = []
        run_command.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[]\n",
            stderr="",
        )

        read_agenda(days=7)

        self.assertEqual(
            run_command.call_args.args[0],
            [
                "khal",
                "list",
                "today",
                "7d",
                "--json",
                "title",
                "--json",
                "start-long-full",
                "--json",
                "end-long-full",
                "--json",
                "calendar",
                "--json",
                "location",
            ],
        )


class UpcomingEventTests(unittest.TestCase):
    def test_upcoming_events_returns_next_three_by_end_time(self) -> None:
        events = [
            EventRecord("Past", "2026-03-13T10:00", "2026-03-13T11:00", "work", None),
            EventRecord("Tomorrow Dinner", "2026-03-14T18:30", "2026-03-14T21:00", "work", None),
            EventRecord("Sunday Meal Prep", "2026-03-15T16:00", "2026-03-15T17:30", "work", None),
            EventRecord("Sunday Cooking", "2026-03-15T17:30", "2026-03-15T21:00", "work", None),
            EventRecord("Monday Planning", "2026-03-16T10:00", "2026-03-16T10:30", "work", None),
        ]

        with patch("cli.runtime.now_local") as now_local:
            now_local.return_value = __import__("datetime").datetime.fromisoformat("2026-03-13T21:00:00-04:00")
            items = upcoming_events(events, limit=3)

        self.assertEqual([item.title for item in items], ["Tomorrow Dinner", "Sunday Meal Prep", "Sunday Cooking"])

    def test_compute_next_meeting_uses_next_future_event(self) -> None:
        events = [
            EventRecord("Tomorrow Dinner", "2026-03-14T18:30", "2026-03-14T21:00", "work", "LBM"),
            EventRecord("Sunday Meal Prep", "2026-03-15T16:00", "2026-03-15T17:30", "work", None),
        ]

        with patch("cli.runtime.now_local") as now_local:
            now_local.return_value = __import__("datetime").datetime.fromisoformat("2026-03-13T21:00:00-04:00")
            meeting = _compute_next_meeting(events)

        self.assertIsNotNone(meeting)
        assert meeting is not None
        self.assertEqual(meeting.title, "Tomorrow Dinner")
        self.assertEqual(meeting.location, "LBM")


if __name__ == "__main__":
    unittest.main()
