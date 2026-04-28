# Video Rotation Saver 1.0.0

Initial public release.

## Highlights

- Windows tray utility for PotPlayer.
- Lossless video rotation metadata updates via FFmpeg stream copy.
- Rename current video from a hotkey while preserving the original extension.
- Reopens the current video in PotPlayer at the previous timestamp.
- Installer setup pages for PotPlayer path and hotkeys.
- Tray menu for hotkey reassignment, startup options, log viewing, pause/resume, and exit.
- Startup submenu supports starting with Windows or starting when PotPlayer starts.
- Branded tray icon, splash screen, installer art, and clean app identity.

## Release Assets

Attach these files from `release-assets` to the GitHub release:

- `VideoRotationSaver-Setup-1.0.0.exe` - recommended installer.
- `VideoRotationSaver.exe` - standalone portable executable.
- `SHA256SUMS.txt` - checksums for verification.

## Notes

FFmpeg and ffprobe must be available on `PATH`; the quickest install path is usually:

```powershell
winget install Gyan.FFmpeg
```

PotPlayer must be installed separately.

