# Telecaster

Annotate someone's shared screen, live, right on their own window.

When someone shares a window — a game, a design, a spreadsheet, a slide deck —
in any screen-sharing or streaming app, the people watching can turn on
**draw mode** and sketch right over what they see: an arrow, a circle, "click
here". The marks show up as an overlay on the sharer's actual window, in the
exact same spot, even when the sharer runs at 1440p and the viewer watches a
720p feed in a small tile.

- **Transparent and click-through.** The sharer's overlay sits on top of
  their window but never takes focus or blocks clicks, so it doesn't get in
  the way of whatever they're doing.
- **Locks onto a window.** The sharer picks the window they're sharing;
  viewers pick the window they're watching it in. Each overlay follows its
  window as it moves and resizes, matching its client area exactly.
- **Resolution independent.** Marks are sent in normalized coordinates, so
  they scale between any sharer/viewer resolutions and DPI settings.
- **Handles letterboxing.** Viewers' overlays fit the shared window's aspect
  ratio inside their view, and they can mark the exact shared area once to
  cut away the viewing app's own buttons and panels.
- **Fading ink and pings.** Marks fade after a few seconds (configurable, or
  never); right-click for a "look here" ripple.
- **One link to join.** The sharer starts a relay and copies an invite link;
  viewers paste it and connect.
- **Self-hostable.** The relay is a tiny server that can run inside the app
  or on any machine.

> Status: beta. The app runs on Windows 10 (2004+) / 11 and macOS 11+. The
> relay server runs anywhere Python does.

## How it works

```
 Viewer PC                          Relay                     Sharer PC
┌──────────────────────┐                             ┌──────────────────────┐
│ Window showing the   │                             │ Shared window        │
│ shared screen        │   marks in [0,1] coords     │                      │
│ ┌──────────────────┐ │                             │ ┌──────────────────┐ │
│ │ Telecaster       │─┼──────► ws://… room ABC123 ──┼─► Telecaster       │ │
│ │ overlay (draw)   │◄┼──────                ───────┼─┤ overlay (click-  │ │
│ └──────────────────┘ │   shared window size ◄──────┼─┤ through)         │ │
└──────────────────────┘                             │ └──────────────────┘ │
                                                     └──────────────────────┘
```

Screen-sharing apps don't let third-party tools send data alongside the
video, so Telecaster runs its own tiny side channel. Every client covers its
chosen window with a borderless, transparent, always-on-top overlay. Mouse
positions are divided by the overlay's size before they are sent, and
multiplied back out by each receiver's overlay size. The sharer also tells the
room their window's aspect ratio, which viewers use to find the shared image
inside a letterboxed view.

Everyone in the room sees everyone's marks, including on their own overlay,
so viewers see what others drew without waiting for the video to catch up.
By default the sharer's overlay is excluded from screen capture, so marks
don't appear twice (once live, once delayed in the shared video).

## Install

