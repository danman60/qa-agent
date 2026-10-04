# Fresh Android captures

Purpose: complete the full development and testing of the app without treating an earlier Android screen as current evidence.

Root verification owner authorized this narrow QA I/O repair after a real Pixel system recording made uiautomator report `ERROR: could not get idle state`, while the existing reader still returned `/sdcard/ui.xml` from an earlier successful capture.

Scope: `executors/avd.py` only. DeviceExecutor inherits this implementation. Do not change unrelated QA runner/browser files, device setup, processes, or teardown.

1. Trace snapshot and selector capture paths. Both currently issue dump to the same shared filename and ignore dump status. Graph affected `_get_ui_xml` includes native readiness, label discovery, launch and fill; direct literal call review includes native selectors and snapshot. All share the repaired reader.
2. Generate a unique filename for each dump, require successful dump output, read and parse the fresh XML, and remove only that generated file. Raise an explicit error before any tap on capture failure. Snapshot must use the same checked reader.
3. Verify Python compilation. On the real Pixel, read the synthetic conversation twice and verify fresh paths are cleaned up. Use an actual ADB transport failure to verify the reader fails explicitly. Preserve the earlier real-device idle-error evidence; do not simulate a successful dump or mock ADB.
4. Report evidence and exact changed file to the main owner. Final native acceptance must use the repaired reader and reject failed captures.

Verified 2026-10-04, 11:38 EDT: Python compilation passes. Two fresh Pixel7Pro snapshots contain the current synthetic Plan To Live conversation; an actual unavailable ADB transport raises the explicit capture error. Independent find and ls checks show no generated capture files left behind. Evidence: `/home/danman60/projects/plantolive-app/docs/evidence/2026-10-04-baseline/qa-native-fresh-capture.json`. The earlier recording-induced idle failure is the real failure evidence; the post-fix negative probe covers transport failure, not a repeated recording.
