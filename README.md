# Video Rotation Saver

Fix video rotation metadata losslessly while watching in PotPlayer, triggered by a global hotkey. Press the rotation hotkey, the current file is closed, ffmpeg rewrites the display-rotation flag, and PotPlayer reopens at the same timestamp. Press the rename hotkey to rename the current video and reopen it in place.

**Status:** Phase 1 — hotkey + silent commit (no preview). Phase 2 will add the preview popup.

## How it works

1. A tiny Python daemon sits in your system tray.
2. It registers global hotkeys (defaults: **Ctrl+Alt+Numpad 2** for rotation, **Ctrl+Alt+Numpad 4** for rename) via the Windows `RegisterHotKey` API. This is independent of PotPlayer's own hotkey prefs.
3. On press, it talks to PotPlayer via WM_USER messages to:
   - read the current playback position (`0x5004`),
   - close the file and release the handle (`0x5009`).
4. It identifies the file via a two-stage lookup: PotPlayer's `RememberFiles` registry entry whose basename matches the window title, with a full handle-enumeration fallback (`NtQuerySystemInformation`).
5. ffmpeg rewrites rotation metadata **losslessly** (`-c copy`) using the modern `-display_rotation` option, into a temp file.
6. ffprobe verifies the new file has the expected rotation, then the tool atomically swaps it in and backs up the original as `.bak`.
7. PotPlayer is relaunched with `/current "path" /seek=<sec>` which reuses the open window at the saved position.

## Install from source

Prereqs:

- Windows 11 (or 10).
- Python 3.10+ available as `py -3` (installer default).
- FFmpeg + ffprobe on `PATH`. Quick option: `winget install Gyan.FFmpeg`.
- PotPlayer 64-bit.

Then from the project root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\install.ps1
```

The script creates a `.venv`, installs Python deps, and offers to put a Startup shortcut so the daemon runs at login.

## Build the Windows app and installer

From the project root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\build.ps1
```

The build creates:

- `dist\VideoRotationSaver.exe` from PyInstaller
- `dist\installer\VideoRotationSaver-Setup-1.0.0.exe` when Inno Setup 6 is installed
- generated icon and installer artwork in `build\assets`

Use `.\build.ps1 -SkipInstaller` to build only the app executable.

## Run

For a first run with a visible console (so you can see any errors):

```powershell
.\.venv\Scripts\python.exe -m potplayer_rotate daemon
```

Once verified, use the silent launcher — no console, shows up only as a tray icon:

```powershell
wscript .\run_silent.vbs
```

## Usage

1. Open a sideways/upside-down video in PotPlayer.
2. Press the configured rotation hotkey.
3. The file is closed, rotation metadata is rewritten 90° CW, PotPlayer reopens at the same timestamp. A tray toast confirms success.
4. Press the configured rename hotkey to enter a new filename for the current video. The app closes the file, renames it on disk, and reopens it in the same PotPlayer window.
5. To undo a rotation: delete the new file and rename the `.bak` back.

