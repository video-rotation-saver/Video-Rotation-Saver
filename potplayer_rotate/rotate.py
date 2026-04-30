"""Rotation engine: ffprobe, ffmpeg display_rotation, MKV remux, file safety.

Internal angle convention: `rotation_cw` = *clockwise picture rotation* the
user wants the file to display at (0 / 90 / 180 / 270). ffmpeg's
``-display_rotation`` uses counter-clockwise degrees, so we convert with
``ffmpeg_arg = (360 - rotation_cw) % 360``.

The rotation engine never talks to PotPlayer directly — callers (daemon
or the CLI `one_shot_rotate`) handle close/reopen around it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .app_info import APP_NAME
from .config import Config, load_config
from .logging_setup import get_logger
from .notify import confirm_yes_no, error as notify_error, toast
from . import potplayer as pp

log = get_logger()

_CREATE_NO_WINDOW = 0x08000000

_MP4_LIKE_EXTS = {".mp4", ".m4v", ".mov"}
_MKV_EXTS = {".mkv", ".webm"}
_UNSUPPORTED_EXTS = {".avi", ".wmv", ".mpg", ".mpeg", ".flv", ".ts", ".m2ts", ".mts", ".3gp"}

_MP4_OK_VIDEO = {"h264", "hevc", "mpeg4", "av1"}
_MP4_OK_AUDIO = {"aac", "mp3", "ac3", "eac3", "opus"}


@dataclass
class SessionState:
    """Flags that persist across hotkey presses within one daemon lifetime."""
    mkv_remux_confirmed: bool = False
    playlist_note_shown: bool = False


_session_state = SessionState()


def session_state() -> SessionState:
    return _session_state


# --- ffprobe ----------------------------------------------------------------

def _run(cmd: list[str], *, capture: bool = True, timeout: float = 600.0) -> subprocess.CompletedProcess:
    log.info("exec: %s", cmd)
    return subprocess.run(
        cmd,
        check=False,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


@dataclass
class StreamInfo:
    video_codec: str | None
    audio_codec: str | None
    current_rotation_ccw: float
    width: int | None
    height: int | None


def probe(path: Path, cfg: Config) -> StreamInfo:
    cmd = [
        cfg.ffprobe_path, "-hide_banner", "-loglevel", "error",
        "-print_format", "json",
        "-show_streams",
        str(path),
    ]
    cp = _run(cmd, timeout=30)
    if cp.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {cp.stderr.strip() or cp.stdout.strip()}")
    data = json.loads(cp.stdout or "{}")
    streams = data.get("streams", [])

    vcodec = acodec = None
    rotation = 0.0
    width = height = None

    for s in streams:
        ctype = s.get("codec_type")
        if ctype == "video" and vcodec is None:
            vcodec = s.get("codec_name")
            width = s.get("width")
            height = s.get("height")
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    try:
                        rotation = float(sd["rotation"])
                        break
                    except (TypeError, ValueError):
                        pass
            tags = s.get("tags") or {}
            if not rotation and "rotate" in tags:
                try:
                    rotation = -float(tags["rotate"]) % 360.0
                except ValueError:
                    pass
        elif ctype == "audio" and acodec is None:
            acodec = s.get("codec_name")

    return StreamInfo(vcodec, acodec, rotation, width, height)


# --- Angle helpers ----------------------------------------------------------

def ccw_to_cw(ccw_deg: float) -> int:
    v = ccw_deg % 360.0
    cw = (360.0 - v) % 360.0
    return int(round(cw / 90.0)) * 90 % 360



# --- Outcome ----------------------------------------------------------------

@dataclass
class RotateResult:
    ok: bool
    new_path: Path | None
    previous_rotation_cw: int
    applied_rotation_cw: int
    message: str


# --- Safety helpers ---------------------------------------------------------

def _unique_backup(original: Path) -> Path:
    cand = original.with_suffix(original.suffix + ".bak")
    if not cand.exists():
        return cand
    n = 2
    while True:
        cand = original.with_suffix(f"{original.suffix}.bak{n}")
        if not cand.exists():
            return cand
        n += 1


def _apply_backup_policy(backup: Path, cfg: Config) -> None:
    if cfg.backup_behavior == "delete_immediately":
        try:
            backup.unlink(missing_ok=True)
        except OSError as e:
            log.warning("couldn't delete backup %s: %s", backup, e)


def _verify_output(out_path: Path, cfg: Config) -> tuple[bool, str]:
    if not out_path.exists():
        return False, "output file missing"
    if out_path.stat().st_size == 0:
        return False, "output file is empty"
    try:
        probe(out_path, cfg)
    except Exception as e:
        return False, f"ffprobe on output failed: {e}"
    return True, ""


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        log.warning("couldn't remove %s: %s", p, e)


# --- ffmpeg commands --------------------------------------------------------

# PotPlayer ignores both the TKHD Display Matrix and H.264/HEVC SEI
# display_orientation NALs. Physical pixel rotation is the only path.
# We use -noautorotate so the input TKHD doesn't compound the rotation,
# then transpose the pixels by the incremental delta. CRF 18 is visually
# lossless at normal phone-video bitrates.

_TRANSPOSE_FOR_CW = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}


def _ffmpeg_mp4_transcode_rotate(src: Path, dst: Path, delta_cw: int, cfg: Config) -> subprocess.CompletedProcess:
    vf = _TRANSPOSE_FOR_CW[delta_cw % 360]
    cmd = [
        cfg.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
        "-noautorotate",
        "-i", str(src),
        "-map", "0:v?",
        "-map", "0:a?",
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    return _run(cmd)


def _ffmpeg_mkv_transcode_rotate(src: Path, dst_mp4: Path, delta_cw: int, cfg: Config) -> subprocess.CompletedProcess:
    vf = _TRANSPOSE_FOR_CW[delta_cw % 360]
    cmd = [
        cfg.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
        "-noautorotate",
        "-i", str(src),
        "-map", "0:v?",
        "-map", "0:a?",
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst_mp4),
    ]
    return _run(cmd)


# --- Core API ---------------------------------------------------------------

def apply_rotation(
    src: Path,
    target_cw: int,
    cfg: Config | None = None,
    state: SessionState | None = None,
) -> RotateResult:
    cfg = cfg or load_config()
    state = state or _session_state

    src = src.resolve()
    if not src.is_file():
        return RotateResult(False, None, 0, target_cw, f"source not found: {src}")

    ext = src.suffix.lower()
    if ext in _UNSUPPORTED_EXTS:
        msg = (f"{ext} container doesn't support rotation metadata. "
               "Re-encoding would be required and is not supported by this tool.")
        return RotateResult(False, None, 0, target_cw, msg)

    info = probe(src, cfg)
    prev_cw = ccw_to_cw(info.current_rotation_ccw)
    if prev_cw == (target_cw % 360):
        return RotateResult(True, src, prev_cw, target_cw, "Rotation unchanged.")

    if ext in _MP4_LIKE_EXTS:
        return _rotate_mp4_like(src, target_cw, prev_cw, cfg)
    if ext in _MKV_EXTS:
        return _rotate_mkv_via_remux(src, target_cw, prev_cw, cfg, info, state)

    log.info("unknown container %s, trying MP4-style rotation", ext)
    return _rotate_mp4_like(src, target_cw, prev_cw, cfg)


def _rotate_mp4_like(src: Path, target_cw: int, prev_cw: int, cfg: Config) -> RotateResult:
    tmp = src.with_name(src.stem + ".rotating.tmp" + src.suffix)
    delta_cw = (target_cw - prev_cw) % 360
    try:
        cp = _ffmpeg_mp4_transcode_rotate(src, tmp, delta_cw, cfg)
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "").strip()
            log.error("ffmpeg failed: %s", err)
            _safe_unlink(tmp)
            return RotateResult(False, None, prev_cw, target_cw, f"ffmpeg failed: {err[:500]}")
        ok, why = _verify_output(tmp, cfg)
        if not ok:
            log.error("output verification failed: %s", why)
            _safe_unlink(tmp)
            return RotateResult(False, None, prev_cw, target_cw, why)
        new_path = _swap_in(src, tmp, cfg)
        return RotateResult(True, new_path, prev_cw, target_cw, "Rotation applied.")
    except Exception as e:
        log.exception("mp4 rotation unexpected failure")
        _safe_unlink(tmp)
        return RotateResult(False, None, prev_cw, target_cw, f"unexpected error: {e}")


def _rotate_mkv_via_remux(
    src: Path, target_cw: int, prev_cw: int, cfg: Config,
    info: "StreamInfo", state: SessionState,
) -> RotateResult:
    acodec = (info.audio_codec or "").lower() if info.audio_codec else ""
    if acodec and acodec not in _MP4_OK_AUDIO:
        msg = (f"This MKV's audio codec ({acodec}) isn't supported in MP4. "
               "No rotation path; leaving file untouched.")
        return RotateResult(False, None, prev_cw, target_cw, msg)

    if not state.mkv_remux_confirmed:
        ok = confirm_yes_no(
            f"This is an MKV file. To apply rotation, {APP_NAME}\n"
            "will re-encode it into a new .mp4 file next to the original.\n\n"
            "The .mkv will be kept as a .bak backup.\n"
            "This asks once per session; subsequent MKVs will convert without prompting.\n\n"
            "Proceed?",
            title=f"{APP_NAME} — MKV -> MP4",
        )
        if not ok:
            return RotateResult(False, None, prev_cw, target_cw, "Cancelled (MKV conversion not confirmed).")
        state.mkv_remux_confirmed = True

    dst_mp4 = src.with_suffix(".mp4")
    if dst_mp4.exists() and dst_mp4.resolve() != src.resolve():
        return RotateResult(
            False, None, prev_cw, target_cw,
            f"Refusing to overwrite existing file: {dst_mp4.name}",
        )

    delta_cw = (target_cw - prev_cw) % 360
    tmp = src.with_name(src.stem + ".rotating.tmp.mp4")
    try:
        cp = _ffmpeg_mkv_transcode_rotate(src, tmp, delta_cw, cfg)
        if cp.returncode != 0:
            err = (cp.stderr or cp.stdout or "").strip()
            log.error("mkv transcode failed: %s", err)
            _safe_unlink(tmp)
            return RotateResult(False, None, prev_cw, target_cw, f"ffmpeg failed: {err[:500]}")
        ok, why = _verify_output(tmp, cfg)
        if not ok:
            _safe_unlink(tmp)
            return RotateResult(False, None, prev_cw, target_cw, why)

        bak = _unique_backup(src)
        os.replace(src, bak)
        os.replace(tmp, dst_mp4)
        _apply_backup_policy(bak, cfg)
        return RotateResult(True, dst_mp4, prev_cw, target_cw, "Converted to .mp4 with rotation.")
    except Exception as e:
        log.exception("mkv transcode unexpected failure")
        _safe_unlink(tmp)
        return RotateResult(False, None, prev_cw, target_cw, f"unexpected error: {e}")


def _swap_in(original: Path, new_tmp: Path, cfg: Config) -> Path:
    bak = _unique_backup(original)
    os.replace(original, bak)
    try:
        os.replace(new_tmp, original)
    except Exception:
        try:
            os.replace(bak, original)
        except Exception:
            log.error("CRITICAL: couldn't restore backup after failed swap. "
                      "Original is at %s, tmp at %s", bak, new_tmp)
        raise
    _apply_backup_policy(bak, cfg)
    return original


# --- Orchestrated flow: anchor-in, toast-out --------------------------------

def run_rotation_flow(
    anchor: pp.PotPlayerAnchor,
    *,
    delta_cw: int | None = None,
    absolute_cw: int | None = None,
) -> RotateResult:
    """Complete anchor-scoped rotation:
      1. Snapshot position + file via anchor's HWND
      2. Close file via IPC on that HWND
      3. Rotate / remux
      4. If anchor HWND is still alive, WM_DROPFILES the new file to it
         and seek to the prior position
      5. Return the result. Success/error toasting is the caller's job.
    """
    cfg = load_config()

    state = pp.snapshot_state(anchor)
    if state.play_status is None or state.play_status == -1 or not state.has_file:
        return RotateResult(False, None, 0, 0,
                            "PotPlayer has focus but nothing is loaded, or its "
                            "file couldn't be resolved. Ensure 'Remember file "
                            "position' is enabled in PotPlayer.")

    assert state.file_path is not None
    current_cw = ccw_to_cw(probe(state.file_path, cfg).current_rotation_ccw)
    if delta_cw is not None:
        target_cw = (current_cw + delta_cw) % 360
    elif absolute_cw is not None:
        target_cw = int(absolute_cw) % 360
    else:
        target_cw = current_cw

    if target_cw == current_cw:
        return RotateResult(True, state.file_path, current_cw, target_cw,
                            "Rotation unchanged.")

    resume_s = max(0.0, (state.position_ms or 0) / 1000.0)
    log.info("flow start hwnd=%d pid=%d file=%s cur=%d target=%d resume=%.2fs",
             anchor.hwnd, anchor.pid, state.file_path, current_cw, target_cw, resume_s)

    # Step 1: close the file on THIS HWND.
    pp.close_current_file(anchor.hwnd)
    if not pp.wait_until_file_released(state.file_path, timeout_s=5.0):
        # Don't give up entirely — try the rotation anyway; if ffmpeg fails
        # due to a lingering lock we'll report it.
        log.warning("file still locked after 5s; proceeding")

    # Step 2: rotate.
    result = apply_rotation(state.file_path, target_cw, cfg, _session_state)
    log.info("rotate ok=%s msg=%s new=%s", result.ok, result.message, result.new_path)

    # Step 3: reopen on the SAME HWND (if still alive).
    target_path = result.new_path or state.file_path
    alive = anchor.is_alive()
    log.info("reopen: anchor hwnd=%d alive=%s target=%s", anchor.hwnd, alive, target_path)
    if not alive:
        log.info("reopen: anchor hwnd=%d no longer exists — skipping", anchor.hwnd)
        return result

    dropped = pp.post_file_drop(anchor.hwnd, target_path)
    log.info("reopen: WM_DROPFILES posted -> %s", dropped)
    if not dropped:
        log.warning("reopen: falling back to CLI /current launch")
        pp.launch_file_via_cli(cfg.potplayer_path, target_path, resume_s)
        return result

    playing = pp.wait_until_playing(anchor.hwnd, timeout_s=4.0)
    log.info("reopen: wait_until_playing -> %s (resume=%.2fs)", playing, resume_s)
    if not playing:
        # PotPlayer didn't pick up the drop. Fall through to CLI as a rescue.
        log.warning("reopen: no playback after 4s; launching via CLI as fallback")
        pp.launch_file_via_cli(cfg.potplayer_path, target_path, resume_s)
        return result

    if resume_s > 0.1:
        pp.seek_ms(anchor.hwnd, int(resume_s * 1000))
        log.info("reopen: seeked to %.2fs", resume_s)

    return result


# --- CLI one-shot (mostly for scripted testing) -----------------------------

def one_shot_rotate(
    *,
    delta_cw: int | None = None,
    absolute_cw: int | None = None,
) -> int:
    anchor = pp.build_anchor_best_effort_any()
    if anchor is None:
        notify_error(
            "No PotPlayer window found.\n"
            "Focus a PotPlayer window (or just have one running) and try again."
        )
        return 2

    result = run_rotation_flow(anchor, delta_cw=delta_cw, absolute_cw=absolute_cw)
    if not result.ok:
        notify_error(f"Rotation failed.\n\n{result.message}")
        return 1

    playlist_note = ""
    if not _session_state.playlist_note_shown:
        playlist_note = "\n(Custom curated playlists may need reloading; folder playlists rebuild automatically.)"
        _session_state.playlist_note_shown = True
    toast(
        APP_NAME,
        f"{result.previous_rotation_cw}° → {result.applied_rotation_cw}° CW "
        f"applied to {result.new_path.name if result.new_path else '?'}"
        + playlist_note,
    )
    return 0
