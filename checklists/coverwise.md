# CoverWise — Full QA (2026-05-29 v2)

Internal broker tool. Carriers: Canada Life / Manulife / Sun Life. Demo client: James Carter.
Broker login: broker@coverwise.test. Demo share link: /q/demo-james-carter-001.

CRITICAL ORDERING RULE: You start LOGGED IN on /dashboard. Do NOT log out, and do NOT click any "Log out" button, until the final "Logged-out checks" section. All broker-dashboard checks assume you remain logged in. If you ever land on /login mid-run, navigate back to /dashboard (the session cookie is still valid) rather than re-typing credentials.

Do NOT delete carriers or rating rules. Do NOT change a rating_rule's factors JSON. Safe to drag sliders, add a test client, generate the letter.

## Broker dashboard (stay logged in)
- [ ] On /dashboard: WFG blue header with logo + "CoverWise" wordmark, client switcher showing "James Carter". No console errors.
- [ ] Total premium shows ~$252/mo and a monthly/annual toggle. Toggle to Annual — number scales ~×12, then back to Monthly.
- [ ] Premium-split donut/pie renders with Life / Critical Illness / Disability segments + a legend with $ and %.
- [ ] Three coverage sliders (Life ~$500,000, Critical Illness ~$100,000, Disability ~$4,000/mo). Each shows a green "ideal zone" band + a status pill (On target / Underinsured / Above).
- [ ] Drag the Life slider up — Life $ rises, total premium rises, pie updates live, carrier card prices update.
- [ ] Coverage lines have option dropdowns (Life term, CI term, Disability waiting/benefit/own-occupation). Change Life term to "Whole life" — premium jumps up. Set back to 20-year.
- [ ] Options have an ⓘ icon; hovering shows a plain-language explainer tooltip.
- [ ] Riders panel lists 5 riders as checkboxes + a flat "Policy fee" line. Tick "Indexation (COLA)" — total premium rises. Untick it.
- [ ] Three carrier cards (Canada Life, Manulife, Sun Life), each with a live premium + feature text. Two show "+$X vs lowest"; the lowest shows "Lowest for this coverage".
- [ ] Click the Sun Life card — it becomes primary (highlighted + "Primary" tag), totals recompute.
- [ ] "Copy live link" button works (a toast appears).

## Reason-Why letter (broker-only, stay logged in)
- [ ] Click "Generate letter" — a letter renders addressed "To: <carrier> — New Business / Underwriting", applicant James Carter, a coverage breakdown by line, a total premium, and a "REASON FOR RECOMMENDATION" section.
- [ ] Letter names the OTHER two carriers as alternatives reviewed.
- [ ] "PDF" button opens the browser print dialog (cancel it).

## Add a client (stay logged in)
- [ ] Use the client switcher "Add client" — add "QA Test Client", age 38, income 120000. Dashboard switches to the new client with default coverage. No console errors.

## Carrier admin (stay logged in)
- [ ] Visit /carriers. Three carriers listed, each with products (Life/CI/Disability), base rate, key features, no-medical max, source URL, version.
- [ ] The "Ingest from carrier rate page / upload PDF" control exists and shows a message that ingestion is offline/batch (phase 2) — does NOT perform a live extraction. Do NOT save a rule edit.

## Settings (stay logged in)
- [ ] Visit /settings — firm name and broker profile shown; a logout control is present (do not click it yet).

## Insured self-serve view (uses a share link, not your session)
- [ ] Open /q/demo-james-carter-001. Phone-framed view: WFG header, "Set up by your advisor", "Hi James", a big premium (~$252/mo), and a pulsing "Live — your advisor sees your changes in real time" banner.
- [ ] Three coverage sliders + term/waiting selects, a donut split, and 3 tap-to-choose carrier cards. Drag the Life slider — premium + donut + carrier prices update live. Profile fields are NOT editable here. No console errors.
- [ ] Open /q/INVALID-TOKEN-123 — shows a friendly "link no longer valid" page, NOT another client's data and NOT a crash.

## Logged-out checks (LAST — only now may you log out)
- [ ] Visit / (landing) — WFG-branded hero loads, CTAs to Sign up / Log in present, no console errors.
- [ ] Click "Log out" (from /settings or the dashboard). Then visit /dashboard while logged out — you are redirected to /login.
