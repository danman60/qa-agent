# CommandCentered — Audit Smoke Test (2026-05-05)

Post-audit smoke test. Goal: verify core dashboard pages load with no JS errors and that fix to /streaming/new dead link is in place. **DO NOT click any "Send", "Test send", "Fire now", or unsubscribe button. Do not edit or save any client. Do not submit any forms.**

## Auth
- [ ] Sign in completes and lands on dashboard. URL is /(dashboard) area.
- [ ] No console errors on login page or dashboard.

## Dashboard / Home
- [ ] Dashboard renders (cards, stats, no spinner stuck > 10s).
- [ ] Sidebar shows main nav: Planning, Communications, Clients, Files, Targeting, Live, Settings (some subset of these — list what you see).

## Communications — Pipeline tab (default)
- [ ] Visit /communications. Default tab is "Pipeline" (🧭 Pipeline).
- [ ] Matrix renders with rows = events, columns = stages. Or shows an empty state.
- [ ] Clicking a single matrix CELL opens a full-screen modal with template picker, datetime, skip toggle, preview iframe. Close it WITHOUT saving.
- [ ] Clicking an event card on the LEFT (event name area) opens a row drawer with thread + Compose. Close it WITHOUT sending anything.
- [ ] No "Test send" / "Send now" button is clicked.

## Communications — other tabs
- [ ] Click "Templates" tab — list of templates renders, no crash.
- [ ] Click "Suppressions" tab — table renders, no crash.
- [ ] Click "Activity" tab — log entries render, no crash.
- [ ] Click "All sends" tab — list renders.

## Planning / Events tab
- [ ] Visit /planning. Calendar (or events) renders.
- [ ] Visit /planning?tab=events. Table of events renders, header has a master checkbox, rows have per-row checkboxes.
- [ ] Table SCROLLS (it's overflow-auto). Scroll down and back up — no clipping.

## Clients
- [ ] Visit /clients. Client list renders, no BLACKLISTED clients visible by default.

## Streaming (DEAD LINK FIX VERIFY)
- [ ] Visit /streaming. The empty-state (or list) renders.
- [ ] If list is empty, the helper text says "Streams are created from an event's detail page in Planning" and "Planning" links to /planning. There is NO "Create your first stream" link to /streaming/new.
- [ ] Confirm /streaming/new is NOT accessible (visiting that URL should 404 or redirect; should NOT be linked from anywhere).

## Settings
- [ ] Visit /settings. Page renders.
- [ ] No console errors.

## Console / Network
- [ ] Throughout the run, capture any console errors (red messages only — yellow warnings OK).
- [ ] Capture any 4xx/5xx fetch responses except expected /api/auth probes.

## Final
- [ ] Confirm: zero emails sent, zero clients edited, zero forms submitted.
- [ ] Sign-out is OPTIONAL — leaving signed in is fine.
