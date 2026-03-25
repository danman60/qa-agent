"""AVD (Android Virtual Device) executor — ADB-based emulator testing.

Implements BaseExecutor for testing Android apps on local emulators.
Uses adb commands for all interaction, uiautomator for element discovery.
"""

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import BaseExecutor


def _adb(args: list[str], serial: str | None = None, timeout: int = 30) -> tuple[str, str, int]:
    """Run an adb command. Returns (stdout, stderr, exit_code)."""
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "ADB command timed out", 1
    except Exception as e:
        return "", str(e), 1


class AVDExecutor(BaseExecutor):
    """Android emulator executor via ADB."""

    def __init__(self, apk_path: str = "", package: str = "", activity: str = "",
                 serial: str | None = None):
        self.apk_path = apk_path
        self.package = package
        self.activity = activity  # e.g. "com.example.app/.MainActivity"
        self.serial = serial  # None = use default device
        self._screen_size = (1080, 1920)
        self._current_activity = ""
        self._logcat_errors: list[dict] = []
        self._last_logcat_pos = 0

    def setup(self) -> bool:
        # Verify adb is connected
        stdout, _, rc = _adb(["devices"], timeout=10)
        if rc != 0:
            return False

        # Find device serial if not specified — prefer emulator over real devices
        if not self.serial:
            emulators = []
            devices = []
            lines = stdout.split("\n")[1:]  # skip header
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1].strip() == "device":
                    serial = parts[0].strip()
                    if serial.startswith("emulator-"):
                        emulators.append(serial)
                    else:
                        devices.append(serial)
            # Prefer emulator when testing APKs
            if emulators:
                self.serial = emulators[0]
            elif not devices:
                # No devices at all — boot an emulator
                if not self._boot_emulator():
                    return False
            else:
                # Only real devices available — still boot an emulator for APK testing
                if self.apk_path:
                    if not self._boot_emulator():
                        self.serial = devices[0]  # fallback to real device
                else:
                    self.serial = devices[0]

        # Get screen size
        stdout, _, _ = _adb(["shell", "wm", "size"], serial=self.serial)
        m = re.search(r"(\d+)x(\d+)", stdout)
        if m:
            self._screen_size = (int(m.group(1)), int(m.group(2)))

        # Install APK if provided
        if self.apk_path and os.path.isfile(self.apk_path):
            stdout, stderr, rc = _adb(["install", "-r", "-g", self.apk_path],
                                       serial=self.serial, timeout=120)
            if rc != 0 and "Success" not in stdout:
                return False

            # Auto-detect package from APK if not specified
            if not self.package:
                self.package = self._detect_package()

        # Launch app if we have a package
        if self.package:
            self._launch_app()

        # Clear logcat baseline
        _adb(["logcat", "-c"], serial=self.serial)

        return True

    def teardown(self) -> None:
        if self.package:
            _adb(["shell", "am", "force-stop", self.package], serial=self.serial)

    def navigate(self, target: str) -> bool:
        """Navigate to an activity or deep link.
        Accepts: activity name, intent URI, or deep link URL."""
        if target.startswith("http"):
            # Deep link
            stdout, stderr, rc = _adb([
                "shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", target
            ], serial=self.serial)
            time.sleep(2)
            return rc == 0
        elif "/" in target:
            # Activity: com.example.app/.SomeActivity
            stdout, stderr, rc = _adb([
                "shell", "am", "start", "-n", target
            ], serial=self.serial)
            time.sleep(2)
            return rc == 0
        else:
            # Just a screen name — try launching the app
            return self._launch_app()

    def snapshot(self) -> str:
        """Dump uiautomator XML and return parsed text representation."""
        _adb(["shell", "uiautomator", "dump", "/sdcard/ui.xml"], serial=self.serial)
        stdout, _, rc = _adb(["shell", "cat", "/sdcard/ui.xml"], serial=self.serial)
        if rc != 0 or not stdout:
            return "(snapshot error: uiautomator dump failed)"

        try:
            return self._xml_to_text(stdout)
        except Exception as e:
            return f"(snapshot parse error: {e})"

    def screenshot(self, path: str) -> str:
        """Take screenshot via adb screencap, pull, and resize."""
        remote = "/sdcard/qa-screenshot.png"
        _adb(["shell", "screencap", "-p", remote], serial=self.serial)
        _adb(["pull", remote, path], serial=self.serial)
        _adb(["shell", "rm", remote], serial=self.serial)

        # Resize if too large (prevents LLM image dimension errors)
        self._resize_image(path)
        return path

    def click(self, role: str, name: str) -> tuple[bool, str]:
        """Find element by role+name in uiautomator XML and tap its center.
        Falls back to text-only search if role+name fails."""
        bounds = self._find_element_bounds(role=role, name=name)
        if bounds:
            x, y = self._bounds_center(bounds)
            return self._tap(x, y)
        # Fallback: try text-only match (ignores role constraint)
        bounds = self._find_element_bounds(text=name)
        if bounds:
            x, y = self._bounds_center(bounds)
            return self._tap(x, y)
        return False, f"Element not found: {role} '{name}'"

    def click_text(self, text: str) -> tuple[bool, str]:
        """Find element by visible text and tap it."""
        bounds = self._find_element_bounds(text=text)
        if bounds:
            x, y = self._bounds_center(bounds)
            return self._tap(x, y)
        return False, f"Text not found: '{text}'"

    def fill(self, role: str, name: str, value: str) -> tuple[bool, str]:
        """Tap a text field and type into it."""
        ok, detail = self.click(role, name)
        if not ok:
            # Try clicking by text (field label)
            ok, detail = self.click_text(name)
        if not ok:
            return False, f"Can't focus field: {detail}"
        time.sleep(0.3)
        # Clear existing text
        _adb(["shell", "input", "keyevent", "KEYCODE_MOVE_END"], serial=self.serial)
        _adb(["shell", "input", "keyevent", "--longpress", "KEYCODE_DEL"], serial=self.serial)
        time.sleep(0.2)
        return self.type_text(value)

    def type_text(self, text: str) -> tuple[bool, str]:
        """Type text via adb. Handles special characters."""
        # ADB input text doesn't handle spaces well — replace with %s
        escaped = text.replace(" ", "%s").replace("&", "\\&").replace("<", "\\<").replace(">", "\\>")
        escaped = escaped.replace("(", "\\(").replace(")", "\\)").replace("|", "\\|")
        escaped = escaped.replace(";", "\\;").replace("'", "\\'").replace('"', '\\"')
        _adb(["shell", "input", "text", escaped], serial=self.serial)
        return True, "typed"

    def press_key(self, key: str) -> tuple[bool, str]:
        """Press a key via adb keyevent."""
        keymap = {
            "Enter": "KEYCODE_ENTER", "Tab": "KEYCODE_TAB",
            "Escape": "KEYCODE_BACK", "Back": "KEYCODE_BACK",
            "Backspace": "KEYCODE_DEL", "Delete": "KEYCODE_FORWARD_DEL",
        }
        keycode = keymap.get(key, f"KEYCODE_{key.upper()}")
        _adb(["shell", "input", "keyevent", keycode], serial=self.serial)
        return True, f"pressed {key}"

    def scroll(self, direction: str = "down") -> tuple[bool, str]:
        """Swipe to scroll."""
        w, h = self._screen_size
        cx = w // 2
        if direction == "down":
            _adb(["shell", "input", "swipe", str(cx), str(h * 3 // 4),
                   str(cx), str(h // 4), "300"], serial=self.serial)
        else:
            _adb(["shell", "input", "swipe", str(cx), str(h // 4),
                   str(cx), str(h * 3 // 4), "300"], serial=self.serial)
        return True, f"scrolled {direction}"

    def select_option(self, role: str, name: str, value: str) -> tuple[bool, str]:
        """Tap dropdown, then tap option text."""
        ok, detail = self.click(role, name)
        if not ok:
            return False, f"Can't open dropdown: {detail}"
        time.sleep(0.5)
        return self.click_text(value)

    def back(self) -> tuple[bool, str]:
        _adb(["shell", "input", "keyevent", "KEYCODE_BACK"], serial=self.serial)
        time.sleep(0.5)
        return True, "pressed back"

    def get_console_errors(self, since_last: bool = True) -> list[dict]:
        """Get logcat errors filtered by package."""
        stdout, _, _ = _adb(["logcat", "-d", "*:E"], serial=self.serial, timeout=10)
        errors = []
        for line in stdout.split("\n"):
            if self.package and self.package.lower() not in line.lower():
                continue
            if line.strip():
                errors.append({
                    "type": "error",
                    "text": line.strip()[:300],
                    "url": self._get_current_activity(),
                })
        if since_last:
            _adb(["logcat", "-c"], serial=self.serial)
        return errors[-20:]  # last 20

    def get_network_errors(self, since_last: bool = True) -> list[dict]:
        """Android doesn't have network monitoring like browser. Return empty."""
        return []

    def discover_links(self, snapshot_text: str) -> dict:
        """Extract clickable elements from snapshot text."""
        links = {}
        for line in snapshot_text.split("\n"):
            # Match button/link-like elements
            m = re.search(r'(?:button|link|tab)\s+"([^"]+)"', line, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                if name and len(name) < 60:
                    links[name] = True
        return links

    def set_viewport(self, width: int, height: int) -> None:
        """Override screen size (density override)."""
        _adb(["shell", "wm", "size", f"{width}x{height}"], serial=self.serial)
        self._screen_size = (width, height)

    @property
    def url(self) -> str:
        """Return current activity as the 'URL'."""
        return self._get_current_activity()

    def get_screen_size(self) -> tuple[int, int]:
        return self._screen_size

    # ── Internal helpers ──

    def _boot_emulator(self, timeout: int = 90) -> bool:
        """Boot an Android emulator. Returns True if emulator is ready."""
        # Find available AVDs
        try:
            result = subprocess.run(["emulator", "-list-avds"],
                                     capture_output=True, text=True, timeout=10)
            avds = [a.strip() for a in result.stdout.strip().split("\n") if a.strip()]
        except Exception:
            return False

        if not avds:
            return False

        # Prefer 'pixel-phone' or 'test-device', else use first available
        avd = avds[0]
        for preferred in ["pixel-phone", "test-device"]:
            if preferred in avds:
                avd = preferred
                break

        # Launch emulator in background
        subprocess.Popen(
            ["emulator", "-avd", avd, "-no-window", "-no-audio",
             "-no-boot-anim", "-gpu", "swiftshader_indirect"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        # Wait for emulator to boot
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            stdout, _, _ = _adb(["devices"], timeout=10)
            for line in stdout.split("\n")[1:]:
                parts = line.split("\t")
                if (len(parts) >= 2 and parts[1].strip() == "device"
                        and parts[0].strip().startswith("emulator-")):
                    self.serial = parts[0].strip()
                    # Check boot completed
                    out, _, _ = _adb(["shell", "getprop", "sys.boot_completed"],
                                      serial=self.serial, timeout=5)
                    if out.strip() == "1":
                        return True
        return False

    def _launch_app(self) -> bool:
        if self.activity:
            _, _, rc = _adb(["shell", "am", "start", "-n", self.activity], serial=self.serial)
        else:
            _, _, rc = _adb([
                "shell", "monkey", "-p", self.package,
                "-c", "android.intent.category.LAUNCHER", "1"
            ], serial=self.serial)
        # Wait for app to fully render (Expo/RN apps need longer on emulator)
        self._wait_for_app_ready()
        return rc == 0

    def _wait_for_app_ready(self, timeout: int = 30) -> bool:
        """Wait until the app has meaningful UI content (not just splash)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            xml_str = self._get_ui_xml()
            if not xml_str:
                continue
            try:
                root = ET.fromstring(xml_str)
            except ET.ParseError:
                continue
            # Count nodes with text or content-desc (meaningful UI)
            meaningful = 0
            for node in root.iter("node"):
                pkg = node.get("package", "")
                text = node.get("text", "")
                desc = node.get("content-desc", "")
                if pkg == self.package and (text or desc):
                    meaningful += 1
            if meaningful >= 3:
                return True
        return False

    def _detect_package(self) -> str:
        """Try to detect package name from installed APK via aapt or dumpsys."""
        if self.apk_path:
            try:
                result = subprocess.run(
                    ["aapt", "dump", "badging", self.apk_path],
                    capture_output=True, text=True, timeout=10
                )
                m = re.search(r"package: name='([^']+)'", result.stdout)
                if m:
                    return m.group(1)
            except Exception:
                pass
        return ""

    def _get_current_activity(self) -> str:
        """Get the current foreground activity."""
        stdout, _, _ = _adb(["shell", "dumpsys", "activity", "activities"],
                             serial=self.serial, timeout=5)
        # Look for "mResumedActivity" or "topResumedActivity"
        for line in stdout.split("\n"):
            if "mResumedActivity" in line or "topResumedActivity" in line:
                m = re.search(r"(\S+/\S+)", line)
                if m:
                    self._current_activity = m.group(1)
                    return self._current_activity
        return self._current_activity or f"{self.package}/(unknown)"

    def _get_ui_xml(self) -> str:
        """Dump and return raw uiautomator XML."""
        _adb(["shell", "uiautomator", "dump", "/sdcard/ui.xml"], serial=self.serial)
        stdout, _, _ = _adb(["shell", "cat", "/sdcard/ui.xml"], serial=self.serial)
        return stdout

    def _find_element_bounds(self, role: str = "", name: str = "",
                              text: str = "") -> str | None:
        """Find element bounds in uiautomator XML.
        Returns bounds string like '[100,200][300,400]' or None."""
        xml_str = self._get_ui_xml()
        if not xml_str:
            return None

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return None

        for node in root.iter("node"):
            node_text = node.get("text", "")
            node_desc = node.get("content-desc", "")
            node_class = node.get("class", "")
            node_rid = node.get("resource-id", "")
            node_clickable = node.get("clickable") == "true"
            bounds = node.get("bounds", "")

            if not bounds:
                continue

            # Match by text
            if text and (text.lower() in node_text.lower() or
                         text.lower() in node_desc.lower()):
                return bounds

            # Match by name (accessibility text, content-desc, or resource-id)
            if name:
                name_lower = name.lower()
                if (name_lower in node_text.lower() or
                    name_lower in node_desc.lower() or
                    name_lower in node_rid.lower()):
                    # Role filter: relax for clickable View/ViewGroup (React Native)
                    if role:
                        if not self._matches_role(node_class, role, node_clickable):
                            continue
                    return bounds

        return None

    def _matches_role(self, android_class: str, web_role: str,
                       clickable: bool = False) -> bool:
        """Map web accessibility roles to Android widget classes.
        React Native renders most interactive elements as View/ViewGroup,
        so clickable views match button/link/tab roles."""
        role_map = {
            "button": ["Button", "ImageButton", "FloatingActionButton", "MaterialButton"],
            "textbox": ["EditText", "TextInputEditText", "AutoCompleteTextView"],
            "link": ["TextView"],
            "checkbox": ["CheckBox", "SwitchCompat", "Switch"],
            "combobox": ["Spinner", "AutoCompleteTextView"],
            "heading": ["TextView"],
            "img": ["ImageView"],
            "tab": ["TabView", "Tab"],
        }
        expected = role_map.get(web_role.lower(), [])
        if not expected:
            return True
        if any(e.lower() in android_class.lower() for e in expected):
            return True
        # React Native: clickable View/ViewGroup acts as button/link/tab
        if clickable and web_role.lower() in ("button", "link", "tab"):
            cls_lower = android_class.lower()
            if "view" in cls_lower:
                return True
        return False

    @staticmethod
    def _bounds_center(bounds_str: str) -> tuple[int, int]:
        """Parse '[x1,y1][x2,y2]' and return center (x, y)."""
        m = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
        if len(m) >= 2:
            x1, y1 = int(m[0][0]), int(m[0][1])
            x2, y2 = int(m[1][0]), int(m[1][1])
            return (x1 + x2) // 2, (y1 + y2) // 2
        return 0, 0

    def _tap(self, x: int, y: int) -> tuple[bool, str]:
        """Tap at coordinates."""
        _adb(["shell", "input", "tap", str(x), str(y)], serial=self.serial)
        time.sleep(0.5)
        return True, f"tapped ({x}, {y})"

    def _xml_to_text(self, xml_str: str) -> str:
        """Convert uiautomator XML to human-readable accessibility text.
        Mimics Playwright's aria_snapshot format for LLM compatibility."""
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return xml_str[:3000]

        lines = []
        for node in root.iter("node"):
            text = node.get("text", "")
            desc = node.get("content-desc", "")
            cls = node.get("class", "").split(".")[-1]  # Just class name
            rid = node.get("resource-id", "").split("/")[-1] if node.get("resource-id") else ""
            clickable = node.get("clickable") == "true"
            enabled = node.get("enabled") == "true"
            focused = node.get("focused") == "true"

            # Map Android class to web-like role
            role = self._class_to_role(cls)
            if not role and not text and not desc:
                continue

            label = text or desc or rid
            if not label:
                continue

            prefix = ""
            if focused:
                prefix = "[focused] "
            if not enabled:
                prefix += "[disabled] "

            if role:
                lines.append(f'  {prefix}{role} "{label}"')
            elif clickable:
                lines.append(f'  {prefix}button "{label}"')
            else:
                lines.append(f'  {prefix}text "{label}"')

        return "\n".join(lines) if lines else "(empty screen)"

    @staticmethod
    def _class_to_role(cls: str) -> str:
        """Map Android widget class name to web-like role."""
        mapping = {
            "Button": "button", "ImageButton": "button",
            "FloatingActionButton": "button", "MaterialButton": "button",
            "EditText": "textbox", "TextInputEditText": "textbox",
            "AutoCompleteTextView": "combobox",
            "CheckBox": "checkbox", "Switch": "checkbox", "SwitchCompat": "checkbox",
            "RadioButton": "radio",
            "Spinner": "combobox",
            "ImageView": "img",
            "TextView": "",  # Too generic
            "RecyclerView": "",
            "ScrollView": "",
        }
        return mapping.get(cls, "")

    @staticmethod
    def _resize_image(path: str, max_w: int = 1000, max_h: int = 2000):
        """Resize image if too large (prevents LLM dimension errors)."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=5
            )
            parts = result.stdout.strip().split(",")
            if len(parts) < 2:
                return
            w, h = int(parts[0]), int(parts[1])
            if w > max_w or h > max_h:
                tmp = path.replace(".png", "_sm.png")
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-i", path,
                    "-vf", f"scale='min({max_w},iw)':'min({max_h},ih)':force_original_aspect_ratio=decrease",
                    tmp
                ], timeout=10)
                os.replace(tmp, path)
        except Exception:
            pass
