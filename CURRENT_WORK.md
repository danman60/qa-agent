# Current Work - qa-agent

## Active Task
All 5 improvements implemented and committed.

## What Was Built (2026-03-21)
- **QA Agent v1** clean rewrite (Playwright Python API, single file)
- **Design pass** (glassmorphism stats, card animations, JetBrains Mono, mobile responsive)
- **5 Improvements** (v2):
  1. Tighter verdicts — fail-first logic, requires positive evidence for PASS
  2. Multi-action steps — VERIFY action, LLM can navigate→wait→verify before verdict
  3. Page-aware navigation — URL tracked after every action, logged on transitions
  4. Screenshot thumbnails — /screenshots/ route + clickable thumbnails in dashboard
  5. Model comparison — --compare flag runs same checklist with multiple models

## Architecture
- Harness = deterministic state machine (owns snapshots, verdicts, login, console, network)
- LLM = brain only (picks elements by role+name from aria_snapshot())
- VERIFY action = LLM's way to say "I've confirmed the task" (tight verdict applied)
- NONE action with no positive evidence = FAIL (no more false passes)
- Dashboard serves screenshots at /screenshots/<filename>

## Key Files
- `qa_agent.py` — the full agent (~1350 lines, single file)
- `checklists/compportal-cd.md` — real 25-item checklist
- `tests/reports/` — all test run reports

## Next Steps
- Run against CompPortal CD checklist to validate tighter verdicts catch false passes
- Test --compare with ollama vs nim
- Consider adding WAIT action for explicit page load waits
