# --read-only (2026-10-30, fable-historyq)

Why: a checklist that said "never type, never press Ask" still had qwen3-coder:30b fill and press Ask on
askwoodstock.vercel.app (prod) at 01:53 ET. Prompts do not hold; the harness must.

`--read-only` sets `QA_READ_ONLY=1`; `execute_action` then refuses FILL, TYPE, SELECT, UPLOAD, any key except
navigation keys (Escape, Tab, arrows, Page, Home, End, Back), and any CLICK whose target is inside a form or is not a
plain link, `<summary>`, tab/menuitem, or an `aria-expanded`/`aria-haspopup` control. NAVIGATE (GET) and SCROLL pass.

Evidence: local page with a GET form (Send), a fetch() button (Save), a disclosure menu and a link. Run 2: FILL, TYPE,
Send, Save, Enter refused; menu and link PASS; the server log shows only `GET /` and `GET /next.html`.
Run 1 lost its browser page mid-item (cause not found; run 2 identical inputs was clean).

Known limit: a link (GET) that changes state on the server, or a menu control with side effects, still passes.

Merge note: the live checkout (`~/projects/qa-agent` master) has uncommitted edits from 2026-09. This patch applies to
it with hunks 1-2 clean and hunk 3 (the `if args.read_only:` lines after `args = parser.parse_args()`) by hand.
