# Closing portal (working name) — end-to-end walk (2026-11-13)

Live app: https://closing-portal.vercel.app . Synthetic data only (workshop tenant, demo accounts). Spec:
~/projects/realtor-sites/docs/plans/2026-11-13-gale-closing-app-spec.md (section 20 overrides).
Owner goal 2026-11-13 22:02 ET: "built to spec, polished, walked/tested e2e and beautifully animated/designed".

**Never type a real person's name, address, phone or email. Never paste a real Follow Up Boss key or a real
contract. Never press anything that would send to a real person (all sending is sandboxed; if a "send" appears,
note it and do not press it).** Run at 390 px and again at 1440 px.

## Realtor: sign in and desk
- [ ] Demo sign-in as a realtor works; lands on the desk. Signed-out visit to /desk redirects to /login?next=/desk.
- [ ] Desk lists deals by closing date; "Coming up" reminders list renders (or says to set reminder days).
- [ ] No console errors; no horizontal scroll; page animates in (and does not with ?anim=off).

## People, places, deals
- [ ] Create a property (synthetic street) and a person (synthetic name).
- [ ] New deal: that property + that person as Buyer. Double-click Save makes one deal.
- [ ] Second deal: another property, the SAME person as Seller. People page shows both roles in date order.
- [ ] Properties page shows each property's people over time.

## Conditions and dates
- [ ] On the deal: add a financing condition by hand with a deadline date and time; it shows days left.
- [ ] Add an inspection condition and a sale-of-buyer's-property condition.
- [ ] Edit one deadline twice; the history shows both changes.
- [ ] Shift: tick two dates, +3 days, preview shows old and new, apply; history shows one grouped shift; shift back.
- [ ] Mark the financing condition fulfilled with a date; status and history update.

## Contract scan (privacy)
- [ ] Upload the synthetic agreement fixture; the "Text sent to be read for dates" panel (the text that left this computer) shows names, addresses and phones blanked.
- [ ] Proposed conditions arrive unticked with type, party, date and quoted clause; confirm two; they appear on the deal.
- [ ] No vendor name appears anywhere (the scan panel names no model at all; only the reviewer Readiness page says "LLM model").
- [ ] An image-only PDF shows the plain "no readable text" message.

## Documents
- [ ] Realtor uploads a PDF on the deal; it lists and opens inline.
- [ ] A non-PDF/image file downloads as an attachment (never renders).
- [ ] Delete a document; it disappears and its old link gives not found.

## Client closing portal
- [ ] Realtor opens a portal link for the person; copy it.
- [ ] Open the link (390 px): the address bar ends at /p; the client sees the property, their role, days to closing,
      what is left, dates and conditions in order; no other person's name.
- [ ] Client uploads a PDF; realtor sees it on the deal.
- [ ] Client asks Sage "when is my financing deadline?"; the date matches the deal.
- [ ] Realtor switches the link off; client's next reload says the link is not active.

## Client health
- [ ] With no contact days set (realtor-b; realtor-a has them set): "Set your contact days". Set them; record contacts; bands show never / due / at risk / ok worst first.
- [ ] No send button anywhere on the health page.

## Arrange your screens (customizable lists, 2026-11-14)
- [ ] Desk, People, Properties, Client health, Members (reviewer): **Cards** / **Table** switch; Table shows the same rows; the page never scrolls sideways at 390 (the table scrolls inside its box).
- [ ] In Table, each column header sorts ascending, then descending, then back to the list's own order (header shows the arrow; aria-sort set); the order matches the data.
- [ ] The sort box next to Search sorts the cards; a reload keeps the table and the sort (they are in the page address).
- [ ] Search narrows the list; a search with no match says "No <things> match"; clearing it brings every row back.
- [ ] Deal page: the sort box above **Checklist** and above **Documents** reorders those rows.
- [ ] Desk: drag the grip next to **Your deals** onto **Coming up** (mouse), and again by keyboard (Space, arrow, Space) and touch (press 200 ms, drag); the order sticks after reload; another realtor's desk is unchanged; **Reset order** puts it back and then disappears.

## Sage (realtor)
- [ ] Ask "who haven't I contacted?" and "when is the inspection deadline on <synthetic street>?"; answers match the data.
- [ ] Ask about a person who is not yours; Sage has nothing on them.

## Connections and admin
- [ ] Follow Up Boss page says not registered yet (until registration exists).
- [ ] Billing page says not set up (until a Stripe test key and price exist).
- [ ] Reviewer: Members page adds and deactivates a realtor; readiness page lists every connection with a real result.
- [ ] Feedback widget button appears and opens; a test note "closing-portal e2e test, ignore" submits or shows the
      endpoint's refusal (the widget requires an email: use the synthetic e2e-test@closing-portal.test).
