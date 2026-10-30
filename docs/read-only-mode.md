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

## Default on for non-localhost sites (02:39 ET)
Without flags, any web page whose host is not localhost/127.0.0.1/::1/*.localhost is guarded. `--allow-writes` (CLI)
or `"allow_writes": true` on a projects.json entry (server mode) turns it off; `--read-only` guards even localhost.
Login (`do_login`) bypasses the guard, so credentialed runs still sign in. Android/device executors are unaffected.

Check (no model, decisions only, nothing clicked): localhost FILL allowed; localhost --read-only FILL refused; on
askwoodstock.vercel.app FILL and CLICK "Ask" refused, header link allowed; --allow-writes allows FILL; no-page executor
not guarded. 7/7 pass.

Cost on merge: remote web projects in this branch's projects.json whose checklists type or submit need
`allow_writes: true` (or --allow-writes) — candidates: 21 compportal-cd compportal-sd compportal-sd-app compportal-prod amplify ddd studiosage lillearner-web memorialwise colourbook carly-hair-co tvbox-admin red-sky recital-scheduler unison bask-prod floortime studioshift-prod household studioshift-prod-shift groupquest-local

## Opt-outs pre-set (02:57 ET)
15 remote web projects get `"allow_writes": true` so their existing checklists keep typing and submitting:
compportal-cd, compportal-sd, compportal-sd-app, amplify, ddd, studiosage, lillearner-web, memorialwise, carly-hair-co,
tvbox-admin, red-sky, recital-scheduler, unison, floortime, household.
Left guarded (their projects.json name says Prod): compportal-prod, colourbook, bask-prod, studioshift-prod,
studioshift-prod-shift, groupquest-local ("GroupQuest (prod)"). Their checklists hold write steps (34, 2, 19, 39, 8, 39
write-verb matches), so in server mode those runs will refuse typing until the owner adds `allow_writes` to each.
The split follows only the owner's own "Prod" labels; any line can be flipped. The live checkout's uncommitted
projects.json additions are all localhost and unaffected.

## Network layer (02:58 ET)
Read-only runs also abort every non-GET/HEAD/OPTIONS request the page sends (`block_write_requests`, a context route
installed on the first checklist action, after login). The action that triggered it fails with
"read-only run: blocked N request(s): POST ...". A run with writes allowed installs no route.
Check (real WebExecutor, local server): a menu control whose click fires `fetch(POST /track)` opened, the POST was
blocked and reported, the link after it worked, and the server log shows only `GET /` and `GET /next.html`. With
QA_ALLOW_WRITES=1 the same click reaches the server (`POST /track`). Limits: GET requests that change server state, and
requests sent by a service worker, are not intercepted.

## Codex review fixes (03:00 ET)
xreview (Codex, 02:59 ET) verdict FIX FIRST, 4 findings, all addressed:
1. Network block now installed right after the executor starts; login POSTs pass only inside `executor._auth_window`
   around `do_login` (any POST during that window passes, not only the auth call).
2. CLICK on a link, or NAVIGATE, whose address matches delete/remove/destroy/logout/signout/unsubscribe/cancel/approve/
   reject/confirm/revoke/archive/reset is refused. Shortcut: other mutating GET addresses and redirects still pass.
3. Requests are judged by their destination host: a localhost page POSTing to a remote host is blocked by default.
4. Browser contexts block service workers unless writes are allowed for the whole run.
Checks (real WebExecutor, servers on 127.0.0.1 and 127.0.0.2 as "remote"): localhost page POST to remote blocked and
reported, remote server log empty; delete link and NAVIGATE refused, plain link allowed; POST in the login window
reached the remote server; with block mode the worker script was never fetched (0 workers) while allow mode fetched it
(1 worker).

## Second Codex review (03:00 ET review, fixes 03:15 ET)
`~/.claude/xreviews/20261030-030049-qa-agent-readonly-3803815.md`, 3 findings:
1. Auth window too broad: fixed. Inside the login window a POST passes only to the login page's host or the
   Supabase URL (`executor._auth_hosts`). Check: POST to a non-auth host inside the window blocked; to the auth host
   passed; after the window blocked. Remote server log: only `POST /track?auth-host`.
2. Mutating-address check is a denylist, not an allowlist: not fixed, documented limit. A GET link with an innocent
   name that changes server state still passes. The network block (non-GET) is the real guard.
3. WebSockets not intercepted: fixed. `context.route_web_socket` refuses guarded sockets (never connects them;
   `ws.close()` in the sync handler deadlocks, proven by traceback). Check: page opened `ws://127.0.0.2:48644`,
   recorded as `WEBSOCKET ...`, raw listener logged 0 connects; with QA_ALLOW_WRITES=1 it logged 1 connect.
