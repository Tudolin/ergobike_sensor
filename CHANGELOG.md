# Changelog

## 1.1.0

Workout modes and heart rate.

- Structured workouts: HIIT 30/30, Tabata, 1 min sprints, pyramid, steady cadence and a 45 min
  endurance ride. Each step has a target rpm range shown with a countdown and an
  on target / speed up / ease off indicator
- Interval builder for your own sessions (warm-up, rounds of work/rest, cool-down)
- Beeps on the last three seconds of a step and a spoken cue with the next step's name, length and
  target (pt-BR voice). Both can be turned off in settings
- Time or distance goal for free rides
- Ride report shows the target bands on the cadence chart and time in target for every step
- Saving a ride tells you when it set a new best 1 to 60 min effort
- Heart rate from Wear OS watches through Pulsoid: live bpm and zones, calories from heart rate,
  TRIMP training load (also weekly on the progress screen), cardiac drift, recovery after each
  interval, and heart rate in the TCX export
- `--demo-hr` to try the heart-rate screens with a simulated feed
- Space bar pauses/resumes, F toggles a focus mode with bigger numbers

**Download:** unzip `ErgoBike-1.1.0-windows-x64.zip` and run `ErgoBike.exe`. Your rides from 1.0.0
are kept and upgraded in place.

## 1.0.0

First public version.

- Desktop app for Windows: live cadence, ride history, per-ride report, weekly progress
- Reed pulses read through the PC microphone input, with a step detector that ignores baseline
  drift, mains hum and cable interference
- Auto calibration and a live signal meter in the settings screen
- TCX and CSV export (TCX uploads to Strava / Garmin Connect as an indoor ride)
- Estimated distance, speed and calories
- Closing the window mid-ride asks to save or discard; opening a second copy focuses the first one
- `python -m ergobike.cli` for signal diagnostics (record, replay, calibrate)
- ESP32 sketch that exposes the sensor as a Bluetooth Cycling Speed and Cadence device (untested
  on hardware)

**Download:** unzip `ErgoBike-1.0.0-windows-x64.zip` and run `ErgoBike.exe`. Requires Windows 10/11
with the WebView2 runtime (already there on Windows 11 / recent Edge).
