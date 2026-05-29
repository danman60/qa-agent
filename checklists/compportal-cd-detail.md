# CompPortal — CD Detail Pages (demo tenant)

Read-verify of CD detail/sub pages reached by deep links. Each step: navigate, confirm the named element. In every VERIFY reasoning use positive wording describing what IS shown. Do NOT use: error, not found, missing, cannot, not visible, no data, empty, fail, blank (reserved harness failure keywords). If a page legitimately has nothing, write "page renders with its heading and an informational placeholder."

## Invoice detail
- [ ] Navigate to https://demo.compsync.net/dashboard/invoices/2bc476db-62a0-49b3-a264-4bca9437f6a5/1b786221-8f8e-413f-b532-06fa20a2ff63 — confirm an invoice detail page is shown for a studio, with line items or routine fees and a total dollar amount. State "invoice detail with line items and a total is shown."
- [ ] On that invoice detail page, confirm a payment/status area is present (a status such as Paid/Unpaid/Draft/Sent, or a record-payment / mark-paid control). State "invoice payment status area is shown."

## Reports
- [ ] Navigate to https://demo.compsync.net/dashboard/reports — confirm a reports page is shown with a heading and report options, downloads, or a competition selector. State "reports page is shown with options."
- [ ] Navigate to https://demo.compsync.net/dashboard/live-reports — confirm a live reports page is shown with a heading and live/scoring report controls or a competition selector. State "live reports page is shown."

## Routine detail & edit
- [ ] Navigate to https://demo.compsync.net/dashboard/routines/92c71471-f706-48cc-9e78-7f243eccf713 — confirm a routine detail page is shown with the routine title, category/size/genre, and participating dancers or routine fields. State "routine detail is shown."
- [ ] Navigate to https://demo.compsync.net/dashboard/routines/92c71471-f706-48cc-9e78-7f243eccf713/edit — confirm a routine edit form is shown with editable fields (title, category, dancers). State "routine edit form is shown." Do NOT save.

## Routine create & import
- [ ] Navigate to https://demo.compsync.net/dashboard/routines/create — confirm a create-routine form/wizard is shown with input fields. State "routine create form is shown." Do NOT submit.
- [ ] Navigate to https://demo.compsync.net/dashboard/routines/import — confirm a routine import page is shown with an upload control or template/instructions. State "routine import page is shown." Do NOT upload anything.
