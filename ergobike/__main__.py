import argparse
import sys

from . import __version__
from .desktop import main as desktop_main


def main() -> int:
    p = argparse.ArgumentParser(prog="ergobike", description="Cadence tracker for exercise bikes")
    p.add_argument("--replay", metavar="WAV", help="play a recording in a loop instead of the mic")
    p.add_argument("--db", help=r"SQLite file (default: %%LOCALAPPDATA%%\ErgoBike\treinos.db)")
    p.add_argument("--browser", action="store_true", help="open in the default browser")
    p.add_argument("--demo-hr", action="store_true",
                   help="simulated heart rate instead of Pulsoid")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args()
    return desktop_main(db=args.db, replay=args.replay, browser=args.browser, demo_hr=args.demo_hr)


if __name__ == "__main__":
    sys.exit(main())
