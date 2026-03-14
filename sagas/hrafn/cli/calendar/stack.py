from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from dateutil import parser as date_parser

from cli.config import ensure_runtime_dirs, get_paths, load_hrafn_json
from cli.models import EventRecord


INSTALL_HINTS = {
    "vdirsyncer": [
        "Arch: sudo pacman -S vdirsyncer",
        "Debian/Ubuntu: sudo apt install vdirsyncer",
        "Fedora: sudo dnf install vdirsyncer",
    ],
    "khal": [
        "Arch: sudo pacman -S khal",
        "Debian/Ubuntu: sudo apt install khal",
        "Fedora: sudo dnf install khal",
    ],
}

KHAL_FIELDS = [
    "title",
    "start-long-full",
    "end-long-full",
    "calendar",
    "location",
]
KHAL_MARKER = "# Managed by Hrafn"
VDIRSYNCER_MARKER = "# Managed by Hrafn"
GOOGLE_SYNCSELECT_URL = "https://calendar.google.com/calendar/syncselect"
GOOGLE_CLOUD_CONSOLE_URL = "https://console.cloud.google.com/"
GOOGLE_BRANDING_URL = "https://console.cloud.google.com/auth/branding"
GOOGLE_AUDIENCE_URL = "https://console.cloud.google.com/auth/audience"
GOOGLE_DATA_ACCESS_URL = "https://console.cloud.google.com/auth/data-access"
GOOGLE_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
HRAFN_MANAGED_MIRROR = "X-HRAFN-MANAGED-MIRROR"
HRAFN_MIRROR_KIND = "X-HRAFN-MIRROR-KIND"
HRAFN_SOURCE_SLUG = "X-HRAFN-SOURCE-SLUG"
HRAFN_SOURCE_NAME = "X-HRAFN-SOURCE-NAME"
HRAFN_SOURCE_PATH = "X-HRAFN-SOURCE-PATH"
HRAFN_SOURCE_UID = "X-HRAFN-SOURCE-UID"
HRAFN_BUSY_SUMMARY = "Busy"


class CalendarStackError(RuntimeError):
    """Raised when khal or vdirsyncer commands fail."""


@dataclass(slots=True)
class CalendarConnection:
    kind: str
    name: str
    slug: str
    path: str
    role: str = "secondary"
    sync_past_days: int | None = None
    sync_future_days: int | None = None
    selected_collections: list[str] | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    token_file: str | None = None


def ensure_binary(name: str) -> None:
    if shutil.which(name):
        return

    hints = "\n".join(INSTALL_HINTS.get(name, [f"Install '{name}' and ensure it is on PATH."]))
    raise CalendarStackError(f"Required binary '{name}' is not installed.\n{hints}")


def run_vdirsyncer_sync() -> str:
    ensure_binary("vdirsyncer")
    ensure_vdirsyncer_ready()
    connections = load_connections()
    render_calendar_stack(connections)
    sync_result = _run_command(["vdirsyncer", "sync"])
    mirror_state = _load_mirror_state()
    if mirror_state.get("pending_cleanup"):
        _save_mirror_state(created=0, updated=0, removed=int(mirror_state.get("removed", 0)), pending_cleanup=False)
        return (sync_result.stdout or sync_result.stderr).strip() or "vdirsyncer sync completed."

    mirror_summary = reconcile_calendar_mirrors()
    push_result = _run_command(["vdirsyncer", "sync"])
    lines = [(sync_result.stdout or sync_result.stderr).strip() or "vdirsyncer sync completed."]
    if mirror_summary:
        lines.append(mirror_summary)
    lines.append((push_result.stdout or push_result.stderr).strip() or "vdirsyncer sync completed.")
    return "\n".join(line for line in lines if line)


def sync_connection(connection: CalendarConnection) -> str:
    ensure_binary("vdirsyncer")
    ensure_vdirsyncer_ready()
    render_calendar_stack(load_connections())
    result = _run_command(["vdirsyncer", "sync", connection.slug], capture_output=False)
    return (result.stdout or result.stderr).strip() or f"Synchronized {connection.slug}."


def list_calendars() -> list[str]:
    ensure_binary("khal")
    ensure_khal_ready()
    result = _run_command(["khal", "printcalendars"])
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def read_agenda(days: int = 30) -> list[EventRecord]:
    ensure_binary("khal")
    ensure_khal_ready()
    command = ["khal", "list", "today", f"{days}d"]
    for main_calendar in get_main_calendar_names():
        command.extend(["-a", main_calendar])
    for field in KHAL_FIELDS:
        command.extend(["--json", field])
    result = _run_command(command)

    try:
        payload = _parse_khal_json_output(result.stdout)
    except json.JSONDecodeError as exc:
        raise CalendarStackError("khal returned invalid JSON for the agenda query.") from exc

    if not isinstance(payload, list):
        raise CalendarStackError("khal returned an unexpected agenda payload.")

    events: list[EventRecord] = []
    for raw_event in payload:
        if not isinstance(raw_event, dict):
            continue
        start_value = raw_event.get("start-long-full")
        end_value = raw_event.get("end-long-full")
        if start_value is None or end_value is None:
            continue
        events.append(
            EventRecord(
                title=_coerce_string(raw_event.get("title"), "(untitled event)"),
                start=_normalize_datetime(start_value),
                end=_normalize_datetime(end_value),
                calendar=_coerce_string(raw_event.get("calendar"), "default"),
                location=_coerce_optional_string(raw_event.get("location")),
            )
        )

    return sorted(events, key=lambda event: event.start)


