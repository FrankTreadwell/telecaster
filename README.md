# Telecaster

Draw on a streamer's screen from the Discord stream you're watching.

When someone streams their game in Discord, everyone watching can turn on
**draw mode** and sketch right over the stream: "go here", "enemy behind that
wall", a circle around the loot. The lines show up on the streamer's own
screen, on top of the game, in the exact same spot — even though the
streamer plays at 1440p and you watch a 720p stream in a small Discord tile.

- **Transparent and click-through.** The overlay sits on top of the game but
  never takes focus or blocks clicks, so it doesn't get in the way of play.
- **Locks onto a window.** Pick the game (streamer) or the Discord window
  (viewers); the overlay follows it as it moves and resizes, matching its
  client area exactly.
- **Resolution independent.** Lines are sent in normalized coordinates, so
  they scale between any source/viewer resolutions and DPI settings.
- **Handles letterboxing.** Viewers' overlays fit the streamer's aspect ratio
  inside the stream view, and you can mark the exact video area once to cut
  away Discord's UI.
- **Fading ink and pings.** Lines fade after a few seconds (configurable, or
  never); right-click for a "look here" ripple.
- **Self-hostable.** A tiny relay server connects everyone in a room. The
  streamer can run it from the app with one checkbox.

> Status: early alpha. Windows 10 (2004+) / 11 only for the overlay app. The
> relay server runs anywhere Python does.

## How it works

```
 Viewer PC                          Relay                     Streamer PC
┌──────────────────────┐                             ┌──────────────────────┐
│ Discord stream view  │                             │ Game window          │
│ ┌──────────────────┐ │   strokes in [0,1] coords   │ ┌──────────────────┐ │
│ │ Telecaster       │─┼──────► ws://… room ABC123 ──┼─► Telecaster       │ │
│ │ overlay (draw)   │◄┼──────                ───────┼─┤ overlay (click-  │ │
│ └──────────────────┘ │   host's window size ◄──────┼─┤ through)         │ │
└──────────────────────┘                             │ └──────────────────┘ │
                                                     └──────────────────────┘
```

Discord has no API for sending data alongside a stream, so Telecaster runs
its own tiny side channel. Every client covers its chosen window with a
borderless, transparent, always-on-top overlay. Mouse positions are divided by
the overlay's size before they are sent, and multiplied back out by each
receiver's overlay size. The host also tells the room its window's aspect
ratio, which viewers use to find the video inside Discord's letterboxed view.

Everyone in the room sees everyone's lines, including on their own overlay,
so viewers see what others drew without waiting for the stream to catch up.
By default the streamer's overlay is excluded from screen capture, so lines
don't appear twice (once live, once delayed in the stream).

## Install

Requires Python 3.10+ on Windows.

```bash
pip install git+https://github.com/<you>/telecaster
```

Or from a checkout:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Then run `telecaster` (or `python -m telecaster`).

## Quick start

**Streamer**

1. Choose **Streaming (host)**.
2. Keep **Run the relay on this PC** ticked, click **New** for a room code,
   and **Connect**.
3. Under **Window to draw on**, choose your game. Play in borderless or
   windowed mode — no overlay can draw over exclusive fullscreen.
4. Pick who's watching (**Viewers on the internet** or **on my home
   network**), click **Copy invite link**, and paste it in the Discord chat.
   For internet viewers, your router must forward the relay's TCP port
   (8765 by default) to your PC — see [Hosting the relay](#hosting-the-relay).

**Viewers**

1. Copy the streamer's invite link, open Telecaster, and choose **Watching
   (viewer)**. The link is filled in from your clipboard automatically (or
   paste it into **Invite link**). Press **Connect**.
2. Choose the Discord window that shows the stream. Full-screening the stream
   in Discord (or popping it out) gives the most accurate lines.
3. If Discord's UI surrounds the video, click **Mark video area…** and drag a
   box around the video once. It's remembered as pixel margins, so resizing
   the Discord window keeps working.
4. Press **Ctrl+Alt+D** to toggle draw mode, draw with the left mouse button,
   right-click to ping. Press **Ctrl+Alt+D** again to give clicks back to Discord.

| Hotkey       | Action                                  |
|--------------|-----------------------------------------|
| Ctrl+Alt+D   | Toggle draw mode                        |
| Ctrl+Alt+C   | Clear your own lines                    |
| Ctrl+Alt+X   | Clear everyone's lines (streamer only)  |

## Hosting the relay

Invite links (`telecaster://join?relay=…&room=…`) carry the relay address and
room code, so viewers never type either. Viewers still have to be able to
reach that address. Options, easiest first:

- **Same network**: choose "Viewers on my home network" before copying the link.
- **Port forwarding**: forward TCP 8765 on the streamer's router to their PC.
  "Viewers on the internet" links use your public IP, which the app looks up
  from [ipify](https://www.ipify.org) when you copy the link.
- **A VPN mesh** such as Tailscale or ZeroTier: viewers tick "Enter relay
  address and room code manually" and use `ws://<streamer's VPN IP>:8765`.
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
`[x, y]` in `[0, 1]` relative to the shared video area; pen widths are a
fraction of the area's height. Writing a client in another language (say, a
macOS or Linux overlay) only requires speaking this protocol.

## Development

```bash
pip install -e .[dev]
pytest
```

Layout:

| Module         | Role                                                         |
|----------------|--------------------------------------------------------------|
| `protocol.py`  | Message schema and validation shared by client and relay     |
| `invite.py`    | Invite link format (relay address + room in one string)     |
| `relay.py`     | asyncio WebSocket relay (standalone or in a background thread) |
| `geometry.py`  | Aspect fitting, insets, normalization (pure, tested)         |
| `strokes.py`   | Stroke/ping model with fading (pure, tested)                 |
| `winapi.py`    | Win32: window list, client rects, click-through, hotkeys     |
| `overlay.py`   | The transparent Qt overlay window: input and painting        |
| `net.py`       | QWebSocket relay client with auto-reconnect                  |
| `app.py`       | Control window that ties it together                         |

### Known limitations / ideas

- Exclusive-fullscreen games can't be overlaid (a Windows limitation).
- Discord's stream delay means lines arrive slightly *before* the viewer's
  video shows the moment they drew on. Usually that's what you want.
- macOS/Linux overlay support would need a `winapi.py` equivalent.
- Ideas: arrows and shapes, undo, a per-viewer mute for the streamer, a
  hosted public relay.

## License

MIT — see [LICENSE](LICENSE).
