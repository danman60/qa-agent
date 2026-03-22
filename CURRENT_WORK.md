# Current Work - qa-agent

## Active Task
Session wrapping up. Next priority: test playlist feature (run multiple tests sequentially from the UI).

## What Was Built This Session (2026-03-21 → 2026-03-22)

### Core Agent Improvements
- **Tighter verdicts** — fail-first logic, requires positive evidence for PASS (no more false passes)
- **Multi-action steps** — VERIFY action, LLM navigates→wait→verify before verdict
- **Page-aware navigation** — URL tracked after every action, logged on transitions
- **Screenshot thumbnails** — /screenshots/ route + clickable thumbnails in dashboard
- **Model comparison** — `--compare` flag runs same checklist with multiple models
- **Visual mode** — `--visual` flag: headed browser, 300ms slow_mo, red highlight flash on elements
- **SPA login detection** — handles apps that don't redirect after auth (sidebar appears, URL stays)
- **Login cascade** — skips all items when login fails (no more false passes on auth failure)
- **Self-learning gotcha system** — `gotchas.md` auto-learns fail phrases from false-pass analysis
- **Flow file executor** — parses /write-tests flow format (Step/Action/Expected/Verify) natively

### Dashboard
- **Dark mode redesign** — glassmorphism header, gradient shimmer progress bar, score ring SVG
- **Stats grid** — 7-column layout replacing cramped pills
- **Section headers** with pass/fail counts per section
- **Per-item meta row** — attempts, model time, page URL
- **Persistent server mode** (`--server`) — always-on dashboard with project picker
- **Test discovery** — scans project repos for flow files, spec files, TEST_PLAN.md
- **Project picker** — cards with test file dropdown, model selector, run button
- **History page** — past runs with scores and links
- **Run launcher** — POST /api/run starts tests from the UI

### Infrastructure
- **ollama-runner.py** — local LLM task runner with tool calls (read/write/edit/bash/grep)
- **NIM/Kimi K2.5 support** — `--provider nim` for cloud LLM (no GPU needed)
- **First successful Kimi spinoff** — flow executor built entirely by Kimi via ollama-runner
- **GPU auto-detection** — nvidia-smi check before spinoffs
- **Global CLAUDE.md** — skill gotcha lists mandatory for all 47 skills, local model routing commands

### Transcripts (16 total, sorted into 3 categories)
- **Sysadmin** (4): claude-code-self-learning, gotcha-list, top-3-plugins, minimax-benchmark
- **Founder Vision** (8): CLOSER, book-tiers, seamless-selling, menu-upsell, objections, elevator-pitch, making-money, confidence-stack
- **Amplify** (5): content-distribution, posting-schedule, viral-prompt, cult-following, vsl-thank-you-page

## Next Up: Test Playlist Feature

### What the user wants
- Run multiple tests sequentially (like a playlist)
- Configurable from the server UI — drag/drop ordering, add/remove tests
- Example: "Run CompPortal CD auth flow, then Studios flow, then Invoices flow, then Amplify auth flow"
- Each test in the playlist runs one after another, results accumulate
- Dashboard shows playlist progress (test 3/7, overall pass rate across all tests)

### Implementation ideas
- `playlists.json` — saved playlist configs: `[{project_id, checklist_path, name}, ...]`
- UI: playlist builder page at `/playlists` — add tests from any project's discovered test files
- Playlist runner: iterates through playlist items, runs each via run_agent(), collects all states
- Dashboard: playlist progress bar + per-test score cards
- POST /api/playlist/run — accepts playlist_id, runs all tests sequentially

## Architecture
- Single file: `qa_agent.py` (~1800 lines)
- `ollama-runner.py` — local LLM task executor
- `projects.json` — project configs with repo paths, test dirs, credentials
- `gotchas.md` — auto-learned fail phrases
- `checklists/` — flat checklist files
- Server mode: persistent HTTP on port 9876, Tailscale accessible at 100.122.177.91:9876
- UFW ports open: 9876 (QA dashboard), plus existing 3100, 11434, 50052

## Key Technical Decisions
- VERIFY action = LLM's way to confirm task completion (tight verdict applied)
- FAIL_PHRASES = module-level (26 base + learned gotchas), checked before PASS_PHRASES
- Flow files auto-detected by presence of "**Action:**" in text
- Dashboard uses inline CSS/HTML (no build step, no npm, pure Python)
- Server mode uses threading for background test runs
- ollama-runner uses tool_call_id for proper conversation threading

## Environment
- PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright
- Server: `python3 qa_agent.py --server --dashboard-port 9876` in tmux window QA-DASH2
- Tailscale URL: http://100.122.177.91:9876
- NIM API: moonshotai/kimi-k2.5 (free cloud, no GPU)
- FIRMAMENT ollama: qwen3-coder:30b (193 tok/s), qwen3.5:27b (41 tok/s)
- GitHub: https://github.com/danman60/qa-agent

## Context for Next Session
- The `--server` mode is live and working but needs the playlist feature
- Flow file parser works but some flow files have empty Expected/Verify fields — the LLM handles these OK
- ollama-runner.py hit NIM rate limit (429) after 31 tool calls — may need backoff/retry
- The overnight pass should focus on Founder Vision, not QA agent
- User wants coaching session tomorrow — web portal + Supabase sync + qwen3.5:27b swap
