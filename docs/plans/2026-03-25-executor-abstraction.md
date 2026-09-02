# QA Agent Executor Abstraction — Unified Web + Android Testing

**Date:** 2026-03-25
**Goal:** Add Android testing (emulator + real device) to qa_agent.py via pluggable executors, then rewire test-webapp and android-apk-testing skills to use it.

## Architecture

```
qa_agent.py (orchestrator — platform agnostic)
├── executors/
│   ├── base.py             (abstract interface)
│   ├── web.py              (Playwright — extracted from current code)
│   ├── avd.py              (ADB to local emulator)
│   └── device.py           (ADB to real phones via PhoneFarm device registry)
├── flow_parser.py          (existing — shared)
├── checklist.py            (existing — shared state machine)
├── gotchas.py              (existing — shared)
├── dashboard.py            (existing — shared)
└── projects.json           (add platform field per project)
```

## Executor Interface (base.py)

```python
class BaseExecutor:
    def navigate(self, target: str) -> bool: ...       # URL or activity
    def screenshot(self, path: str) -> str: ...         # returns path to resized image
    def get_elements(self) -> str: ...                  # DOM snapshot or uiautomator XML
    def tap(self, x: int, y: int) -> bool: ...          # click/tap
    def tap_element(self, selector: str) -> bool: ...   # CSS selector or resource-id
    def type_text(self, text: str) -> bool: ...         # fill/input text
    def back(self) -> bool: ...                         # browser back or KEYCODE_BACK
    def get_errors(self) -> list[str]: ...              # console errors or logcat
    def get_page_text(self) -> str: ...                 # visible text content
    def setup(self) -> bool: ...                        # connect, install APK, etc.
    def teardown(self) -> None: ...                     # cleanup
    def get_screen_size(self) -> tuple[int, int]: ...   # for coordinate scaling
```

## Phase 1: Fix test-webapp skill (no code changes to qa_agent)

Rewrite ~/.claude/skills/test-webapp/SKILL.md to invoke:
```bash
python3 ~/projects/qa-agent/qa_agent.py <URL> --checklist <path> --email <email> --password <pass>
```
Instead of spawning raw Opus + playwright-cli.

## Phase 2: Extract web executor

1. Read qa_agent.py thoroughly — understand how it currently interacts with Playwright
2. Create executors/ directory
3. Create executors/base.py with abstract BaseExecutor class
4. Create executors/web.py — extract all Playwright interaction code from qa_agent.py
5. Modify qa_agent.py to use WebExecutor via the interface
6. Test: `python3 qa_agent.py https://example.com --checklist <path>` still works identically
7. Commit: "refactor: extract web executor from qa_agent"

## Phase 3: Build AVD executor

1. Create executors/avd.py implementing BaseExecutor
   - setup(): verify adb connected, install APK, launch app
   - navigate(): `adb shell am start -n <package>/<activity>`
   - screenshot(): `adb exec-out screencap -p > file.png`, resize with ffmpeg
   - get_elements(): `adb shell uiautomator dump /sdcard/ui.xml`, pull + parse XML
   - tap(): `adb shell input tap X Y`
   - tap_element(): parse uiautomator XML for element bounds, tap center
   - type_text(): `adb shell input text <escaped>`
   - back(): `adb shell input keyevent KEYCODE_BACK`
   - get_errors(): `adb logcat -d *:E | grep <package> | tail -20`
   - get_page_text(): extract text from uiautomator XML
   - get_screen_size(): `adb shell wm size`
2. Add --executor flag to qa_agent.py CLI: `--executor web|avd|device` (default: web)
3. Add --apk flag for Android: `--apk /path/to/app.apk`
4. Add --package flag: `--package com.example.app` (auto-detected from APK if possible)
5. Test on local emulator with a known APK (LilLearner)
6. Commit: "feat: add AVD executor for Android emulator testing"

## Phase 4: Build device executor

1. Create executors/device.py extending avd.py
   - Reads PhoneFarm device registry at ~/projects/phonefarm/data/devices.json
   - Connects via `adb connect <tailscale-ip>:<port>`
   - Pre-test: wake device, unlock, dismiss notifications, detect resolution
   - Scale coordinates based on actual screen resolution
   - Post-test: disconnect gracefully
2. Add --device flag: `--device pixel-7-pro` or `--device all`
3. If --device all: run same flow on all available devices, aggregate results
4. Test on Pixel 7 Pro (100.67.114.30:38387 — currently connected)
5. Commit: "feat: add device executor for real phone testing via PhoneFarm"

## Phase 5: Rewire android-apk-testing skill

Rewrite ~/.claude/skills/android-apk-testing/SKILL.md to invoke:
```bash
# Emulator
python3 ~/projects/qa-agent/qa_agent.py --apk /path/to/app.apk --executor avd --checklist tests/agent/flow-login.md

# Real device
python3 ~/projects/qa-agent/qa_agent.py --apk /path/to/app.apk --executor device --device pixel-7-pro --checklist tests/agent/flow-login.md
```

## Phase 6: Update projects.json

Add platform field to each project:
```json
{
  "LilLearner": {
    "platform": "android",
    "package": "com.lillearner.app",
    "apk_path": "/mnt/firmament/LilLearner-mega-build-2026-03-25.apk",
    ...
  },
  "CompPortal": {
    "platform": "web",
    "url": "https://tester.compsync.net",
    ...
  }
}
```

qa_agent.py auto-selects executor based on platform if --executor not specified.

## Testing Strategy

- Phase 2: run existing web tests, verify identical results
- Phase 3: test LilLearner APK on emulator — install, launch, navigate 3 screens, screenshot each
- Phase 4: test same APK on Pixel 7 Pro via Tailscale
- Each phase must pass before moving to next

## DO NOT

- Break existing web testing — all current qa_agent behavior must be preserved
- Import PhoneFarm TypeScript code — reimplement ADB patterns in Python
- Add DroidClaw as a dependency — use uiautomator dump directly
- Make flow files platform-specific (yet) — that's a later enhancement
- Touch PhoneFarm codebase

## Files to Read First

1. ~/projects/qa-agent/qa_agent.py (the whole thing)
2. ~/projects/qa-agent/projects.json
3. ~/projects/qa-agent/gotchas.md
4. ~/projects/phonefarm/src/device-manager.ts (for device registry format)
5. ~/projects/phonefarm/src/instagram.ts (for ADB patterns)
6. ~/projects/phonefarm/data/devices.json (if exists)
