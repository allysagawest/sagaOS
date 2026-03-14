from __future__ import annotations

import json
import sys
import webbrowser
from typing import Annotated

import typer

from cli.calendar.stack import (
    CalendarStackError,
    CalendarConnection,
    GOOGLE_AUDIENCE_URL,
    GOOGLE_BRANDING_URL,
    GOOGLE_CLOUD_CONSOLE_URL,
    GOOGLE_CREDENTIALS_URL,
    GOOGLE_DATA_ACCESS_URL,
    GOOGLE_SYNCSELECT_URL,
    cleanup_calendar_mirrors,
    create_event,
    discover_remote_collections,
    get_main_calendar_name,
    has_khal_config,
    has_vdirsyncer_config,
    list_calendars,
    read_agenda,
    run_vdirsyncer_sync,
    setup_caldav_calendar,
    setup_google_calendar,
    setup_local_calendar,
    sync_connection,
    update_connection_selection,
)
from cli.config import load_theme
from cli.service import (
    HrafnServiceError,
    configure_sync_service,
    service_status,
)
from cli.signals import compute_signals
from cli.runtime import upcoming_events
from cli.utils.time import format_agenda_time

app = typer.Typer(
    help=(
        "Hrafn operational awareness CLI.\n\n"
        "Hrafn is a thin wrapper around vdirsyncer and khal for calendar sync, "
        "agenda inspection, event creation, and signal computation."
    )
)
THEME = load_theme()


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _colorize(text: str, color_key: str) -> str:
    if not _supports_color():
        return text

    color = THEME.colors.get(color_key, "")
    if not color.startswith("#") or len(color) != 7:
        return text

    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return f"\033[38;2;{red};{green};{blue}m{text}\033[0m"


def _signal_color(name: str) -> str:
    return {
        "meeting_starting_soon": "meeting_starting_soon",
        "meeting_live": "meeting_live",
        "focus_window_available": "focus_window_available",
    }.get(name, "agenda_title")


def _print_calendar_error(exc: CalendarStackError) -> None:
    typer.echo(_colorize(str(exc), "error"), err=True)
    if not has_khal_config():
        typer.echo(
            _colorize(
                "No khal calendar config was found. Run 'hrafn connect' to set up a calendar inside Hrafn.",
                "warning",
            ),
            err=True,
        )
    elif not has_vdirsyncer_config():
        typer.echo(
            _colorize(
                "No vdirsyncer config was found. Run 'hrafn connect --provider caldav' to add a sync-backed calendar.",
                "warning",
            ),
            err=True,
        )


def _print_service_error(exc: HrafnServiceError) -> None:
    typer.echo(_colorize(str(exc), "error"), err=True)


def _provider_prompt() -> str:
    typer.echo("Choose a calendar source:", err=True)
    typer.echo("1. Local calendar only", err=True)
    typer.echo("2. CalDAV account", err=True)
    typer.echo("3. Google Calendar", err=True)
    choice = typer.prompt("Select 1, 2, or 3", default="1", show_default=True).strip()
    if choice == "1":
        return "local"
    if choice == "2":
        return "caldav"
    if choice == "3":
        return "google"
    raise CalendarStackError("Invalid choice. Select 1 for local, 2 for CalDAV, or 3 for Google.")


def _connect_local_calendar() -> CalendarConnection:
    name = typer.prompt("Calendar name", default="Personal", show_default=True).strip()
    connection = setup_local_calendar(name)
    typer.echo(
        _colorize(
            f"Local calendar '{connection.name}' is ready at {connection.path}",
            "sync_status",
        )
    )
    return connection


def _connect_caldav_calendar() -> CalendarConnection:
    typer.echo("Enter the CalDAV account details Hrafn should store in the standard vdirsyncer config.", err=True)
    name = typer.prompt("Account label", default="work", show_default=True).strip()
    url = typer.prompt("CalDAV URL").strip()
    username = typer.prompt("Username").strip()
    password = typer.prompt("Password or app password", hide_input=True).strip()
    connection = setup_caldav_calendar(name=name, url=url, username=username, password=password)
    typer.echo(_colorize("Wrote khal and vdirsyncer config. Discovering remote calendars now.", "sync_status"))
    collections = discover_remote_collections(connection)
    updated = _select_account_calendar(connection, collections)
    typer.echo(_colorize("Syncing the selected calendar now.", "sync_status"))
    typer.echo(_colorize(sync_connection(updated), "sync_status"))
    return updated


