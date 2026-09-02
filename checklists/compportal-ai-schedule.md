# CompPortal — AI Auto-Schedule Multi-Day Test (demo)

Target: https://demo.compsync.net (demo tenant; human_like_scheduler ON). CD login testcd@compsync.net / 1CompSyncLogin! at /login.
Live demo only. Screenshot every step.

## CRITICAL SETUP — select the right competition FIRST
The scheduler defaults to an EMPTY competition ("Showcase Fall 2026", 0 routines). You MUST switch to the one with routines or every later check is meaningless.

- [ ] 1. Log in as CD. Go to the scheduler page (Director panel → Schedule, i.e. /dashboard/director-panel/schedule).
- [ ] 2. Find the competition selector dropdown near the top of the scheduler page (it currently shows a competition name like "Showcase Fall 2026"). Click it to open it. Screenshot the open dropdown showing the list of competitions.
- [ ] 3. From that dropdown, select **"Pinnacle Dance Classic Spring 2026"** (the 4-day April 9–12 competition with ~1448 unscheduled routines). Confirm the page now shows that competition name and a large unscheduled-routines count (NOT "All routines scheduled!"). Screenshot. If you cannot open or select from this dropdown after a few tries, record EXACTLY what you clicked and what happened — that is itself a finding.

## CORE CHECK — multi-day distribution (the bug being verified)
- [ ] 4. Open the "Auto-Schedule" action/modal for Pinnacle. Choose "Entire competition (all days)". Trigger the preview. Screenshot the preview.
- [ ] 5. Read the per-day routine counts in the preview for ALL FOUR days: Thursday Apr 9, Friday Apr 10, Saturday Apr 11, Sunday Apr 12. Record the number for each day.
- [ ] 6. VERDICT: PASS if all 4 days have a substantial share (~300–400 each; ~1448/4 ≈ 362). FAIL if one day has ~everything (e.g. ~1448) and others ~0. State the four numbers and the verdict explicitly.
- [ ] 7. Record "Routines Placed" total (≈1448–1470) and "Unplaced/overflow" (should be 0).
- [ ] 8. For each day, note the Lunch position — it should be mid-day within that day's count (e.g. lunch after ~80–180), NOT a huge number like 1241.

## Preferences page
- [ ] 9. Go to /dashboard/scheduler/preferences. Screenshot. Confirm it renders dark/legible with visible toggles and sliders (no white-on-white).

## Console
- [ ] 10. Note any uncaught console errors or 500s seen during the flow (state "none seen" if clean).
