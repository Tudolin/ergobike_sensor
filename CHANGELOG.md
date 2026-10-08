# Changelog

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
