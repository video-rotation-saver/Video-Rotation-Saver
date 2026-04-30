"""Entry points.

Usage:
    pythonw -m potplayer_rotate                 # run daemon (silent, tray)
    python  -m potplayer_rotate daemon          # run daemon with console (debug)
    python  -m potplayer_rotate rotate --cw     # one-shot rotate 90 CW, then exit
    python  -m potplayer_rotate rotate --angle 180
"""
from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="potplayer_rotate")
    p.add_argument("--watch-potplayer", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("daemon", help="Run the tray + hotkey daemon (default).")

    rot = sub.add_parser("rotate", help="Apply a single rotation to the current PotPlayer file and exit.")
    g = rot.add_mutually_exclusive_group(required=True)
    g.add_argument("--cw", action="store_true", help="Rotate picture 90 degrees clockwise (delta).")
    g.add_argument("--ccw", action="store_true", help="Rotate picture 90 degrees counter-clockwise (delta).")
    g.add_argument("--angle", type=int, choices=(0, 90, 180, 270),
                   help="Set absolute CW picture rotation.")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.watch_potplayer:
        from .autostart import run_potplayer_watcher
        return run_potplayer_watcher()

    if args.cmd == "rotate":
        from .rotate import one_shot_rotate
        if args.cw:
            return one_shot_rotate(delta_cw=90)
        if args.ccw:
            return one_shot_rotate(delta_cw=-90)
        return one_shot_rotate(absolute_cw=args.angle)

    # default: daemon
    from .daemon import run_daemon
    return run_daemon()


if __name__ == "__main__":
    sys.exit(main())