def _open_url(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - browser behavior is platform dependent
        typer.echo(_colorize(f"Failed to open browser automatically: {exc}", "warning"), err=True)
        typer.echo(url, err=True)
        return

    if not opened:
        typer.echo(_colorize("Browser launch was not confirmed by the system. Open this URL manually:", "warning"), err=True)
        typer.echo(url, err=True)


def _maybe_open_google_docs() -> None:
    if not typer.confirm("Open the Google setup pages in your browser now?", default=True):
        return

    for url in [
        GOOGLE_CLOUD_CONSOLE_URL,
        GOOGLE_BRANDING_URL,
        GOOGLE_AUDIENCE_URL,
        GOOGLE_DATA_ACCESS_URL,
        GOOGLE_CREDENTIALS_URL,
        GOOGLE_SYNCSELECT_URL,
    ]:
        _open_url(url)


def _print_google_setup_steps() -> None:
    typer.echo("Google setup for an OSS-friendly personal account flow:", err=True)
    typer.echo("1. Create or pick a Google Cloud project.", err=True)
    typer.echo("2. Configure the OAuth consent screen branding.", err=True)
    typer.echo("3. Set audience to External and add yourself as a test user if needed.", err=True)
    typer.echo("4. In Data Access, add the Google Calendar scope.", err=True)
    typer.echo("5. Create OAuth credentials for a Desktop app.", err=True)
    typer.echo("6. Copy the Desktop app client ID and client secret back into Hrafn.", err=True)
    typer.echo("7. In Google Calendar, visit Sync Select so the calendars you want are exposed to sync clients.", err=True)
    typer.echo("", err=True)
    typer.echo(f"Cloud Console: {GOOGLE_CLOUD_CONSOLE_URL}", err=True)
    typer.echo(f"Branding: {GOOGLE_BRANDING_URL}", err=True)
    typer.echo(f"Audience: {GOOGLE_AUDIENCE_URL}", err=True)
    typer.echo(f"Data Access: {GOOGLE_DATA_ACCESS_URL}", err=True)
    typer.echo(f"Credentials: {GOOGLE_CREDENTIALS_URL}", err=True)
    typer.echo(f"Sync Select: {GOOGLE_SYNCSELECT_URL}", err=True)


def _connect_google_calendar() -> CalendarConnection:
    _print_google_setup_steps()
    _maybe_open_google_docs()
    typer.echo("", err=True)
    typer.echo("Enter the Desktop OAuth client values from Google Cloud Console.", err=True)
    typer.echo("Hrafn will keep them in its managed vdirsyncer config and let vdirsyncer handle the OAuth token flow.", err=True)
    name = typer.prompt("Google account label", default="google-personal", show_default=True).strip()
    client_id = typer.prompt("Google client ID").strip()
    client_secret = typer.prompt("Google client secret", hide_input=True).strip()
    connection = setup_google_calendar(
        name=name,
        client_id=client_id,
        client_secret=client_secret,
    )
    typer.echo(_colorize("Wrote khal and vdirsyncer config. Starting Google authorization and listing calendars now.", "sync_status"))
    collections = discover_remote_collections(connection)
    updated = _select_account_calendar(connection, collections)
    typer.echo(_colorize("Syncing the selected calendar now.", "sync_status"))
    typer.echo(_colorize(sync_connection(updated), "sync_status"))
    return updated


def _prompt_connection_role() -> str:
    typer.echo("Choose how Hrafn should treat this calendar:", err=True)
    typer.echo("1. Main calendar (default writable calendar for new events)", err=True)
    typer.echo("2. Secondary calendar (mirrors into main, receives busy from main)", err=True)
    choice = typer.prompt("Select 1 or 2", default="2", show_default=True).strip()
    return {
        "1": "main",
        "2": "secondary",
    }.get(choice) or (_raise_invalid_role())


def _raise_invalid_role() -> str:
    raise CalendarStackError("Invalid choice. Select 1 for main or 2 for secondary.")


def _prompt_account_collections(connection: CalendarConnection, collections: list[str]) -> list[str]:
    if len(collections) == 1:
        selected = [collections[0]]
        typer.echo(
            _colorize(
                f"Only one calendar was discovered for {connection.name}. Using '{selected[0]}'.",
                "sync_status",
            )
        )
        return selected

    typer.echo("Hrafn discovered these calendars for the account:", err=True)
    for index, collection in enumerate(collections, start=1):
        typer.echo(f"{index}. {collection}", err=True)
    typer.echo("Select each calendar once by number or exact ID. Type 'done' when finished.", err=True)

    selected: list[str] = []
    while True:
        prompt = "Select a calendar"
        if selected:
            prompt = f"Select a calendar [{len(selected)} chosen, or 'done']"
        choice = typer.prompt(prompt).strip()
        if choice.lower() == "done":
            if selected:
                return selected
            raise CalendarStackError("Select at least one calendar before finishing.")
        if choice.isdigit() and 1 <= int(choice) <= len(collections):
            candidate = collections[int(choice) - 1]
        elif choice in collections:
            candidate = choice
        else:
            raise CalendarStackError("Invalid calendar selection.")
        if candidate in selected:
            typer.echo(_colorize(f"'{candidate}' is already selected.", "warning"), err=True)
            continue
        selected.append(candidate)
        typer.echo(_colorize(f"Selected '{candidate}'.", "sync_status"))


def _select_account_calendar(connection: CalendarConnection, collections: list[str]) -> CalendarConnection:
    if not collections:
        if connection.kind == "google":
            raise CalendarStackError(
                "No remote calendar collections were discovered for account "
                f"'{connection.slug}'. In Google Calendar, verify the calendars are enabled in "
                f"Sync Select ({GOOGLE_SYNCSELECT_URL}) and confirm the Desktop OAuth client ID "
                "and client secret are correct."
            )
        raise CalendarStackError(
            f"No remote calendar collections were discovered for account '{connection.slug}'."
        )

    selected = _prompt_account_collections(connection, collections)
    role = _prompt_connection_role()
    updated = update_connection_selection(
        connection,
        selected_collections=selected,
        role=role,
    )
    default_calendar = selected[0]
    collection_label = ", ".join(selected)
    note = ""
    if role == "main" and len(selected) > 1:
        note = f" Default writable calendar: '{default_calendar}'."
    typer.echo(
        _colorize(
            f"Account '{updated.name}' is now pinned to calendars [{collection_label}] with role '{role}'.{note}",
            "sync_status",
        )
    )
    return updated


@app.command()
def connect(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Calendar provider type: local, caldav, or google."),
    ] = None,
) -> None:
    """Set up khal and vdirsyncer from inside Hrafn."""
    selected = provider.strip().lower() if provider else _provider_prompt()

    try:
        if selected == "local":
            _connect_local_calendar()
            return
        if selected == "caldav":
            _connect_caldav_calendar()
            return
        if selected == "google":
            _connect_google_calendar()
            return
        raise CalendarStackError("Unsupported provider. Use 'local', 'caldav', or 'google'.")
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc


