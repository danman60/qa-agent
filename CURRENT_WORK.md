# Current Work - QA Agent

## Last Session Summary (2026-03-25)
Implemented executor abstraction for unified web + Android testing. All 6 phases of the plan at docs/plans/2026-03-25-executor-abstraction.md completed — pluggable executors (web, AVD, device), CLI flags, skill rewrites, projects.json platform field.

## What Changed
- `9987238` refactor: extract web executor — created executors/ package with BaseExecutor, WebExecutor
- `1672299` feat: AVD executor — ADB-based Android emulator testing via uiautomator
- `d7b4ef5` feat: device executor — real phone testing via PhoneFarm registry + Tailscale ADB
- `eeda73b` feat: platform field in projects.json, auto-select executor in dashboard server

## Build Status
PASSING — all imports verified, CLI --help works, no syntax errors

## Known Bugs & Issues
- Old `Browser` class in qa_agent.py is dead code (lines ~559-726) — remove in cleanup pass
- `do_login()` uses `executor.page_handle` (WebExecutor-specific) — won't work if AVD executor passed with credentials
- Android auto-checklist `generate_auto_checklist()` produces web-specific items — needs Android equivalent

## Incomplete Work
- No end-to-end test of AVD executor on real APK (emulator not running this session)
- No test of device executor on Pixel 7 Pro
- `--device all` multi-device mode in `run_on_all_devices()` not wired into CLI main()

## Tests
- Not run this session (structural refactor, verified via import checks only)
- All 3 executors need real-world testing

## Next Steps (priority order)
1. Test AVD executor on LilLearner APK on emulator
2. Test device executor on Pixel 7 Pro via Tailscale
3. Remove dead Browser class from qa_agent.py
4. Create Android-specific auto-checklist generator
5. Wire `--device all` into CLI main()
6. Playlist feature (from previous session plan — see below)

## Gotchas for Next Session
- Skill files (test-webapp, android-apk-testing) are at ~/.claude/skills/ — outside this repo
- PhoneFarm device registry: ~/projects/phonefarm/data/devices.json — only has pixel7
- AVD executor's `_xml_to_text()` mimics Playwright aria_snapshot format so same LLM system prompt works for both platforms
- The `--server` mode is live at http://100.122.177.91:9876

## Files Touched This Session
- executors/__init__.py (created)
- executors/base.py (created)
- executors/web.py (created)
- executors/avd.py (created)
- executors/device.py (created)
- qa_agent.py (modified — executor interface, CLI flags, platform detection)
- projects.json (modified — added platform field, LilLearner entry)
- ~/.claude/skills/test-webapp/SKILL.md (rewritten)
- ~/.claude/skills/android-apk-testing/SKILL.md (rewritten)

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

## Deferred: Test Playlist Feature
- Run multiple tests sequentially from server UI
- `playlists.json` — saved playlist configs
- Dashboard playlist progress bar + per-test score cards
- POST /api/playlist/run — accepts playlist_id

## Environment
- PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright
- Server: `python3 qa_agent.py --server --dashboard-port 9876`
- Tailscale URL: http://100.122.177.91:9876
- GitHub: https://github.com/danman60/qa-agent
