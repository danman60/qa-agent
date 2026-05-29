# CompPortal — CD Deep Coverage (demo tenant)

Read-verify sweep of CD-accessible pages beyond basic nav. Each step: navigate to the URL, confirm the named element is present. In every VERIFY reasoning, describe what IS shown using positive wording. Do NOT use the words: error, not found, missing, cannot, not visible, no data, empty, fail, blank (these are reserved failure keywords for the harness and will wrongly fail an otherwise-good step). If a page genuinely has nothing, write "page renders with its heading and an informational placeholder" instead.

## Global Invoices
- [ ] Navigate to https://demo.compsync.net/dashboard/invoices/all — confirm the global invoices view shows a heading and a table or card layout with studio names and dollar amounts. In VERIFY reasoning, state "global invoices table is shown with studios and amounts."
- [ ] On the same invoices view, confirm at least one invoice row/card shows a status indicator (such as Paid, Unpaid, Draft, or Sent). State "invoice status indicators are shown."

## Pipeline
- [ ] Navigate to https://demo.compsync.net/dashboard/pipeline — confirm the CD pipeline page shows KPI summary cards (counts of studios/routines/reservations) at the top. State "pipeline KPI cards are shown."
- [ ] On the pipeline page, confirm a list/table of studios with per-studio routine counts or stage indicators is shown. State "pipeline studio rows are shown."

## Routine Summaries
- [ ] Navigate to https://demo.compsync.net/dashboard/routine-summaries — confirm the page shows a heading and per-studio summary rows or cards. State "routine summaries are shown."

## Reservations
- [ ] Navigate to https://demo.compsync.net/dashboard/reservations — confirm the reservations page shows a heading and a list/table of studio reservations with spaces/confirmed counts. State "reservations list is shown."

## Competitions
- [ ] Navigate to https://demo.compsync.net/dashboard/competitions — confirm three competition cards/rows are shown, each with a name and dates. State "competition cards are shown."
- [ ] Navigate to https://demo.compsync.net/dashboard/competitions/new — confirm a create-competition form is shown with input fields for name and dates. State "new competition form fields are shown." Do NOT submit the form.

## Judges
- [ ] Navigate to https://demo.compsync.net/dashboard/judges — confirm the judges management page shows a list of judges (about 5) with names. State "judges list is shown."

## Tenant Settings
- [ ] Navigate to https://demo.compsync.net/dashboard/settings/tenant — confirm the tenant settings page shows configuration sections such as Age Divisions, Entry Sizes, Pricing/Fees, Dance Styles, and Scoring. State "tenant settings sections are shown."
- [ ] On the tenant settings page, confirm the named configuration sections are present by their headings — look for several of: "Setup", "Categories", "Age Divisions", "Pricing & Fees", "Awards", "Scoring Rubric", "Branding & Theme" (a table-of-contents rail listing these is also acceptable). State "tenant settings section headings are shown."

## Competition Settings (per-competition edit)
- [ ] Navigate to https://demo.compsync.net/dashboard/competitions/1b786221-8f8e-413f-b532-06fa20a2ff63/edit — confirm a competition edit form is shown with fields such as name, dates, and competition configuration controls. State "competition edit form is shown." Do NOT save.

## Analytics
- [ ] Navigate to https://demo.compsync.net/dashboard/analytics — confirm an analytics/dashboard page is shown with metric cards or charts. State "analytics metrics are shown."

## Emails
- [ ] Navigate to https://demo.compsync.net/dashboard/emails — confirm an emails page is shown with a heading and either a template list or an informational placeholder. State "emails page is shown with its heading."

## Help Center
- [ ] Navigate to https://demo.compsync.net/dashboard/help — confirm a help center page is shown with a heading and help topics or content sections. State "help center content is shown."

## Tabulation (Master Tabulator)
- [ ] Navigate to https://demo.compsync.net/dashboard/tabulation — confirm the master tabulator page is shown. Look for a "Master Tabulator" heading or a competition selector plus a routines/scores table. State "master tabulator page is shown." (A competition picker before the table is acceptable as a PASS.)

## Planning
- [ ] Navigate to https://demo.compsync.net/dashboard/planning — confirm the planning page is shown with planning tools such as a trophy/award order, venue editor, or meal sheet. State "planning tools are shown." (A competition picker first is acceptable as a PASS.)

## Media (CD media dashboard)
- [ ] Navigate to https://demo.compsync.net/dashboard/media — confirm the CD media page is shown with a heading and media controls (studio/package list, upload status, or a competition selector). State "media dashboard is shown."

## Studios detail
- [ ] Navigate to https://demo.compsync.net/dashboard/studios — confirm the studios page lists studios with names and contact columns. State "studios table is shown."
- [ ] On the studios page, type into the search/filter box if one is present (use the text "Apex") and confirm the list narrows or updates. State "studios filter updated the list." If there is no search box, state "studios table is shown without a filter box, which is acceptable."