@app.command()
def sync() -> None:
    """Run vdirsyncer to synchronize local calendars."""
    try:
        output = run_vdirsyncer_sync()
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    typer.echo(_colorize(output, "sync_status"))


@app.command("cleanup-mirrors")
def cleanup_mirrors() -> None:
    """Remove Hrafn-generated mirror events from local calendar stores."""
    try:
        output = cleanup_calendar_mirrors()
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    typer.echo(_colorize(output, "sync_status"))


@app.command("sync-service")
def sync_service(
    interval_minutes: Annotated[
        int | None,
        typer.Option("--interval-minutes", min=1, help="Resync cadence in minutes."),
    ] = None,
    enable: Annotated[
        bool | None,
        typer.Option("--enable/--disable", help="Enable or disable the background sync timer."),
    ] = None,
) -> None:
    """Inspect or update the background sync timer."""
    try:
        if interval_minutes is None and enable is None:
            status = service_status()
            typer.echo(f"enabled: {status.enabled}")
            typer.echo(f"interval_minutes: {status.interval_minutes}")
            typer.echo(f"timer_installed: {status.timer_installed}")
            typer.echo(f"timer_enabled: {status.timer_enabled}")
            typer.echo(f"timer_active: {status.timer_active}")
            typer.echo(f"next_sync_time: {status.next_sync_time or 'unknown'}")
            typer.echo(
                "next_sync_in: "
                + (
                    f"{status.next_sync_in_seconds}s"
                    if status.next_sync_in_seconds is not None
                    else "unknown"
                )
            )
            return

        updated = configure_sync_service(interval_minutes=interval_minutes, enabled=enable)
    except HrafnServiceError as exc:
        _print_service_error(exc)
        raise typer.Exit(code=1) from exc

    state = "enabled" if updated.enabled else "disabled"
    typer.echo(
        _colorize(
            f"Hrafn background sync timer {state}; interval = {updated.interval_minutes} minute(s).",
            "sync_status",
        )
    )


