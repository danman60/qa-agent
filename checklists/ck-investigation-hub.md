# CK Investigation Hub — QA checklist

Public research site for the UT v. Robinson corpus. Read-only; there is nothing to log into and
nothing to submit. The point of this pass is that **every page loads, every claim on screen has its
source visible, and the honesty markers survive**. A page that renders beautifully but drops its
"this is a claim, not a finding" caveat is a FAIL, not a nit.

## 1. Dashboard `/`
- [ ] Page loads, no console errors.
- [ ] Two front-door cards are present: **Court Record** and **Parallax**. Both link correctly.
- [ ] Stats cards render numbers (not `NaN`, not blank, not `0` across the board).

## 2. Court Record `/court`
- [ ] Loads. Header states 55 transcripts.
- [ ] The amber "Read this before quoting anything" box is present and contains all three warnings:
      ASR captions · a preliminary hearing is one-sided · a question is not evidence.
- [ ] Five tier cards render with counts: Sworn testimony 5 · Hearing/ruling 18 ·
      Official statement 5 · Derived clip 11 · Commentary 16.
- [ ] Tier filter pills work — clicking "Sworn testimony" narrows the list to 5 rows.
- [ ] Search `southwest corner` → returns ~7 matches. **Verify the same sentence appears under both
      a SWORN TESTIMONY badge and a DERIVED CLIP badge** — that contrast is the feature, not a bug.
- [ ] Search `casings` returns results.
- [ ] Search a 2-character string → no crash; it should refuse politely.
- [ ] Search gibberish (`zzzqqq`) → "Nothing" message with the ASR-variant hint, not an error.
- [ ] Clicking a search result opens the reader **scrolled to that line, highlighted**.

## 3. Transcript reader `/court/[vid]`
Test at least one of each: a full hearing day, a derived clip, a press conference.
- [ ] Loads. Tier badge + "ASR — live captions" badge both visible.
- [ ] Caption lines render with timestamps.
- [ ] A timestamp link opens YouTube at that second (check the `&t=` param is present and non-zero).
- [ ] `❯❯` speaker-change markers are visible on some lines.
- [ ] On a **derived clip** (`/court/C1jl4sdqQTs`): the purple box names its parent
      ("excerpt cut from Preliminary hearing day 1") and links to the full proceeding.
- [ ] On `/court/nWCDVkE6ccM` (parent unresolved): the amber box says the parent could **not** be
      identified. It must NOT claim a parent.
- [ ] In-transcript filter box narrows the lines and shows an "N of M lines" count.
- [ ] Bad id `/court/notarealid` → 404, not a 500.

## 4. Parallax `/parallax`
- [ ] Page loads with hub chrome; the scene iframe renders (dark WebGL canvas, not blank white).
- [ ] "Open full screen" link works.
- [ ] Tier legend (State witness / Host claim / Unresolved) is present below the scene.
- [ ] The "What is real and what is not" table is present and includes the row saying building
      **heights are estimated**.
- [ ] Inside the scene: give it ~15s, then confirm the "COMPUTING VISIBILITY FIELD" overlay has gone
      and the SIGHTLINES layer count shows a number, not a dash.
- [ ] Click LOSEE / SORENSEN / DIFF / SOLVE — the legend text changes for each.
- [ ] DIFF reports roughly: Losee-only 428, Sorensen-only 189, both 2.
- [ ] Click each of the three claimed positions — the right inspector panel changes.
- [ ] The Sorensen panel must say there is **no source in the hearing record** for it.
- [ ] The Tent panel must say the position is **unresolved / no coordinate in any source**.
- [ ] The amber banner top-right of the scene mentions heights being estimates.

## 5. Regression — pre-existing pages must still work
- [ ] `/timeline`, `/witness-timeline`, `/contradictions`, `/dropped-threads`, `/entities`,
      `/episodes`, `/search`, `/flights`, `/tips`, `/statements`, `/research`, `/brief` all load
      without a 500.

## 6. Responsive
- [ ] At 390×844 the nav collapses to a hamburger and opens.
- [ ] `/court` list and search results are readable; no horizontal page scroll.
- [ ] `/parallax` — the scene is small on mobile, which is expected. The explanatory content below
      it must still be readable, and the page must not scroll sideways.

## Known and intentional — do NOT report these as bugs
- Host timeline lanes in Parallax (Baron / Ian / Candace) are **placeholder density**, labelled as
  such on screen. Not yet bound to real data.
- Court captions are ASR and imperfect. That is the subject of the warnings, not a defect.
- Building heights in the 3D scene are estimates. Labelled.
- The scene takes several seconds to compute the visibility field on load.
