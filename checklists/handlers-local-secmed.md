# Handlers (CONTROL) — local-stack game run after secmed migration 002

LOCAL ONLY: app http://localhost:3917 on a local Supabase stack. Two helper players (BOTA, BOTB) join
any new lobby automatically within ~3 s. You play the host in the browser. The point: the browser no
longer reads the room tables directly, so the lobby, round and end screens must still update live.

## 1. Create operation `/`
- [ ] Page loads with the CONTROL title and buttons "New Operation" and "Join Operation". No console errors.
- [ ] Click "New Operation". Type `HOST` in the YOUR NAME box. Click "Open Channel".
- [ ] URL changes to `/room/<4-letter code>` and a large 4-letter room code is visible.

## 2. Lobby updates live
- [ ] Wait up to 15 seconds without reloading. The player list shows HOST, BOTA and BOTB (3 players).
- [ ] The start button now reads "Begin Operation" (not "Need N more") and is enabled.

## 3. Round starts
- [ ] Click "Begin Operation". Within 15 seconds, without reloading, the screen changes away from the
      lobby (a "SECURE TRANSMISSION INBOUND" reveal screen or an "Eyes Only" mission card appears).
- [ ] Wait up to 20 seconds. A screen with a "mission clock" countdown or a mission card is visible.
      Note exactly what text is on screen.

## 4. Finish round
- [ ] If a "Mission Complete" button is visible, click it. Within 20 seconds the screen shows a result or
      debrief, or a "Next Round"/"End Operation" button. If no such button appears, record what is on screen.
- [ ] If an "End Operation" button is visible, click it. Within 15 seconds "Operation Concluded" and
      "final standings" with 3 names appear.

## 5. Reload keeps state
- [ ] Reload the page. The same room screen comes back (not the home menu) and shows the same room state.
- [ ] Report every console error seen during the whole run, verbatim.