Download the latest build from the
[Releases page](https://github.com/FrankTreadwell/telecaster/releases):

- **Windows 10/11** — `Telecaster-…-windows-x64.exe`. No installer; just run
  it. The app isn't code-signed yet, so SmartScreen may say "Windows
  protected your PC": click **More info → Run anyway**.
- **macOS 11+, Apple Silicon** — `Telecaster-…-macos-arm64.zip`. Unzip and
  drag **Telecaster** into Applications. It isn't notarized yet, so the
  first time you open it macOS will refuse; go to **System Settings →
  Privacy & Security** and click **Open Anyway**.

  macOS will also ask for two optional permissions:
  - **Accessibility**, so the hotkeys work while another app is in front.
  - **Screen Recording**, only so the window picker can show window titles
    (otherwise windows are listed by app name). Telecaster never records.

**From source** (any Python 3.10+, including Intel Macs):

```bash
pip install git+https://github.com/FrankTreadwell/telecaster
telecaster
```

## Quick start

**The person sharing**

1. Choose **Sharing my screen (host)**.
2. Keep **Run the relay on this PC** ticked, click **New** for a room code,
   and **Start relay**.
3. Under **Window to draw on**, choose the window you're sharing. For games,
   use borderless or windowed mode — no overlay can draw over exclusive
   fullscreen.
4. Pick who's watching (**Viewers on the internet** or **on my home
   network**), click **Copy invite link**, and send it to your viewers.
   For internet viewers, your router must forward the relay's TCP port
   (8765 by default) to your PC — see [Hosting the relay](#hosting-the-relay).

**Viewers**

1. Copy the invite link, open Telecaster, and choose **Viewing a shared
   screen**. The link is filled in from your clipboard automatically (or
   paste it into **Invite link**). Press **Connect**.
2. Choose the window that shows the shared screen. Viewing it full screen
   (or popped out) gives the most accurate marks.
3. If the viewing app's buttons or panels surround the shared image, click
   **Mark shared area…** and drag a box around the image once. It's
   remembered as pixel margins, so resizing the window keeps working.
4. Press **Ctrl+Alt+D** to toggle draw mode, draw with the left mouse button,
   right-click to ping. Press **Ctrl+Alt+D** again to give clicks back to the
   window underneath.

| Windows      | macOS | Action                                  |
|--------------|-------|-----------------------------------------|
| Ctrl+Alt+D   | ⌃⌥D   | Toggle draw mode                        |
| Ctrl+Alt+C   | ⌃⌥C   | Clear your own lines                    |
| Ctrl+Alt+X   | ⌃⌥X   | Clear everyone's lines (sharer only)    |

## Hosting the relay

Invite links (`telecaster://join?relay=…&room=…`) carry the relay address and
room code, so viewers never type either. Viewers still have to be able to
reach that address. Options, easiest first:

- **Same network**: choose "Viewers on my home network" before copying the link.
- **Port forwarding**: forward TCP 8765 on the sharer's router to their PC.
  "Viewers on the internet" links use your public IP, which the app looks up
  from [ipify](https://www.ipify.org) when you copy the link.
- **A VPN mesh** such as Tailscale or ZeroTier: viewers tick "Enter relay
  address and room code manually" and use `ws://<sharer's VPN IP>:8765`.
- **A small server**: the relay only needs `websockets`:

  ```bash
  pip install websockets
  python -m telecaster.relay --port 8765
  ```

  or with Docker: `docker build -t telecaster-relay . && docker run -p 8765:8765 telecaster-relay`.
  Put it behind a TLS reverse proxy (Caddy, nginx) to use `wss://`.

The room code is the only access control, so use the generated random codes.
The relay forwards only validated drawing messages, rate-limits each client,
and keeps nothing except the host's window size.

## Protocol

JSON over WebSocket; documented at the top of
[`src/telecaster/protocol.py`](src/telecaster/protocol.py). Coordinates are
`[x, y]` in `[0, 1]` relative to the shared area; pen widths are a
fraction of the area's height. Writing a client in another language (say, a
macOS or Linux overlay) only requires speaking this protocol.

## Development

```bash
pip install -e .[dev]
pytest
python packaging/platform_smoke.py      # needs a desktop session
```

Building the app locally (output in `dist/`):

```bash
pip install -e .[build]
pyinstaller packaging/telecaster.spec --noconfirm
```

### Releasing

Bump the version in `pyproject.toml` and `src/telecaster/__init__.py`,
commit, then tag and push, e.g. `git tag v0.2.0b1 && git push origin v0.2.0b1`.
GitHub Actions tests and builds on Windows and macOS and publishes a release
with both downloads (marked pre-release for `a`/`b`/`rc` versions).

Layout:

| Module         | Role                                                         |
|----------------|--------------------------------------------------------------|
| `protocol.py`  | Message schema and validation shared by client and relay     |
| `invite.py`    | Invite link format (relay address + room in one string)     |
| `relay.py`     | asyncio WebSocket relay (standalone or in a background thread) |
| `geometry.py`  | Aspect fitting, insets, normalization (pure, tested)         |
| `strokes.py`   | Stroke/ping model with fading (pure, tested)                 |
| `platforms/`   | Per-OS window list, tracking, click-through, hotkeys (`windows.py`, `macos.py`) |
| `overlay.py`   | The transparent Qt overlay window: input and painting        |
| `net.py`       | QWebSocket relay client with auto-reconnect                  |
| `app.py`       | Control window that ties it together                         |

### Known limitations / ideas

- Exclusive-fullscreen games can't be overlaid on Windows; use borderless.
- On macOS the overlay covers the whole window including its title bar,
  since that's what screen-sharing apps capture there.
- Screen-sharing video lags a little, so marks arrive slightly *before* the
  viewer's video shows the moment they drew on. Usually that's what you want.
- Linux support would need a `platforms/linux.py` (see `platforms/base.py`).
- Builds aren't code-signed or notarized yet.
- Ideas: arrows and shapes, undo, a per-viewer mute for the sharer, a
  hosted public relay.

## License

MIT — see [LICENSE](LICENSE).