@app.command()
def dashboard() -> None:
    """Launch the Hrafn cyberpunk operations console."""
    try:
        from cli.bus import HrafnBus, HrafnState
        from cli.dashboard import HrafnDashboard
        from cli.runtime import HrafnRuntime
    except ModuleNotFoundError as exc:
        typer.echo(
            _colorize(
                f"Dashboard dependencies are missing: {exc}. Reinstall Hrafn to pick up the Textual dependency.",
                "error",
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc

    theme = load_theme()
    bus = HrafnBus(HrafnState(theme=theme))
    runtime = HrafnRuntime(bus)
    HrafnDashboard(bus, runtime).run()


@app.command()
def agenda(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text."),
    ] = False,
    days: Annotated[
        int,
        typer.Option("--days", min=1, help="Number of days to include starting today."),
    ] = 30,
) -> None:
    """Show the next upcoming events using khal."""
    try:
        events = read_agenda(days=days)
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    events = upcoming_events(events, limit=3)

    if json_output:
        typer.echo(json.dumps([event.to_dict() for event in events], indent=2))
        return

    if not events:
        typer.echo(_colorize("No upcoming events from khal.", "empty"))
        return

    for event in events:
        time_text = _colorize(format_agenda_time(event.start), "agenda_time")
        title_text = _colorize(event.title, "agenda_title")
        calendar_text = _colorize(f"[{event.calendar}]", "sync_status")
        typer.echo(f"{time_text} {title_text} {calendar_text}")


@app.command()
def calendars() -> None:
    """List calendars available to khal."""
    try:
        calendar_names = list_calendars()
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    if not calendar_names:
        typer.echo(_colorize("No calendars available in khal.", "empty"))
        return

    for name in calendar_names:
        typer.echo(_colorize(name, "agenda_title"))


@app.command("new-event")
def new_event(
    title: Annotated[
        str,
        typer.Option("--title", help="Event title. Example: 'Architecture Meeting'."),
    ],
    start: Annotated[
        str,
        typer.Option(
            "--start",
            help="Start value accepted by khal. Example: '2026-03-14 09:00' or 'tomorrow 9am'.",
        ),
    ],
    end: Annotated[
        str | None,
        typer.Option(
            "--end",
            help="Optional end value accepted by khal. Example: '2026-03-14 10:00' or '1h'.",
        ),
    ] = None,
    calendar: Annotated[
        str | None,
        typer.Option(
            "--calendar",
            help="Optional calendar name. Defaults to Hrafn's main calendar when configured. Example: 'main'.",
        ),
    ] = None,
) -> None:
    """Create a new event with khal.

    Examples:
      hrafn new-event --title "Architecture Meeting" --start "2026-03-14 09:00" --end "2026-03-14 10:00"
      hrafn new-event --title "Lunch" --start "tomorrow 12:30" --end "1h"
      hrafn new-event --title "Planning" --start "2026-03-17 14:00" --calendar "work"
    """
    selected_calendar = calendar
    try:
        if not selected_calendar:
            selected_calendar = get_main_calendar_name()
            if not selected_calendar:
                available = list_calendars()
                if len(available) == 1:
                    selected_calendar = available[0]
                elif len(available) > 1:
                    raise CalendarStackError(
                        "Multiple calendars are available and no main calendar is configured. Re-run with '--calendar <name>' or reconnect one account as the main calendar."
                    )
        output = create_event(title=title, start=start, end=end, calendar=selected_calendar)
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    typer.echo(_colorize(output, "sync_status"))


@app.command()
def signals(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text."),
    ] = False,
    days: Annotated[
        int,
        typer.Option("--days", min=1, help="Number of days to inspect starting today."),
    ] = 30,
) -> None:
    """Compute operational calendar signals from khal events."""
    try:
        events = read_agenda(days=days)
    except CalendarStackError as exc:
        _print_calendar_error(exc)
        raise typer.Exit(code=1) from exc

    active_signals = compute_signals(events)

    if json_output:
        typer.echo(json.dumps([signal.to_dict() for signal in active_signals], indent=2))
        return

    if not active_signals:
        typer.echo(_colorize("No active signals.", "empty"))
        return

    for signal in active_signals:
        typer.echo(_colorize(signal.name, _signal_color(signal.name)))


if __name__ == "__main__":
    app()
