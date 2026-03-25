"""Device executor — ADB-based testing on real phones via PhoneFarm registry.

Extends AVDExecutor with:
- Device lookup from PhoneFarm's data/devices.json
- Wireless ADB connection via Tailscale IP
- Pre-test: wake device, unlock, dismiss notifications
- Post-test: graceful disconnect
- Coordinate scaling based on actual device resolution
"""

import json
import os
import time
from pathlib import Path

from .avd import AVDExecutor, _adb

PHONEFARM_DEVICES = Path.home() / "projects" / "phonefarm" / "data" / "devices.json"


def load_device_registry() -> list[dict]:
    """Load PhoneFarm device registry."""
    if PHONEFARM_DEVICES.is_file():
        return json.loads(PHONEFARM_DEVICES.read_text())
    return []


class DeviceExecutor(AVDExecutor):
    """Real phone executor via PhoneFarm device registry + wireless ADB."""

    def __init__(self, apk_path: str = "", package: str = "", activity: str = "",
                 device_id: str = ""):
        self.device_id = device_id  # PhoneFarm ID e.g. "pixel7"
        self._device_info = None
        super().__init__(apk_path=apk_path, package=package, activity=activity)

    def setup(self) -> bool:
        # Look up device in PhoneFarm registry
        registry = load_device_registry()

        if self.device_id == "all":
            # Multi-device mode is handled by the caller
            # For now, pick first online device
            online = [d for d in registry if d.get("status") == "online"]
            if not online:
                return False
            self._device_info = online[0]
        elif self.device_id:
            self._device_info = next(
                (d for d in registry if d["id"] == self.device_id), None
            )
        else:
            # No device specified — pick first online
            online = [d for d in registry if d.get("status") == "online"]
            if online:
                self._device_info = online[0]

        if not self._device_info:
            return False

        # Connect via wireless ADB if serial looks like IP:port
        serial = self._device_info["serial"]
        if ":" in serial:
            stdout, stderr, rc = _adb(["connect", serial], timeout=15)
            if rc != 0 and "connected" not in stdout and "already" not in stdout:
                return False
            self.serial = serial
        else:
            self.serial = serial

        # Wake device and dismiss lock screen
        self._wake_device()

        # Get actual screen resolution
        stdout, _, _ = _adb(["shell", "wm", "size"], serial=self.serial)
        import re
        m = re.search(r"(\d+)x(\d+)", stdout)
        if m:
            self._screen_size = (int(m.group(1)), int(m.group(2)))

        # Install APK if provided
        if self.apk_path and os.path.isfile(self.apk_path):
            stdout, stderr, rc = _adb(["install", "-r", "-g", self.apk_path],
                                       serial=self.serial, timeout=120)
            if rc != 0 and "Success" not in stdout:
                return False

            if not self.package:
                self.package = self._detect_package()

        # Launch app
        if self.package:
            self._launch_app()

        # Clear logcat
        _adb(["logcat", "-c"], serial=self.serial)

        return True

    def teardown(self) -> None:
        """Stop app and disconnect gracefully."""
        if self.package:
            _adb(["shell", "am", "force-stop", self.package], serial=self.serial)

        # Lock screen (be a good citizen)
        _adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"], serial=self.serial)

        # Disconnect wireless ADB
        if self.serial and ":" in self.serial:
            _adb(["disconnect", self.serial])

    @property
    def device_name(self) -> str:
        if self._device_info:
            return self._device_info.get("name", self.device_id)
        return self.device_id or "unknown"

    def _wake_device(self):
        """Wake device, unlock, dismiss notifications."""
        # Wake
        _adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=self.serial)
        time.sleep(0.5)

        # Dismiss lock screen (swipe up)
        w, h = 540, 1920  # Default, will be updated
        stdout, _, _ = _adb(["shell", "wm", "size"], serial=self.serial)
        import re
        m = re.search(r"(\d+)x(\d+)", stdout)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        _adb(["shell", "input", "swipe",
               str(w // 2), str(h * 3 // 4), str(w // 2), str(h // 4), "300"],
              serial=self.serial)
        time.sleep(0.5)

        # Dismiss notifications (press home)
        _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=self.serial)
        time.sleep(0.5)


def run_on_all_devices(apk_path: str, package: str, checklist_path: str,
                        provider: str, model: str, **kwargs) -> list:
    """Run the same test on all online devices. Returns list of (device_name, state)."""
    registry = load_device_registry()
    online = [d for d in registry if d.get("status") == "online"]

    if not online:
        print("No online devices found in PhoneFarm registry")
        return []

    results = []
    for device in online:
        print(f"\n{'='*60}")
        print(f"  DEVICE: {device['name']} ({device['id']})")
        print(f"  Serial: {device['serial']}")
        print(f"{'='*60}")

        executor = DeviceExecutor(
            apk_path=apk_path,
            package=package,
            device_id=device["id"],
        )
        # Import run_agent here to avoid circular import
        from qa_agent import run_agent, parse_checklist, generate_auto_checklist
        from datetime import datetime

        items = []
        if checklist_path and os.path.isfile(checklist_path):
            with open(checklist_path) as f:
                items = parse_checklist(f.read())
        if not items:
            items = generate_auto_checklist(f"android://{package}")

        report_dir = f"tests/reports/device-{device['id']}-{datetime.now():%Y%m%d-%H%M%S}"
        state = run_agent(
            f"android://{package}", provider, model, items, report_dir,
            no_dashboard=True, executor=executor, **kwargs,
        )
        results.append((device["name"], state))

    return results