def create_event(
    *,
    title: str,
    start: str,
    end: str | None,
    calendar: str | None,
) -> str:
    ensure_binary("khal")
    ensure_khal_ready()
    command = ["khal", "new"]
    if calendar:
        command.extend(["-a", calendar])
    command.append(start)
    if end:
        command.append(end)
    command.append(title)
    result = _run_command(command)
    return (result.stdout or result.stderr).strip() or "Event created."


def setup_local_calendar(name: str) -> CalendarConnection:
    slug = slugify(name)
    if not slug:
        raise CalendarStackError("Calendar name must contain at least one letter or number.")

    _ensure_hrafn_calendar_dirs()
    connections = load_connections()
    connection = CalendarConnection(
        kind="local",
        name=name.strip(),
        slug=slug,
        path=str(_calendar_root() / "local" / slug),
        role="main",
        sync_past_days=None,
        sync_future_days=None,
        selected_collections=[slug],
    )
    Path(connection.path).mkdir(parents=True, exist_ok=True)
    connections = _upsert_connection(connections, connection)
    save_connections(connections)
    render_calendar_stack(connections)
    return connection


def setup_caldav_calendar(
    *,
    name: str,
    url: str,
    username: str,
    password: str,
) -> CalendarConnection:
    slug = slugify(name)
    if not slug:
        raise CalendarStackError("Account name must contain at least one letter or number.")
    if not _looks_like_url(url):
        raise CalendarStackError("CalDAV URL must start with http:// or https://")
    if not username.strip():
        raise CalendarStackError("Username is required for a CalDAV connection.")
    if not password:
        raise CalendarStackError("Password is required for a CalDAV connection.")

    _ensure_hrafn_calendar_dirs()
    connections = load_connections()
    connection = CalendarConnection(
        kind="caldav",
        name=name.strip(),
        slug=slug,
        path=str(_calendar_root() / slug),
        role="secondary",
        sync_past_days=7,
        sync_future_days=3650,
        url=url.strip(),
        username=username.strip(),
        password=password,
    )
    Path(connection.path).mkdir(parents=True, exist_ok=True)
    connections = _upsert_connection(connections, connection)
    save_connections(connections)
    render_calendar_stack(connections)
    return connection


def setup_google_calendar(
    *,
    name: str,
    client_id: str,
    client_secret: str,
) -> CalendarConnection:
    slug = slugify(name)
    if not slug:
        raise CalendarStackError("Account name must contain at least one letter or number.")
    if ".apps.googleusercontent.com" not in client_id:
        raise CalendarStackError(
            "Google client ID should look like a Desktop OAuth client ID ending in '.apps.googleusercontent.com'."
        )
    if not client_secret.strip():
        raise CalendarStackError("Google client secret is required.")

    _ensure_hrafn_calendar_dirs()
    connections = load_connections()
    token_file = _token_root() / f"{slug}-google-token.json"
    connection = CalendarConnection(
        kind="google",
        name=name.strip(),
        slug=slug,
        path=str(_calendar_root() / slug),
        role="secondary",
        sync_past_days=7,
        sync_future_days=3650,
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        token_file=str(token_file),
    )
    Path(connection.path).mkdir(parents=True, exist_ok=True)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    connections = _upsert_connection(connections, connection)
    save_connections(connections)
    render_calendar_stack(connections)
    return connection


def finalize_caldav_connection(connection: CalendarConnection) -> str:
    ensure_binary("vdirsyncer")
    ensure_binary("khal")
    ensure_vdirsyncer_ready()
    discover_remote_collections(connection)
    return "CalDAV calendars discovered."


def finalize_google_connection(connection: CalendarConnection) -> str:
    ensure_binary("vdirsyncer")
    ensure_binary("khal")
    ensure_vdirsyncer_ready()
    discover_remote_collections(connection)
    return "Google calendars discovered."


