# CompPortal — Competition Director Testing

## Login & Dashboard
- [ ] Verify you are logged in and on the dashboard (do NOT sign out or navigate to login page — confirm authentication succeeded by checking current page content)
- [ ] Dashboard shows after login — verify widgets, stats, or welcome message
- [ ] Dashboard sidebar has navigation links for all major sections

## Studios
- [ ] Click Studios in sidebar — page loads with a table or list of studios
- [ ] Studios table shows studio names, contact info, or status columns
- [ ] Try clicking on a studio row to see details

## Routines & Entries
- [ ] Click Routines in sidebar — page loads with routines list
- [ ] Routines show entry names, dance styles, or categories
- [ ] Try filtering or searching if a search bar is visible

## Invoices
- [ ] Click Invoices in sidebar — page loads with invoice list
- [ ] Invoices show amounts, statuses (paid/unpaid), and dates
- [ ] Try clicking an invoice to see detail view

## Scheduling
- [ ] Click Scheduler in sidebar — page loads
- [ ] Check if schedule shows time slots, events, or a calendar view
- [ ] Check for any modal or dialog that appears on load

## Events & Competitions
- [ ] Click Events in sidebar — page loads with competition list
- [ ] Events show competition names and dates

## Pipeline
- [ ] Click Pipeline in sidebar — page loads
- [ ] Pipeline shows stages or workflow status

## Judges
- [ ] Click Judges in sidebar — page loads with judge list or management view

## Settings & Admin
- [ ] Look for Settings or Admin link in sidebar
- [ ] Click it and verify settings page loads with configuration options

## Error & Edge Cases
- [ ] Navigate to the single URL https://demo.compsync.net/does-not-exist-12345 (one navigation only, do NOT try other URLs) — a fallback page with a "Go Home" button is EXPECTED here and counts as a PASS. Confirm success ONLY by the presence of the "Go Home" link/button. CRITICAL: in your VERIFY reasoning write EXACTLY this sentence and nothing else: "Go Home button is present and the page rendered." Do NOT quote any page heading text. Do NOT use any of these reserved words/phrases (they auto-fail the step): error, not found, missing, empty, fail, no data, not visible, cannot.
- [ ] Navigate directly to https://demo.compsync.net/dashboard (one navigation) and confirm the Competition Director dashboard loads. In your VERIFY reasoning write EXACTLY: "dashboard is shown." Do NOT use any of: error, missing, cannot, not visible, no.
- [ ] In the left sidebar (scroll to the bottom of the sidebar if needed), confirm a "Sign Out" button/link is present. Do NOT actually click it — stay logged in. In your VERIFY reasoning write EXACTLY: "Sign Out button is present in the sidebar." Do NOT use any of: cannot, not visible, missing, no, error.
