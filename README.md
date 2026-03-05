# Saga

Saga is a modular, user-space Hyprland desktop layer for Linux.

It provides:
- a complete Hyprland UI stack
- optional modules ("sagas"), starting with `njal`
- centralized shortcuts and theming
- event-driven UI updates via the Saga daemon socket

## Highlights

- User-space install only (`~/.config`, `~/.local/bin`, `~/.local/share`)
- No writes to `/etc` or `/usr`
- Symlink-based config management for clean removal
- Distro-aware dependency installation (Arch/Fedora/Debian/Ubuntu)
- Conflict-safe shortcut layer (`SUPER+ALT+...`)
- Event-bus-driven UI state (no high-frequency polling loops)

## Supported distros

- Arch (`pacman`)
- Fedora (`dnf`)
- Debian (`apt`)
- Ubuntu (`apt`, uses Debian package list)

## Architecture

Saga consists of:

1. `saga`
The main user CLI (`install`, `update`, `doctor`, `theme`, `query`, `subscribe`, etc.)

2. Desktop configs under `desktop/`
Hyprland, Waybar, EWW, SwayNC, Wofi

3. Theme engine under `themes/`
`theme.json` + generated variables/styles

4. Optional modules under `sagas/`
Current module: `njal` (Zsh environment)

5. Event bus integration
UI clients consume Saga daemon data from socket:
`~/.local/share/saga/saga.sock`

## What Saga installs

### User-space paths

- `~/.local/bin/saga` (symlink to repo CLI)
- `~/.local/share/saga/state.json`
- symlinked configs:
  - `~/.config/hypr -> <repo>/desktop/hypr`
  - `~/.config/waybar -> <repo>/desktop/waybar`
  - `~/.config/swaync -> <repo>/desktop/swaync`
  - `~/.config/eww -> <repo>/desktop/eww`
  - `~/.config/wofi -> <repo>/desktop/wofi`

### Package groups

Saga installs missing packages (via distro package manager) from `packages/*.txt`.

Core desktop:
- hyprland
- waybar
- wofi
- swaynotificationcenter
- networkmanager
- pipewire
- wireplumber
- kitty
- hyprlock
- hypridle
- mpvpaper
- eww
- xdg-desktop-portal-hyprland
- bluez
- brightnessctl
- inotify-tools

CLI utilities:
- python3
- git
- curl
- wget
- ripgrep
- fd
- bat
- eza
- fzf
- jq

Optional `njal` module packages:
- zsh
- starship
- zoxide
- btop
- lazygit
- tmux

## Quick start

### 1. Clone and run install

```bash
chmod +x saga scripts/*.sh sagas/njal/*.sh
./cli/saga install
```

Install with Njál module:

```bash
./cli/saga install --njal
```

During install, Saga:
- detects distro
- installs missing dependencies (with `sudo` package-manager calls)
- copies `desktop/hypr/hyprland.conf` to `~/.config/hypr/hyprland.conf`
- reloads Hyprland (if available)

## Command reference

| Command | What it does |
|---|---|
| `saga install` | Installs the Saga desktop layer and dependencies. |
| `saga doctor` | Checks required binaries and Hyprland config state. |
| `saga uninstall` | Removes Saga-managed files and Saga-installed package set. |

## Shortcuts and keybinds

Configured directly in:
- `desktop/hypr/hyprland.conf`

Current default bindings:
- `SUPER + T` terminal
- `SUPER + SPACE` launcher
- `SUPER + ALT + C` control center
- `SUPER + ALT + N` notifications
- `SUPER + ALT + W` wifi menu
- `SUPER + ALT + B` bluetooth menu
- `SUPER + ALT + V` audio
- `SUPER + ALT + L` brightness
- `SUPER + ALT + I` system stats
- `SUPER + ALT + P` power menu
- `SUPER + ALT + F` file search

After editing `desktop/hypr/hyprland.conf`:

```bash
hyprctl reload
```

## Theming

Default theme:
- `themes/saga-cyberpunk/theme.json`

Apply:

```bash
saga theme apply saga-cyberpunk
```

Theme apply regenerates:
- `themes/<theme>/variables.css`
- `themes/<theme>/variables.scss`
- `desktop/waybar/style.css`
- `desktop/eww/eww.scss`
- `desktop/wofi/style.css`
- `desktop/hypr/theme.conf`

Then it reloads UI components where available.

## Event bus model (important)

Saga UI is wired to use Saga daemon events/state as source of truth.

Socket path:
- `~/.local/share/saga/saga.sock`

Protocol (newline-delimited JSON):
- query request: `{"command":"query","metric":"cpu"}`
- subscribe request: `{"command":"subscribe","event":"cpu_update"}`

Examples:
- response: `{"cpu":13}`
- event: `{"event":"cpu_update","value":14}`

If the socket is missing, commands fail fast with a clear error.

## Uninstall

### Remove module only

```bash
saga remove njal
```

### Remove Saga desktop configs/state

```bash
./scripts/uninstall-desktop.sh
```

This removes Saga-managed symlinks/config/state only.
It does **not** uninstall system packages.

## Health checks and troubleshooting

Run:

```bash
saga doctor
```

Doctor checks:
- Hyprland command availability
- `kitty`, `mpvpaper`, `waybar` command availability
- `~/.config/hypr/hyprland.conf` exists
- terminal bind exists in Hypr config
- Fedora `disable_hyprland_qtutils_check = true` is present

## Video wallpaper (mpvpaper)

Saga now uses `mpvpaper` for wallpaper so MP4 playback is supported.
On Fedora, `mpvpaper` may come from a COPR repo depending on your setup.

Default video path:
- `~/.local/share/saga/wallpapers/default.mp4`

Hyprland startup command in `desktop/hypr/hyprland.conf`:
- kills old `mpvpaper` instance
- starts `mpvpaper` loop with no audio if the file exists

Quick setup:

```bash
mkdir -p ~/.local/share/saga/wallpapers
cp /path/to/your/video.mp4 ~/.local/share/saga/wallpapers/default.mp4
./cli/saga install
hyprctl reload
```

Common issues:

1. `error: saga socket not found`
Start `sagad` so `~/.local/share/saga/saga.sock` exists.

2. Waybar/EWW not updating live
Confirm event listeners are running (`saga eww-stream`, `saga ui-listen`) and daemon publishes events.

3. Keybind changes not applied
Run `./cli/saga install` again, then `hyprctl reload`.

4. Package name mismatch on distro
Edit the relevant `packages/<distro>.txt` entry and re-run install.

## Development workflow

Theme iteration:

```bash
./cli/saga doctor
```

Use this to confirm package/bin/config health.

Manual validation:

```bash
bash -n cli/saga
```

## Repository map

- `cli/` CLI entrypoint
- `config/` centralized static config (shortcuts)
- `desktop/` Hyprland/Waybar/EWW/SwayNC/Wofi config
- `sagas/` optional module installs
- `scripts/` installers, generators, stream helpers
- `themes/` theme definitions and style templates
- `packages/` distro package maps
- `state/` local project state placeholder

---

Saga is intended to be safe to run repeatedly: install/update/generate operations are idempotent and keep changes scoped to user-space.