def discover_remote_collections(connection: CalendarConnection) -> list[str]:
    ensure_binary("vdirsyncer")
    ensure_binary("khal")
    ensure_vdirsyncer_ready()
    render_calendar_stack(load_connections())

    result = subprocess.run(
        ["vdirsyncer", "discover", "--list", connection.slug],
        capture_output=True,
        check=False,
        text=True,
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    collections = _parse_discovered_remote_collections(output, connection)
    if collections:
        return collections
    if result.returncode != 0:
        raise CalendarStackError(_format_command_error(["vdirsyncer", "discover", "--list", connection.slug], output))
    return []


def list_discovered_collections(connection: CalendarConnection) -> list[str]:
    root = Path(connection.path)
    if not root.exists():
        return []
    return sorted(
        item.name
        for item in root.iterdir()
        if item.is_dir()
    )


def update_connection_selection(
    connection: CalendarConnection,
    *,
    selected_collections: str | list[str],
    role: str,
) -> CalendarConnection:
    normalized_role = _normalize_role(role)
    if normalized_role not in {"main", "secondary", "source"}:
        raise CalendarStackError("Connection role must be 'main', 'secondary', or 'source'.")

    if isinstance(selected_collections, str):
        normalized_collections = [selected_collections]
    else:
        normalized_collections = [str(item).strip() for item in selected_collections if str(item).strip()]
    deduped_collections = list(dict.fromkeys(normalized_collections))
    if not deduped_collections:
        raise CalendarStackError("Select at least one calendar collection.")

    available = set(list_discovered_collections(connection))
    missing = [item for item in deduped_collections if available and item not in available]
    if missing:
        raise CalendarStackError(
            f"Collection '{missing[0]}' was not discovered for account '{connection.slug}'."
        )

    updated = CalendarConnection(
        kind=connection.kind,
        name=connection.name,
        slug=connection.slug,
        path=connection.path,
        role=normalized_role,
        sync_past_days=connection.sync_past_days,
        sync_future_days=connection.sync_future_days,
        selected_collections=deduped_collections,
        url=connection.url,
        username=connection.username,
        password=connection.password,
        client_id=connection.client_id,
        client_secret=connection.client_secret,
        token_file=connection.token_file,
    )

    connections = load_connections()
    if normalized_role == "main":
        connections = [
            CalendarConnection(
                kind=item.kind,
                name=item.name,
                slug=item.slug,
                path=item.path,
                role="secondary" if item.role in {"master", "main"} and item.slug != updated.slug else item.role,
                sync_past_days=item.sync_past_days,
                sync_future_days=item.sync_future_days,
                selected_collections=item.selected_collections,
                url=item.url,
                username=item.username,
                password=item.password,
                client_id=item.client_id,
                client_secret=item.client_secret,
                token_file=item.token_file,
            )
            for item in connections
        ]
    connections = _upsert_connection(connections, updated)
    save_connections(connections)
    render_calendar_stack(connections)
    for collection in deduped_collections:
        (Path(connection.path) / collection).mkdir(parents=True, exist_ok=True)
    return updated


def update_connection_sync_window(
    connection: CalendarConnection,
    *,
    past_days: int | None = None,
    future_days: int | None = None,
) -> CalendarConnection:
    updated = CalendarConnection(
        kind=connection.kind,
        name=connection.name,
        slug=connection.slug,
        path=connection.path,
        role=connection.role,
        sync_past_days=connection.sync_past_days if past_days is None else max(0, past_days),
        sync_future_days=connection.sync_future_days if future_days is None else max(0, future_days),
        selected_collections=connection.selected_collections,
        url=connection.url,
        username=connection.username,
        password=connection.password,
        client_id=connection.client_id,
        client_secret=connection.client_secret,
        token_file=connection.token_file,
    )
    connections = _upsert_connection(load_connections(), updated)
    save_connections(connections)
    render_calendar_stack(connections)
    return updated


def get_main_calendar_name() -> str | None:
    for connection in load_connections():
        if connection.role in {"master", "main"}:
            return connection.slug
    return None


def get_main_calendar_names() -> list[str]:
    for connection in load_connections():
        if connection.role in {"master", "main"}:
            return _khal_calendar_names(connection)
    return []


def has_khal_config() -> bool:
    return _khal_config_path().exists()


def has_vdirsyncer_config() -> bool:
    return _vdirsyncer_config_path().exists()


def ensure_khal_ready() -> None:
    if has_khal_config():
        return
    raise CalendarStackError(
        "khal is not configured yet. Run 'hrafn connect' to create the standard khal configuration."
    )


def ensure_vdirsyncer_ready() -> None:
    if has_vdirsyncer_config():
        return
    raise CalendarStackError(
        "vdirsyncer is not configured yet. Run 'hrafn connect' to add a sync-backed calendar."
    )


def load_connections() -> list[CalendarConnection]:
    paths = ensure_runtime_dirs()
    payload = load_hrafn_json(paths.calendar_stack_file)
    connections: list[CalendarConnection] = []

    for item in payload.get("connections", []):
        if not isinstance(item, dict):
            continue
        try:
            connections.append(
                CalendarConnection(
                    kind=str(item["kind"]),
                    name=str(item["name"]),
                    slug=str(item["slug"]),
                    path=str(item["path"]),
                    role=_normalize_role(item.get("role")),
                    sync_past_days=_load_sync_past_days(item),
                    sync_future_days=_load_sync_future_days(item),
                    selected_collections=_load_selected_collections(item.get("selected_collections")),
                    url=str(item["url"]) if item.get("url") else None,
                    username=str(item["username"]) if item.get("username") else None,
                    password=str(item["password"]) if item.get("password") else None,
                    client_id=str(item["client_id"]) if item.get("client_id") else None,
                    client_secret=str(item["client_secret"]) if item.get("client_secret") else None,
                    token_file=str(item["token_file"]) if item.get("token_file") else None,
                )
            )
        except KeyError:
            continue

    return connections


def save_connections(connections: list[CalendarConnection]) -> None:
    paths = ensure_runtime_dirs()
    payload = {"connections": [asdict(connection) for connection in connections]}
    paths.calendar_stack_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render_calendar_stack(connections: list[CalendarConnection]) -> None:
    _ensure_hrafn_calendar_dirs()
    khal_path = _khal_config_path()
    vdirsyncer_path = _vdirsyncer_config_path()

    _guard_unmanaged_config(khal_path, KHAL_MARKER, "khal")
    _guard_unmanaged_config(vdirsyncer_path, VDIRSYNCER_MARKER, "vdirsyncer")

    khal_path.parent.mkdir(parents=True, exist_ok=True)
    khal_path.write_text(_render_khal_config(connections), encoding="utf-8")

    sync_connections = [connection for connection in connections if connection.kind in {"caldav", "google"}]
    if sync_connections:
        vdirsyncer_path.parent.mkdir(parents=True, exist_ok=True)
        vdirsyncer_path.write_text(_render_vdirsyncer_config(sync_connections), encoding="utf-8")
        _prune_stale_vdirsyncer_status(sync_connections)
    elif vdirsyncer_path.exists() and _is_hrafn_managed(vdirsyncer_path, VDIRSYNCER_MARKER):
        vdirsyncer_path.unlink()


def reconcile_calendar_mirrors() -> str:
    connections = load_connections()
    main_connection = next((connection for connection in connections if connection.role == "main"), None)
    detail_sources = [connection for connection in connections if connection.role in {"secondary", "source"}]
    writable_secondaries = [connection for connection in connections if connection.role == "secondary"]
    if main_connection is None or not detail_sources:
        return ""

    main_dir = _selected_collection_path(main_connection)
    if main_dir is None:
        return ""
    main_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    updated = 0
    removed = 0

    expected_main_paths: set[Path] = set()
    main_native_files = _list_native_ics_files(main_dir)
    expected_secondary_paths: dict[str, set[Path]] = {}

    for secondary in detail_sources:
        expected_secondary_paths[secondary.slug] = set()
        for secondary_dir in _selected_collection_paths(secondary):
            secondary_dir.mkdir(parents=True, exist_ok=True)

            for source_file in _list_native_ics_files(secondary_dir):
                target_file = main_dir / _mirror_filename("detail", secondary.slug, source_file)
                expected_main_paths.add(target_file)
                changed, existed = _write_mirror_file(
                    source_file=source_file,
                    target_file=target_file,
                    target_kind="detail",
                    source_connection=secondary,
                )
                if changed:
                    if existed:
                        updated += 1
                    else:
                        created += 1

            if secondary.role == "secondary":
                for source_file in main_native_files:
                    target_file = secondary_dir / _mirror_filename("busy", main_connection.slug, source_file)
                    expected_secondary_paths[secondary.slug].add(target_file)
                    changed, existed = _write_mirror_file(
                        source_file=source_file,
                        target_file=target_file,
                        target_kind="busy",
                        source_connection=main_connection,
                    )
                    if changed:
                        if existed:
                            updated += 1
                        else:
                            created += 1

    removed += _prune_stale_mirror_files(main_dir, expected_main_paths, kind="detail")
    for secondary in detail_sources:
        for secondary_dir in _selected_collection_paths(secondary):
            removed += _prune_stale_mirror_files(
                secondary_dir,
                expected_secondary_paths.get(secondary.slug, set()),
                kind="busy",
            )

    _save_mirror_state(created=created, updated=updated, removed=removed)
    return f"Hrafn mirror sync: created={created} updated={updated} removed={removed}"


def cleanup_calendar_mirrors() -> str:
    removed = 0
    for connection in load_connections():
        for collection_dir in _selected_collection_paths(connection):
            if not collection_dir.exists():
                continue
            for candidate in collection_dir.glob("*.ics"):
                if not candidate.is_file():
                    continue
                if not _is_hrafn_generated_ics(candidate):
                    continue
                candidate.unlink()
                removed += 1
    _save_mirror_state(created=0, updated=0, removed=removed, pending_cleanup=True)
    _prune_stale_vdirsyncer_status(load_connections())
    sync_result = _run_command(["vdirsyncer", "sync"])
    return (
        f"Removed {removed} Hrafn-generated mirror file(s). "
        f"Deletion sync completed. The next 'hrafn sync' will skip mirror regeneration once.\n"
        f"{(sync_result.stdout or sync_result.stderr).strip() or 'vdirsyncer sync completed.'}"
    )


def _run_command(
    args: Sequence[str],
    *,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            capture_output=capture_output,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() or (exc.stdout or "").strip() or "Command returned a non-zero exit status."
        raise CalendarStackError(_format_command_error(args, stderr)) from exc


def _format_command_error(args: Sequence[str], stderr: str) -> str:
    if args and args[0] == "vdirsyncer":
        if "aiohttp-oauthlib not installed" in stderr:
            return _build_vdirsyncer_google_oauth_error(stderr)
        href = _extract_vdirsyncer_missing_href(stderr)
        if href:
            return _build_vdirsyncer_missing_href_error(href, stderr)
        forbidden = _extract_vdirsyncer_forbidden_href(stderr)
        if forbidden:
            return _build_vdirsyncer_forbidden_href_error(forbidden, stderr)
    return stderr


def _extract_vdirsyncer_missing_href(stderr: str) -> str | None:
    match = re.search(r"Unknown error occurred for [^:]+: (?P<href>/\S+)", stderr)
    if match:
        return match.group("href")

    match = re.search(r"NotFoundError\((?P<quote>['\"])(?P<href>/.+?)(?P=quote)\)", stderr)
    if match:
        return match.group("href")

    return None


def _extract_vdirsyncer_forbidden_href(stderr: str) -> str | None:
    match = re.search(r"403, message='Forbidden', url='(?P<href>https://[^']+)'", stderr)
    if match:
        return match.group("href")
    return None


def _build_vdirsyncer_missing_href_error(href: str, stderr: str) -> str:
    decoded_href = unquote(href)
    filename = unquote(href.split("/events/", 1)[-1]) if "/events/" in href else unquote(href.rsplit("/", 1)[-1])
    lines = [stderr]

    if "/events/" in decoded_href:
        lines.extend(
            [
                "",
                "Hrafn diagnosis: the remote CalDAV server advertised an event href that vdirsyncer could not fetch.",
                f"Broken remote item: {decoded_href}",
            ]
        )

        if filename.startswith("null-"):
            lines.extend(
                [
                    "This usually means a stale or malformed Google Calendar event reference.",
                    f"Likely offending event key: {filename.removesuffix('.ics')}",
                    "Remediation: find that event in the calendar provider, then delete or recreate it before syncing again.",
                ]
            )
        else:
            lines.append(
                f"Remediation: inspect or remove the remote event backing '{filename}', then run sync again."
            )

    return "\n".join(lines)


def _build_vdirsyncer_forbidden_href_error(href: str, stderr: str) -> str:
    decoded_href = unquote(href)
    lines = [
        stderr,
        "",
        "Hrafn diagnosis: Google rejected a write to the selected calendar with 403 Forbidden.",
        f"Rejected remote target: {decoded_href}",
        "This usually means the calendar is visible to the account but is not writable.",
        "Remediation: reconnect or reclassify that calendar as a 'source' calendar instead of 'secondary'.",
        "A 'source' calendar still syncs in and mirrors detail into main, but Hrafn will stop pushing Busy blocks back to it.",
    ]
    return "\n".join(lines)


def _build_vdirsyncer_google_oauth_error(stderr: str) -> str:
    install_hint = _google_oauth_install_hint()
    lines = [
        stderr,
        "",
        "Hrafn diagnosis: vdirsyncer's Google backend is missing its aiohttp OAuth dependency.",
    ]
    if install_hint:
        lines.append(f"Install it with: {install_hint}")
    else:
        lines.append("Install the distro package that provides the Python module 'aiohttp_oauthlib', then run the command again.")
    lines.append("After installing it, rerun 'hrafn connect --provider google' or 'vdirsyncer discover'.")
    return "\n".join(lines)


def _google_oauth_install_hint() -> str | None:
    distro = _detect_linux_distro()
    return {
        "arch": "sudo pacman -S python-aiohttp-oauthlib",
        "endeavouros": "sudo pacman -S python-aiohttp-oauthlib",
        "debian": "sudo apt install python3-aiohttp-oauthlib",
        "ubuntu": "sudo apt install python3-aiohttp-oauthlib",
        "fedora": "sudo dnf install python3-aiohttp-oauthlib",
    }.get(distro)


def _detect_linux_distro() -> str | None:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return None

    for raw_line in os_release.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("ID="):
            return raw_line.split("=", 1)[1].strip().strip('"').lower()
    return None


def _parse_khal_json_output(output: str | None) -> list[object]:
    raw = (output or "").strip()
    if not raw:
        return []

    if raw.startswith("["):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        else:
            if isinstance(payload, list) and (not payload or isinstance(payload[0], dict)):
                return payload

    events: list[object] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, list):
            events.extend(payload)
            continue
        events.append(payload)
    return events


def _parse_discovered_remote_collections(output: str, connection: CalendarConnection) -> list[str]:
    header = f"{connection.slug}_remote:"
    in_section = False
    collections: list[str] = []

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if line == header:
            in_section = True
            continue
        if not in_section:
            continue
        if not line.startswith("  - "):
            if line and not line.startswith("Saved for "):
                break
            continue
        match = re.match(r'\s*-\s+"([^"]+)"', line)
        if match:
            collections.append(match.group(1))
    return collections


def _coerce_string(value: object, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _coerce_optional_string(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _normalize_datetime(value: object) -> str:
    if value is None:
        raise CalendarStackError("khal returned an event without a start or end time.")

    try:
        parsed = date_parser.parse(str(value))
    except (TypeError, ValueError) as exc:
        raise CalendarStackError(f"Could not parse khal date/time value: {value}") from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    return parsed.isoformat(timespec="minutes")


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _upsert_connection(
    connections: list[CalendarConnection],
    connection: CalendarConnection,
) -> list[CalendarConnection]:
    filtered = [existing for existing in connections if existing.slug != connection.slug]
    filtered.append(connection)
    return sorted(filtered, key=lambda item: item.slug)


def _selected_collection_path(connection: CalendarConnection) -> Path | None:
    paths = _selected_collection_paths(connection)
    if not paths:
        return None
    return paths[0]


def _selected_collection_paths(connection: CalendarConnection) -> list[Path]:
    if connection.kind == "local":
        return [Path(connection.path)]
    selected = connection.selected_collections or []
    if not selected:
        return []
    return [Path(connection.path) / collection for collection in selected]


def _khal_calendar_names(connection: CalendarConnection) -> list[str]:
    selected = connection.selected_collections or []
    if connection.kind == "local" or not selected:
        return [connection.slug]
    names = [connection.slug]
    for index in range(1, len(selected)):
        names.append(f"{connection.slug}__{index + 1}")
    return names


def _list_native_ics_files(collection_dir: Path) -> list[Path]:
    return sorted(
        candidate
        for candidate in collection_dir.glob("*.ics")
        if candidate.is_file() and not _is_hrafn_generated_ics(candidate)
    )


def _is_hrafn_generated_ics(path: Path) -> bool:
    try:
        return HRAFN_MANAGED_MIRROR in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _mirror_filename(kind: str, source_slug: str, source_file: Path) -> str:
    digest = sha1(f"{source_slug}:{source_file.parent.name}:{source_file.name}".encode("utf-8")).hexdigest()[:16]
    return f"hrafn-{kind}-{source_slug}-{digest}.ics"


def _write_mirror_file(
    *,
    source_file: Path,
    target_file: Path,
    target_kind: str,
    source_connection: CalendarConnection,
) -> tuple[bool, bool]:
    rendered = _render_mirror_ics(
        source_file=source_file,
        target_kind=target_kind,
        source_connection=source_connection,
    )
    existed = target_file.exists()
    current = target_file.read_text(encoding="utf-8") if existed else None
    if current == rendered:
        return False, existed
    target_file.write_text(rendered, encoding="utf-8")
    return True, existed


def _prune_stale_mirror_files(collection_dir: Path, expected_paths: set[Path], *, kind: str) -> int:
    removed = 0
    for candidate in collection_dir.glob(f"hrafn-{kind}-*.ics"):
        if candidate in expected_paths:
            continue
        if not _is_hrafn_generated_ics(candidate):
            continue
        candidate.unlink()
        removed += 1
    return removed


def _render_mirror_ics(
    *,
    source_file: Path,
    target_kind: str,
    source_connection: CalendarConnection,
) -> str:
    payload = source_file.read_text(encoding="utf-8")
    lines = _unfold_ics_lines(payload)
    event_blocks = _extract_vevent_blocks(lines)
    if not event_blocks:
        raise CalendarStackError(f"Calendar file '{source_file}' does not contain a VEVENT.")

    header = lines[:event_blocks[0][0]]
    footer = lines[event_blocks[-1][1] + 1 :]
    transformed_events = []
    for start, end in event_blocks:
        transformed_events.extend(
            _transform_event_block(
                lines[start : end + 1],
                target_kind=target_kind,
                source_connection=source_connection,
                source_file=source_file,
            )
        )
    if not any(line == f"{HRAFN_MANAGED_MIRROR}:TRUE" for line in header):
        insert_at = len(header) - 1 if header and header[-1] == "END:VCALENDAR" else len(header)
        header = header[:insert_at] + [f"{HRAFN_MANAGED_MIRROR}:TRUE"] + header[insert_at:]
    return _fold_ics_lines(header + transformed_events + footer) + "\n"


def _extract_vevent_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    depth = 0
    for index, line in enumerate(lines):
        if line == "BEGIN:VEVENT":
            if depth == 0:
                start = index
            depth += 1
        elif line == "END:VEVENT" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append((start, index))
                start = None
    return blocks


def _transform_event_block(
    event_lines: list[str],
    *,
    target_kind: str,
    source_connection: CalendarConnection,
    source_file: Path,
) -> list[str]:
    cleaned = _strip_nested_component(event_lines, "VALARM")
    source_uid = _first_property_value(cleaned, "UID") or source_file.stem
    mirror_uid = _mirror_uid(target_kind, source_connection.slug, source_uid)
    summary = _unescape_ics_text(_first_property_value(cleaned, "SUMMARY") or HRAFN_BUSY_SUMMARY)
    dtstamp = _first_property_value(cleaned, "DTSTAMP") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines: list[str] = []
    for line in cleaned:
        if line == "BEGIN:VEVENT":
            lines.append(line)
            continue
        if line == "END:VEVENT":
            lines.extend(
                [
                    f"UID:{mirror_uid}",
                    f"SUMMARY:{_escape_ics_text(_mirror_summary(target_kind, source_connection.name, summary))}",
                    f"{HRAFN_MANAGED_MIRROR}:TRUE",
                    f"{HRAFN_MIRROR_KIND}:{target_kind}",
                    f"{HRAFN_SOURCE_SLUG}:{_escape_ics_text(source_connection.slug)}",
                    f"{HRAFN_SOURCE_NAME}:{_escape_ics_text(source_connection.name)}",
                    f"{HRAFN_SOURCE_PATH}:{_escape_ics_text(source_file.name)}",
                    f"{HRAFN_SOURCE_UID}:{_escape_ics_text(source_uid)}",
                    f"DTSTAMP:{dtstamp}",
                ]
            )
            if target_kind == "busy":
                lines.extend(["CLASS:PRIVATE", "TRANSP:OPAQUE"])
            lines.append(line)
            continue

        property_name = _property_name(line)
        if property_name in {"UID", "SUMMARY", HRAFN_MANAGED_MIRROR, HRAFN_MIRROR_KIND, HRAFN_SOURCE_SLUG, HRAFN_SOURCE_NAME, HRAFN_SOURCE_PATH, HRAFN_SOURCE_UID, "DTSTAMP", "CLASS", "TRANSP"}:
            continue
        if target_kind == "detail" and property_name in {"ATTENDEE", "ORGANIZER"}:
            continue
        if target_kind == "busy" and property_name in {"ATTENDEE", "ORGANIZER", "DESCRIPTION", "LOCATION", "URL"}:
            continue
        lines.append(line)
    return lines


def _strip_nested_component(lines: list[str], component_name: str) -> list[str]:
    cleaned: list[str] = []
    depth = 0
    begin_marker = f"BEGIN:{component_name}"
    end_marker = f"END:{component_name}"
    for line in lines:
        if line == begin_marker:
            depth += 1
            continue
        if line == end_marker and depth:
            depth -= 1
            continue
        if depth == 0:
            cleaned.append(line)
    return cleaned


def _first_property_value(lines: list[str], name: str) -> str | None:
    prefix = f"{name}:"
    for line in lines:
        if _property_name(line) != name:
            continue
        if ":" not in line:
            return None
        return line.split(":", 1)[1]
    return None


def _property_name(line: str) -> str:
    return line.split(":", 1)[0].split(";", 1)[0].upper()


def _mirror_uid(kind: str, source_slug: str, source_uid: str) -> str:
    digest = sha1(f"{kind}:{source_slug}:{source_uid}".encode("utf-8")).hexdigest()
    return f"hrafn-{kind}-{source_slug}-{digest[:24]}"


def _mirror_summary(kind: str, source_name: str, source_summary: str) -> str:
    if kind == "busy":
        return HRAFN_BUSY_SUMMARY
    return f"[{source_name}] {source_summary}"


def _unfold_ics_lines(payload: str) -> list[str]:
    lines = payload.splitlines()
    unfolded: list[str] = []
    for line in lines:
        if unfolded and line[:1] in {" ", "\t"}:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _fold_ics_lines(lines: list[str]) -> str:
    folded: list[str] = []
    for line in lines:
        text = line.rstrip("\r\n")
        while len(text) > 75:
            folded.append(text[:75])
            text = " " + text[75:]
        folded.append(text)
    return "\n".join(folded)


def _escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _unescape_ics_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _load_mirror_state() -> dict[str, object]:
    paths = get_paths()
    if not paths.calendar_mirror_state_file.exists():
        return {}
    try:
        return json.loads(paths.calendar_mirror_state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_mirror_state(*, created: int, updated: int, removed: int, pending_cleanup: bool = False) -> None:
    paths = get_paths()
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created": created,
        "updated": updated,
        "removed": removed,
        "pending_cleanup": pending_cleanup,
    }
    paths.calendar_mirror_state_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_khal_config(connections: list[CalendarConnection]) -> str:
    lines = [
        KHAL_MARKER,
        "[calendars]",
    ]

    for connection in connections:
        if connection.kind == "local":
            lines.extend(
                [
                    f"[[{connection.slug}]]",
                    f"path = {connection.path}",
                    "",
                ]
            )
        elif connection.kind in {"caldav", "google"}:
            selected = connection.selected_collections or []
            if selected:
                for calendar_name, collection in zip(_khal_calendar_names(connection), selected, strict=False):
                    lines.extend(
                        [
                            f"[[{calendar_name}]]",
                            f"path = {Path(connection.path) / collection}",
                            "",
                        ]
                    )
            else:
                lines.extend(
                    [
                        f"[[{connection.slug}]]",
                        f"path = {connection.path}/*",
                        "type = discover",
                        "",
                    ]
                )
        else:
            lines.extend(
                [
                    f"[[{connection.slug}]]",
                    f"path = {connection.path}/*",
                    "type = discover",
                    "",
                ]
            )

    lines.extend(
        [
            "[locale]",
            "timeformat = %H:%M",
            "dateformat = %Y-%m-%d",
            "longdateformat = %Y-%m-%d",
            "datetimeformat = %Y-%m-%d %H:%M",
            "longdatetimeformat = %Y-%m-%d %H:%M",
            "",
        ]
    )
    return "\n".join(lines)


def _render_vdirsyncer_config(connections: list[CalendarConnection]) -> str:
    lines = [
        VDIRSYNCER_MARKER,
        "[general]",
        f'status_path = "{_vdirsyncer_status_root()}/"',
        "",
    ]

    for connection in connections:
        pair_lines = [f"[pair {connection.slug}]"]
        if connection.role == "secondary":
            pair_lines.extend(
                [
                    f'a = "{connection.slug}_remote"',
                    f'b = "{connection.slug}_local"',
                ]
            )
        else:
            pair_lines.extend(
                [
                    f'a = "{connection.slug}_local"',
                    f'b = "{connection.slug}_remote"',
                ]
            )
        if connection.selected_collections:
            collections = connection.selected_collections
        elif connection.role == "secondary":
            collections = ["from a"]
        else:
            collections = ["from b"]
        lines.extend(
            pair_lines
            + [
                f"collections = {json.dumps(collections)}",
                'conflict_resolution = "b wins"',
                'metadata = ["displayname", "color"]',
                "",
                f"[storage {connection.slug}_local]",
                'type = "filesystem"',
                f'path = "{connection.path}/"',
                'fileext = ".ics"',
                "",
            ]
        )
        if connection.kind == "caldav":
            lines.extend(
                [
                    f"[storage {connection.slug}_remote]",
                    'type = "caldav"',
                    f'url = "{connection.url}"',
                    f'username = "{connection.username}"',
                    f'password = "{connection.password}"',
                    *_render_sync_window_lines(connection),
                    "",
                ]
            )
        elif connection.kind == "google":
            lines.extend(
                [
                    f"[storage {connection.slug}_remote]",
                    'type = "google_calendar"',
                    f'token_file = "{connection.token_file}"',
                    f'client_id = "{connection.client_id}"',
                    f'client_secret = "{connection.client_secret}"',
                    *_render_sync_window_lines(connection),
                    "",
                ]
            )
    return "\n".join(line for line in lines if line != "")


def _render_sync_window_lines(connection: CalendarConnection) -> list[str]:
    if connection.sync_past_days is None and connection.sync_future_days is None:
        return []
    past_days = max(0, connection.sync_past_days or 0)
    future_days = max(0, connection.sync_future_days or 0)
    return [
        f'start_date = "date.today() - timedelta(days={past_days})"',
        f'end_date = "date.today() + timedelta(days={future_days})"',
    ]


def _guard_unmanaged_config(path: Path, marker: str, tool_name: str) -> None:
    if not path.exists():
        return
    if _is_hrafn_managed(path, marker):
        return
    raise CalendarStackError(
        f"Existing {tool_name} config at {path} is not Hrafn-managed. Hrafn will not overwrite it automatically."
    )


def _is_hrafn_managed(path: Path, marker: str) -> bool:
    try:
        return marker in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _ensure_hrafn_calendar_dirs() -> None:
    _calendar_root().mkdir(parents=True, exist_ok=True)
    (_calendar_root() / "local").mkdir(parents=True, exist_ok=True)
    _vdirsyncer_status_root().mkdir(parents=True, exist_ok=True)


def _calendar_root() -> Path:
    return Path.home() / ".local" / "share" / "hrafn" / "calendars"


def _khal_config_path() -> Path:
    return Path.home() / ".config" / "khal" / "config"


def _vdirsyncer_config_path() -> Path:
    return Path.home() / ".config" / "vdirsyncer" / "config"


def _vdirsyncer_status_root() -> Path:
    return Path.home() / ".local" / "share" / "vdirsyncer" / "status"


def _prune_stale_vdirsyncer_status(connections: list[CalendarConnection]) -> None:
    status_root = _vdirsyncer_status_root()
    expected_slugs = {connection.slug for connection in connections if connection.kind in {"caldav", "google"}}

    for collections_file in status_root.glob("*.collections"):
        if collections_file.stem not in expected_slugs:
            collections_file.unlink(missing_ok=True)

    for pair_dir in status_root.iterdir() if status_root.exists() else []:
        if not pair_dir.is_dir():
            continue
        connection = next((item for item in connections if item.slug == pair_dir.name), None)
        if connection is None:
            shutil.rmtree(pair_dir, ignore_errors=True)
            continue
        selected = set(connection.selected_collections or [])
        if not selected:
            continue
        for items_file in pair_dir.glob("*.items"):
            if items_file.stem not in selected:
                items_file.unlink(missing_ok=True)


def _token_root() -> Path:
    return Path.home() / ".local" / "share" / "hrafn" / "tokens"


def _load_selected_collections(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    collections = [str(item) for item in value if str(item).strip()]
    return collections or None


def _load_sync_past_days(item: dict[str, object]) -> int | None:
    legacy = item.get("sync_from_today")
    if isinstance(legacy, bool):
        return 7 if legacy else None
    value = item.get("sync_past_days")
    if isinstance(value, int):
        return max(0, value)
    return None


def _load_sync_future_days(item: dict[str, object]) -> int | None:
    legacy = item.get("sync_from_today")
    if isinstance(legacy, bool):
        return 3650 if legacy else None
    value = item.get("sync_future_days")
    if isinstance(value, int):
        return max(0, value)
    return None


def _normalize_role(value: object) -> str:
    role = str(value or "secondary")
    if role == "master":
        return "main"
    if role == "busy_target":
        return "source"
    return role
