# Hrafn

Hrafn is SagaOS's terminal-first calendar intelligence layer.

It does not talk to calendar providers directly. Hrafn is a thin wrapper around:

- `vdirsyncer` for synchronization
- `khal` for querying and writing local calendars

That keeps Hrafn focused on operational awareness instead of calendar API
integration.

## Architecture

Hrafn assumes the standard `vdirsyncer` and `khal` configuration locations are
already in use.

- remote providers sync into local `.ics` data through `vdirsyncer`
- `khal` reads and writes those local calendars
- Hrafn shells out to those tools and computes signals from their results

Hrafn does not implement:

- Google OAuth
- Google Calendar API access
- token storage
- custom calendar databases
- provider-specific sync code

## Commands

```bash
hrafn connect
hrafn dashboard
hrafn sync
hrafn agenda
hrafn agenda --json
hrafn calendars
hrafn new-event --title "Architecture Meeting" --start "2026-03-14 09:00" --end "2026-03-14 10:00"
hrafn signals
```

## Command Behavior

`hrafn connect`

- sets up calendar access from inside Hrafn
- creates standard `~/.config/khal/config` and `~/.config/vdirsyncer/config`
- supports a local calendar, a CalDAV account, or a Google account through `vdirsyncer`
- includes an in-CLI Google setup walkthrough for creating a user-owned Desktop OAuth client
- runs `vdirsyncer discover` and `vdirsyncer sync` for sync-backed connections
- if multiple remote calendars are discovered for an account, Hrafn asks which single calendar should be pinned for that account
- supports one main calendar, unlimited read-only source calendars, and unlimited writable secondary calendars
- mirrors full-detail source and secondary events into the main calendar, prefixing the title with the source calendar name
- mirrors main-calendar events back only into writable secondary calendars as `Busy` blocks

`hrafn dashboard`

- launches the cyberpunk terminal operations console
- subscribes to the in-process Hrafn bus instead of owning calendar/task state
- publishes keyboard actions like refresh, sync, join-next-meeting, and complete-task onto the bus

`hrafn sync`

- runs `vdirsyncer sync`

`hrafn agenda`

- runs `khal list today 30d`
- uses khal JSON output for deterministic parsing
- supports `--json`

`hrafn calendars`

- runs `khal printcalendars`

`hrafn new-event`

- runs `khal new`
- supports `--calendar`, `--title`, `--start`, and `--end`

`hrafn signals`

- computes `meeting_starting_soon`
- computes `meeting_live`
- computes `focus_window_available`
- uses events returned by `khal`

## Dependencies

Hrafn requires these binaries on `PATH`:

- `vdirsyncer`
- `khal`

If one is missing, Hrafn prints install guidance for Arch, Debian/Ubuntu, and
Fedora.

If khal or vdirsyncer is not configured yet, Hrafn points users to `hrafn connect`
instead of expecting them to run external setup commands.

## Google Accounts

Hrafn stays OSS-friendly by not shipping a project-owned Google OAuth client.

For Google Calendar, Hrafn guides each user through creating their own Desktop
OAuth client in Google Cloud Console, then stores that client in the
Hrafn-managed `vdirsyncer` config and lets `vdirsyncer` handle authorization and
token refresh.

On Arch, Debian/Ubuntu, and Fedora, `./install.sh` also installs the system
package that provides `aiohttp_oauthlib`, which `vdirsyncer` needs for the
Google OAuth flow.

## Roles

When a sync-backed account is connected, Hrafn asks how to classify the chosen
calendar:

- `main`: the default writable calendar for `hrafn new-event`; its native events are mirrored to writable secondaries as `Busy`
- `source`: a read-only calendar whose native events are mirrored into the main calendar with full details; Hrafn never pushes `Busy` blocks back to it
- `secondary`: a writable calendar whose native events are mirrored into the main calendar with full details and which also receives `Busy` blocks from the main calendar

If a mirror run goes wrong, `hrafn cleanup-mirrors` removes every Hrafn-generated
mirror `.ics` file from the local stores so the next `hrafn sync` can propagate
the deletions upstream.

## Installation

From the repository:

```bash
cd sagas/hrafn
./install.sh
```

The installer creates the Hrafn virtualenv and attempts to install `khal` and
`vdirsyncer` through the distro package manager mapping already used by SagaOS.
