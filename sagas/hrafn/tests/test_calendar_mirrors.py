from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from cli.calendar.stack import CalendarConnection, _render_mirror_ics, cleanup_calendar_mirrors, reconcile_calendar_mirrors


DETAIL_SOURCE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:source-uid
DTSTART:20260314T130000Z
DTEND:20260314T140000Z
SUMMARY:Pipeline Review
DESCRIPTION:Discuss revenue and clients
LOCATION:HQ
ATTENDEE:mailto:test@example.com
ORGANIZER:mailto:owner@example.com
END:VEVENT
END:VCALENDAR
"""

BUSY_SOURCE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:main-uid
DTSTART:20260314T150000Z
DTEND:20260314T160000Z
SUMMARY:Private Planning
DESCRIPTION:Sensitive notes
LOCATION:Office
END:VEVENT
END:VCALENDAR
"""


class CalendarMirrorTests(unittest.TestCase):
    def test_secondary_detail_mirror_preserves_details_and_labels_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "source.ics"
            source_file.write_text(DETAIL_SOURCE, encoding="utf-8")

            rendered = _render_mirror_ics(
                source_file=source_file,
                target_kind="detail",
                source_connection=CalendarConnection(
                    kind="google",
                    name="Bryce",
                    slug="bryce",
                    path=tmp,
                    role="secondary",
                ),
            )

        self.assertIn("SUMMARY:[Bryce] Pipeline Review", rendered)
        self.assertIn("DESCRIPTION:Discuss revenue and clients", rendered)
        self.assertIn("LOCATION:HQ", rendered)
        self.assertNotIn("ATTENDEE:", rendered)
        self.assertNotIn("ORGANIZER:", rendered)
        self.assertIn("X-HRAFN-MIRROR-KIND:detail", rendered)

    def test_main_busy_mirror_strips_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_file = Path(tmp) / "main.ics"
            source_file.write_text(BUSY_SOURCE, encoding="utf-8")

            rendered = _render_mirror_ics(
                source_file=source_file,
                target_kind="busy",
                source_connection=CalendarConnection(
                    kind="google",
                    name="Main",
                    slug="main",
                    path=tmp,
                    role="main",
                ),
            )

        self.assertIn("SUMMARY:Busy", rendered)
        self.assertIn("CLASS:PRIVATE", rendered)
        self.assertIn("TRANSP:OPAQUE", rendered)
        self.assertNotIn("DESCRIPTION:S", rendered)
        self.assertNotIn("LOCATION:Office", rendered)
        self.assertIn("X-HRAFN-MIRROR-KIND:busy", rendered)

    def test_source_role_mirrors_detail_without_creating_busy_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_dir = root / "main" / "primary"
            source_dir = root / "source" / "alex@example.com"
            main_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            (main_dir / "main.ics").write_text(BUSY_SOURCE, encoding="utf-8")
            (source_dir / "source.ics").write_text(DETAIL_SOURCE, encoding="utf-8")

            main = CalendarConnection(
                kind="google",
                name="Main",
                slug="main",
                path=str(root / "main"),
                role="main",
                selected_collections=["primary"],
            )
            source = CalendarConnection(
                kind="google",
                name="Source",
                slug="source",
                path=str(root / "source"),
                role="source",
                selected_collections=["alex@example.com"],
            )

            with patch("cli.calendar.stack.load_connections", return_value=[main, source]):
                reconcile_calendar_mirrors()

            self.assertTrue(any(path.name.startswith("hrafn-detail-") for path in main_dir.glob("*.ics")))
            self.assertFalse(any(path.name.startswith("hrafn-busy-") for path in source_dir.glob("*.ics")))

    def test_cleanup_removes_all_hrafn_managed_files_even_without_hrafn_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collection_dir = root / "main" / "primary"
            collection_dir.mkdir(parents=True)
            managed = collection_dir / "random-export-name.ics"
            native = collection_dir / "native-event.ics"
            managed.write_text(
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nX-HRAFN-MANAGED-MIRROR:TRUE\nEND:VEVENT\nEND:VCALENDAR\n",
                encoding="utf-8",
            )
            native.write_text(BUSY_SOURCE, encoding="utf-8")

            main = CalendarConnection(
                kind="google",
                name="Main",
                slug="main",
                path=str(root / "main"),
                role="main",
                selected_collections=["primary"],
            )

            with (
                patch("cli.calendar.stack.load_connections", return_value=[main]),
                patch("cli.calendar.stack._run_command") as run_command,
            ):
                run_command.return_value.stdout = "ok"
                run_command.return_value.stderr = ""
                message = cleanup_calendar_mirrors()

            self.assertIn("Removed 1", message)
            self.assertFalse(managed.exists())
            self.assertTrue(native.exists())


if __name__ == "__main__":
    unittest.main()
