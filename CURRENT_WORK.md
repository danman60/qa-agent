# Current Work - QA Agent

## Last Session Summary (2026-03-25 evening)
LilLearner APK end-to-end test: diagnosed crash (missing JS bundle), fixed APK build, fixed AVD executor for React Native apps, achieved 11/11 PASS.

## What Changed This Session
- `29b2332` fix: AVD executor React Native compatibility + emulator auto-boot

### APK Fix (LilLearner)
- Root cause: debug APK had no JS bundle — Expo's `debuggableVariants` defaults to `["debug"]` which skips bundling
- Fix: set `debuggableVariants = []` in `android/app/build.gradle` + ran `expo prebuild --clean` + `./gradlew assembleDebug`

### AVD Executor (executors/avd.py)
- Auto-boot emulator (pixel-phone AVD) if none running
- Prefer `emulator-*` serial over real phones
- Wait for app ready: polls until 3+ meaningful UI nodes from uiautomator
- React Native click support: clickable View/ViewGroup matches button/link/tab roles
- Text fallback: click() falls back to text-only when role+name fails
- Escape/Back → KEYCODE_BACK for Android navigation
- Android system prompt: BACK action docs for non-web executors

### QA Agent Core (qa_agent.py)
- Gotcha learner: word-boundary matching (`\bbut\b` not `but`), min 10-char snippets
- Android prompt injection for non-WebExecutor sessions

## Build Status
PASSING — 11/11 on LilLearner APK (report: tests/reports/qa-20260325-165208/)

## Known Bugs & Issues
- Old `Browser` class in qa_agent.py is dead code (lines ~559-726) — remove in cleanup pass
- `do_login()` uses `executor.page_handle` (WebExecutor-specific)
- `generate_auto_checklist()` produces web-specific items for Android — needs Android equivalent
- Gotcha learner still too aggressive on app content strings ("No reports yet")

## Next Steps (priority order)
1. Add crash detection to AVD executor (check foreground app matches package after each step)
2. Test device executor on Pixel 7 Pro via Tailscale
3. Remove dead Browser class from qa_agent.py
4. Create Android-specific auto-checklist generator
5. Wire `--device all` into CLI main()
6. Playlist feature (deferred)

## Artifacts
- Checklist: `checklists/lillearner-android.md` (11 items)
- LilLearner bundled APK: `/home/danman60/projects/LilLearner/android/app/build/outputs/apk/debug/app-debug.apk`

## Architecture
```
qa_agent.py (orchestrator — platform agnostic)
├── executors/
│   ├── base.py       (abstract BaseExecutor interface)
│   ├── web.py        (Playwright — extracted from original code)
│   ├── avd.py        (ADB to local emulator via uiautomator)
│   └── device.py     (ADB to real phones via PhoneFarm registry)
├── ollama-runner.py  (local LLM task executor)
├── projects.json     (platform field auto-selects executor)
├── gotchas.md        (auto-learned fail phrases)
└── checklists/       (flat checklist files)
```

## Environment
- PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright
- Server: `python3 qa_agent.py --server --dashboard-port 9876`
- Tailscale URL: http://100.122.177.91:9876
- GitHub: https://github.com/danman60/qa-agent