Each hotkey press advances rotation by **+90° CW**. Four presses returns to the starting orientation (with a few seconds of ffmpeg work each time, since we don't buffer intermediate state in Phase 1).

The rename prompt edits only the file name, not the extension. Video Rotation Saver preserves the original extension automatically.

## MKV files

MKV's ProjectionPoseRoll tag isn't honored by mainstream players (VLC/Chrome/WMP). When you rotate an MKV, Video Rotation Saver **remuxes it losslessly to MP4** (stream copy, no transcoding) and applies `-display_rotation`. The original MKV becomes a `.bak`, and PotPlayer reopens the new `.mp4`.

A one-time confirmation dialog appears the first MKV you rotate each session, so the extension change doesn't surprise you.

If the MKV's codecs aren't MP4-compatible (e.g. DTS audio, VP9 video), the remux is refused and the file is left untouched with a clear error.

## AVI / WMV / TS / FLV

Rejected with an error — these containers have no rotation metadata surface, and this tool won't silently re-encode.

## Config

Lives at `%APPDATA%\VideoRotationSaver\config.ini`. Auto-created on first run. See `config.sample.ini` for the full list of keys.

Common tweaks:

- `rotation_hotkey` and `rename_hotkey` under `[ui]` — e.g. `alt+F12`, `win+shift+J`. You can also use **Change hotkeys...** from the tray menu.
- `backup_behavior` under `[safety]` — `keep_until_next_run` (default) / `keep_forever` / `delete_immediately`.
- `potplayer_path` — override if auto-detect picks the wrong install.

## Tray menu

- **● Running** — status indicator. Shows the active rotate/rename hotkeys, or "⚠ Hotkey unavailable" if another app owns a combo.
- **Pause / Resume hotkey** — temporarily unregister and re-register the hotkey.
- **Change hotkeys...** — choose new rotate/rename hotkeys and claim them immediately.
- **Startup > Enable / Disable start with Windows** — mirrors the installer startup option and controls whether Video Rotation Saver runs when Windows starts.
- **Startup > Enable / Disable start when PotPlayer starts** — installs or removes a per-user watcher that runs at Windows sign-in and starts Video Rotation Saver when PotPlayer starts. Enabling it also starts the watcher immediately for the current session. The watcher only launches one tray instance even if multiple PotPlayer windows are opened.
- **View log** — opens `%LOCALAPPDATA%\VideoRotationSaver\log.txt`.
- **Exit** — shuts the daemon down.

When the last PotPlayer window closes, Video Rotation Saver asks whether to clear the log, then asks whether to close Video Rotation Saver too. Choosing yes to clear the log empties the current log and removes rotated log copies.

## Troubleshooting

**"⚠ Hotkey unavailable" on the tray icon.**
Another app already owns the combo (common culprits: Logitech/Razer utilities, browser extensions). Pick a different combo in `config.ini` and restart the daemon, or close the conflicting app and toggle Pause/Resume to retry.

**"Could not determine which file PotPlayer is playing."**
PotPlayer's "Remember file position" is probably off, and the handle-enum fallback also came up empty. Turn "Remember file position and settings" back on in PotPlayer (F5 → Playback → Resume on startup).

**"ffmpeg failed" with a red error dialog.**
Check `%LOCALAPPDATA%\VideoRotationSaver\log.txt` for the full command line and stderr. Most often this means a weird container edge case — file the log and try a different angle.

**Rotation goes the wrong way.**
Rare but possible, because the CW/CCW sign conventions are inverted between the legacy MP4 `rotate` tag and the modern display matrix. If you hit this, confirm with a clean test video, then open an issue with ffprobe output from before + after.

**Daemon does nothing on hotkey press.**
Open the log. Most likely: PotPlayer isn't the 64-bit window class (`PotPlayer64`) the tool looks for, or it's elevated and the daemon isn't (a non-elevated daemon can't `SendMessage` into an elevated window). Run both un-elevated.

## Known limitations (Phase 1)

- No preview. You pick blindly and check the result after PotPlayer reopens. Phase 2 fixes this.
- Hotkey always rotates +90° CW per press. No `--angle` picker from the hotkey. (You can run `python -m potplayer_rotate rotate --angle 180` from a terminal to hit a specific angle.)
- Network-mounted files work but are slow (ffmpeg has to re-copy stream bytes).

## File layout

```
video-rotation-saver/
├── potplayer_rotate/
│   ├── __init__.py
│   ├── __main__.py        # CLI entry: daemon | rotate
│   ├── config.py          # load/write %APPDATA%\VideoRotationSaver\config.ini
│   ├── paths.py
│   ├── logging_setup.py
│   ├── notify.py          # MessageBox + Windows toasts
│   ├── potplayer.py       # window find, IPC, path detection, launch/seek
│   ├── handle_enum.py     # NtQuerySystemInformation fallback
│   ├── rotate.py          # ffprobe/ffmpeg + safety + MKV remux
│   ├── hotkey.py          # RegisterHotKey listener thread
│   ├── tray.py            # pystray icon
│   └── daemon.py          # tray + hotkey orchestration
├── requirements.txt
├── config.sample.ini
├── run_silent.vbs         # launches daemon with no console
├── install.ps1
└── README.md
```

## Manual test matrix

- [ ] MP4 rotated 90° CW — plays correctly in VLC after fix
- [ ] MP4 rotated 180° — plays correctly in Windows Media Player after fix
- [ ] MKV rotated 90° CCW — remuxed to .mp4, plays correctly in VLC and Chrome
- [ ] File with spaces/unicode in path
- [ ] File on a network drive (should still work or fail gracefully)
- [ ] AVI file (should fail with clear error, not crash or silently re-encode)
- [ ] PotPlayer not running when hotkey is pressed (should fail gracefully)
- [ ] PotPlayer running but nothing loaded (should fail gracefully)
