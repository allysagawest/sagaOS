from __future__ import annotations

import unittest

from cli.calendar.stack import (
    CalendarConnection,
    _build_vdirsyncer_google_oauth_error,
    _parse_discovered_remote_collections,
    _render_khal_config,
    _normalize_role,
    _render_vdirsyncer_config,
    discover_remote_collections,
)


class VdirsyncerConfigTests(unittest.TestCase):
    def test_secondary_connection_discovers_from_remote_when_unpinned(self) -> None:
        config = _render_vdirsyncer_config(
            [
                CalendarConnection(
                    kind="google",
                    name="Bryce",
                    slug="bryce",
                    path="/tmp/bryce",
                    role="secondary",
                    client_id="client-id",
                    client_secret="client-secret",
                    token_file="/tmp/token.json",
                )
            ]
        )

        self.assertIn('collections = ["from a"]', config)
        self.assertNotIn("read_only = true", config)
        self.assertNotIn('partial_sync = "ignore"', config)

    def test_selected_collections_render_exactly_in_vdirsyncer_config(self) -> None:
        config = _render_vdirsyncer_config(
            [
                CalendarConnection(
                    kind="google",
                    name="Personal",
                    slug="google_personal",
                    path="/tmp/google_personal",
                    role="secondary",
                    selected_collections=[
                        "alex.carson.440@gmail.com",
                        "family10799977565623715725@group.calendar.google.com",
                    ],
                    client_id="client-id",
                    client_secret="client-secret",
                    token_file="/tmp/token.json",
                )
            ]
        )

        self.assertIn(
            'collections = ["alex.carson.440@gmail.com", "family10799977565623715725@group.calendar.google.com"]',
            config,
        )

    def test_selected_collections_render_as_multiple_khal_calendars(self) -> None:
        config = _render_khal_config(
            [
                CalendarConnection(
                    kind="google",
                    name="Personal",
                    slug="google_personal",
                    path="/tmp/google_personal",
                    role="secondary",
                    selected_collections=[
                        "alex.carson.440@gmail.com",
                        "family10799977565623715725@group.calendar.google.com",
                    ],
                    client_id="client-id",
                    client_secret="client-secret",
                    token_file="/tmp/token.json",
                )
            ]
        )

        self.assertIn("[[google_personal]]", config)
        self.assertIn("path = /tmp/google_personal/alex.carson.440@gmail.com", config)
        self.assertIn("[[google_personal__2]]", config)
        self.assertIn(
            "path = /tmp/google_personal/family10799977565623715725@group.calendar.google.com",
            config,
        )

    def test_legacy_roles_normalize_to_supported_values(self) -> None:
        self.assertEqual(_normalize_role("source"), "secondary")
        self.assertEqual(_normalize_role("busy_target"), "secondary")
        self.assertEqual(_normalize_role("master"), "main")

    def test_google_oauth_error_includes_install_guidance(self) -> None:
        from unittest.mock import patch

        with patch("cli.calendar.stack._detect_linux_distro", return_value="fedora"):
            message = _build_vdirsyncer_google_oauth_error("critical: aiohttp-oauthlib not installed")

        self.assertIn("aiohttp OAuth dependency", message)
        self.assertIn("sudo dnf install python3-aiohttp-oauthlib", message)

    def test_parse_discovered_remote_collections_reads_remote_section(self) -> None:
        connection = CalendarConnection(
            kind="google",
            name="Bryce",
            slug="bryce",
            path="/tmp/bryce",
            role="secondary",
        )
        output = (
            "Discovering collections for pair bryce\n"
            "bryce_remote:\n"
            '  - "ally@fitbryceadams.com"\n'
            '  - "brian-archived@fitbryceadams.com" ("Brian@FitBryceAdams.com")\n'
            "bryce_local:\n"
        )

        collections = _parse_discovered_remote_collections(output, connection)

        self.assertEqual(
            collections,
            ["ally@fitbryceadams.com", "brian-archived@fitbryceadams.com"],
        )

    def test_discover_remote_collections_uses_vdirsyncer_list_mode(self) -> None:
        from unittest.mock import patch

        connection = CalendarConnection(
            kind="google",
            name="Bryce",
            slug="bryce",
            path="/tmp/bryce",
            role="secondary",
            client_id="client-id",
            client_secret="client-secret",
            token_file="/tmp/token.json",
        )

        with (
            patch("cli.calendar.stack.ensure_binary"),
            patch("cli.calendar.stack.ensure_vdirsyncer_ready"),
            patch("cli.calendar.stack.render_calendar_stack"),
            patch("cli.calendar.stack.load_connections", return_value=[connection]),
            patch("cli.calendar.stack.subprocess.run") as run_command,
        ):
            run_command.return_value.returncode = 0
            run_command.return_value.stdout = (
                "Discovering collections for pair bryce\n"
                "bryce_remote:\n"
                '  - "ally@fitbryceadams.com"\n'
            )
            run_command.return_value.stderr = ""
            collections = discover_remote_collections(connection)

        self.assertEqual(collections, ["ally@fitbryceadams.com"])
        run_command.assert_called_once()
        self.assertEqual(
            run_command.call_args.args[0],
            ["vdirsyncer", "discover", "--list", "bryce"],
        )


if __name__ == "__main__":
    unittest.main()
