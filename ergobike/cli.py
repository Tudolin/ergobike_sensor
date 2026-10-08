"""Signal diagnostics from the terminal.

    python -m ergobike.cli list
    python -m ergobike.cli calibrate --device 2
    python -m ergobike.cli live --device 2 --record ride.wav
    python -m ergobike.cli replay ride.wav [--calibrate]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time

import numpy as np

from .audio import PulseSource, input_devices, read_wav, wav_blocks
from .cadence import CadenceTracker
from .pulses import POLARITIES, Calibration, EdgeDetector, calibrate


def print_calibration(c: Calibration) -> None:
    print(f"\nlevel      min {c.peak_min:+.3f}  max {c.peak_max:+.3f}")
    print(f"step noise {c.noise:.4f} (p99)")
    print(f"close      {c.close_count:4d} steps, median {c.close_median:.3f}")
    print(f"open       {c.open_count:4d} steps, median {c.open_median:.3f}")
    if c.threshold is None:
        print("\nNo clear steps. Check the cable, the input device and the channel.")
        return
    print(f"\nsuggested: --edge {c.polarity} --threshold {c.threshold}  (margin {c.margin:.0f}x)")
    if c.margin < 5:
        print("Low margin. Turn off Windows audio enhancements/AGC or try the WASAPI/WDM-KS device.")


class Session:
    def __init__(self, args):
        self.args = args
        self.tracker = CadenceTracker(args.pulses_per_rev)
        self._fh = open(args.csv, "w", newline="") if args.csv else None
        self._csv = csv.writer(self._fh) if self._fh else None
        if self._csv:
            self._csv.writerow(["t_s", "rev", "rpm_inst", "rpm_avg"])

    def pulse(self, t: float):
        rev = self.tracker.add_pulse(t)
        if self._csv:
            fmt = lambda v: "" if v is None else f"{v:.1f}"
            self._csv.writerow([f"{rev.t:.3f}", rev.rev, fmt(rev.rpm_inst), fmt(rev.rpm_avg)])
        return rev

    def status(self, now: float) -> str:
        rpm = self.tracker.rpm(now)
        revs = self.tracker.revs
        line = f"rpm {rpm:6.1f} | revs {revs:7.0f} | time {int(now // 60):02d}:{int(now % 60):02d}"
        if self.args.wheel_m > 0:
            line += (f" | {rpm * self.args.wheel_m * 0.06:5.1f} km/h"
                     f" | {revs * self.args.wheel_m / 1000:6.2f} km")
        return line

    def close(self) -> None:
        if self._fh:
            self._fh.close()


def detector(args, rate: int) -> EdgeDetector:
    return EdgeDetector(rate, threshold=args.threshold, polarity=POLARITIES[args.edge],
                        min_interval_s=args.min_interval)


def cmd_list(_args) -> None:
    for i, name in input_devices():
        print(f"{i:3d}  {name}")


def cmd_calibrate(args) -> None:
    import sounddevice as sd
    print(f"Pedal slowly for {args.seconds:.0f} s (Ctrl+C to stop early).\n")
    blocks, peak = [], [0.0]
    det = EdgeDetector(args.rate)

    def callback(indata, frames, t, status):
        x = indata[:, args.channel].copy()
        blocks.append(x)
        det.process(x)
        peak[0] = max(peak[0], det.last_peak)

    try:
        with sd.InputStream(device=args.device, channels=args.channel + 1, samplerate=args.rate,
                            blocksize=1024, callback=callback):
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                time.sleep(0.1)
                v, peak[0] = peak[0], 0.0
                print(f"\rstep {v:5.3f} |{'#' * int(min(v / 2, 1.0) * 50):<50}|", end="", flush=True)
    except KeyboardInterrupt:
        pass
    print()
    if blocks:
        print_calibration(calibrate(np.concatenate(blocks), args.rate))


def cmd_live(args) -> None:
    session = Session(args)
    source = PulseSource(args.device, args.rate, detector(args, args.rate),
                         channel=args.channel, record_path=args.record)
    print("Reading... Ctrl+C to stop.\n")
    try:
        with source:
            while True:
                while not source.events.empty():
                    session.pulse(source.events.get())
                extra = f" | overflow {source.overflows}" if source.overflows else ""
                print(f"\r{session.status(source.stream_time)}{extra}   ", end="", flush=True)
                time.sleep(0.25)
    except KeyboardInterrupt:
        print()
    finally:
        session.close()


def cmd_replay(args) -> None:
    if args.calibrate:
        x, rate = read_wav(args.wav, args.channel)
        print_calibration(calibrate(x, rate))
        return
    blocks, rate = wav_blocks(args.wav, args.channel)
    det = detector(args, rate)
    session = Session(args)
    for block in blocks:
        for idx in det.process(block):
            rev = session.pulse(idx / rate)
            inst = f"{rev.rpm_inst:6.1f}" if rev.rpm_inst else "     -"
            print(f"{rev.t:8.3f}s  rev {rev.rev:4d}  rpm {inst}")
    print("\n" + session.status(det.n / rate))
    session.close()


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--channel", type=int, default=0)
    common.add_argument("--threshold", type=float, default=0.1)
    common.add_argument("--edge", choices=POLARITIES, default="close")
    common.add_argument("--min-interval", type=float, default=0.2)
    common.add_argument("--pulses-per-rev", type=int, default=1)
    common.add_argument("--wheel-m", type=float, default=0.0)
    common.add_argument("--csv")

    audio = argparse.ArgumentParser(add_help=False)
    audio.add_argument("--device", type=int)
    audio.add_argument("--rate", type=int, default=44100)

    p = argparse.ArgumentParser(prog="ergobike.cli", description="Reed sensor signal tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list input devices").set_defaults(func=cmd_list)
    c = sub.add_parser("calibrate", parents=[common, audio], help="measure noise and step size")
    c.add_argument("--seconds", type=float, default=15.0)
    c.set_defaults(func=cmd_calibrate)
    lv = sub.add_parser("live", parents=[common, audio], help="count revolutions live")
    lv.add_argument("--record", help="also save the raw signal to a WAV file")
    lv.set_defaults(func=cmd_live)
    r = sub.add_parser("replay", parents=[common], help="run the detector on a WAV file")
    r.add_argument("wav")
    r.add_argument("--calibrate", action="store_true")
    r.set_defaults(func=cmd_replay)
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
