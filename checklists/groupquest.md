# GroupQuest — QA checklist (demo phase)

Group trip planner around an event (Gen Con). Three demo roles exist on `/login` as one-click buttons:
**"Sign in as host (demo)"**, **"Sign in as member (demo)"**, **"Sign in as producer (demo)"**. Match the button
name EXACTLY (exact=True); the three names share a prefix. The seeded quest is `/q/gencon-road-trip`.
Every write below must show up on screen after the form submits; a form that clears but shows nothing new is a FAIL.

## 1. Logged out
- [ ] `/` loads, no console errors. There is a visible way to sign in.
- [ ] `/q` redirects to `/login` (signed out).
- [ ] `/q/gencon-road-trip` redirects to `/login`.
- [ ] `/p/gencon` redirects to `/login`.
- [ ] `/nope-not-a-page` renders the app's 404, not a stack trace.
- [ ] `/legal/privacy` and `/legal/terms` render text.

## 2. Host
- [ ] Click "Sign in as host (demo)" → lands on `/q` with "Your quests" and the card "Gen Con road trip".
- [ ] Start a quest: name `QA quest <timestamp>`, origin `Buffalo, NY`, click "Start quest" → a new quest card appears.
- [ ] Open `/q/gencon-road-trip` (the board). Readiness bar, "Open" cards and "Add a card" form are visible.
- [ ] Add a card: title `QA card`, XP 15, click "Add card" → `QA card` appears under Open and in "Your moves".
- [ ] In "Your moves", click "Claim" on `QA card` → button becomes "Mark done". Click it → card leaves Open; readiness % rises.
- [ ] `/q/gencon-road-trip/map`: add a car `QA van`, seats 4 → it appears in the Vehicle select of "Add a leg".
- [ ] Add a leg: direction There, origin `Toronto, ON`, destination `Indianapolis, IN`, pick a depart time, vehicle `QA van`, tick a rider → a leg card appears (distance may say pending; that is OK).
- [ ] `/q/gencon-road-trip/places`: propose `QA Diner`, kind Restaurant → appears under the Restaurants tab.
- [ ] Vote up on `QA Diner` → the count changes. Click lock (host only) → it shows as locked.
- [ ] `/q/gencon-road-trip/stays`: add a stay for a hotel (propose a hotel first under Places if none), check-in/out dates, nightly 189 → stay card shows $189.
- [ ] `/q/gencon-road-trip/ledger`: log expense memo `QA fuel`, amount 40, category Fuel, Paid by shows a NAME (not an id), both members ticked → expense row appears, balances update, totals are dollars not cents.
- [ ] `/q/gencon-road-trip/party`: save character name `QA Knight`, class Paladin → sheet shows `QA Knight`. Save profile display name unchanged → "Saved" message, no error.
- [ ] `/q/gencon-road-trip/campfire`: post `QA message` → appears in the thread. Pin it → it shows pinned.
- [ ] `/q/gencon-road-trip/invite`: create an invite → a `/join/<code>` link appears. COPY THE CODE for section 3. Revoke a different invite if one exists → it is marked revoked.
- [ ] Console: no errors on any of the pages above.

## 3. Member
- [ ] Sign out (or new context). Click "Sign in as member (demo)" → `/q` shows "Gen Con road trip".
- [ ] `/q/gencon-road-trip/invite` → NOT available to a member (redirect or 404, not the host's invite list).
- [ ] `/q/gencon-road-trip/places`: vote on `QA Diner` works; there is NO lock control for a member.
- [ ] `/q/gencon-road-trip/campfire`: the host's `QA message` is visible; post `QA reply` → appears.
- [ ] Open `/join/<code>` from section 3 while signed in as member → page says already a member or joins without error.

## 4. Producer
- [ ] Click "Sign in as producer (demo)" → `/p/gencon` loads with four stat tiles (Quests ≥ 1).
- [ ] Save an announcement titled `QA notice`, Publish now ticked → appears in the Announcements list.
- [ ] Save a sponsor `QA Brewing`, website field set to example dot com with the https scheme (type it, do not open it), blurb under 140 chars → appears under Sponsors.
- [ ] Blurb counter: typing shows `n / 140`.
- [ ] `/q/gencon-road-trip` as producer → redirect to `/q` (producer is not a quest member) — no crash.

## 5. Break it
- [ ] Submit "Add a card" with an empty title → inline error or browser validation, no crash.
- [ ] Log an expense with amount `-5` → rejected.
- [ ] Double-click "Log expense" fast → only ONE row appears.
- [ ] Refresh the ledger after submitting → the row persists.
- [ ] Phone width 390: board, ledger, places, party — nothing scrolls sideways, bottom tab bar visible.

## 6. Added 2026-10-20 (portraits, limits, activation)
- [ ] Host: `/q/gencon-road-trip/party` shows a selfie upload and a "Request portrait" control on the host's own sheet; before any upload the sheet shows initials and an instruction, not a broken image.
- [ ] Upload a non-image file (e.g. a .txt renamed .jpg) → inline error, no crash. Upload a real small JPG → it is accepted and shown.
- [ ] Member: cannot change the host's portrait (no control on another member's sheet).
- [ ] Signed out: opening a portrait image URL from the party page → redirect or 401/403, never the image.
- [ ] Rate limit: post 12 campfire messages fast → after the limit an inline "slow down" style error appears and no further rows; the earlier messages remain.
- [ ] Rate limit: create invites repeatedly → same behaviour; existing invites intact.
- [ ] Activation: host starts a new quest → the board shows what to do next (invite, first card) with a working link to Invite.
- [ ] Activation: member opens a `/join/<code>` link → lands on that quest's board with an empty state that says what to do.
- [ ] `/` and `/login`: no filler copy ("Lorem", "Your tagline here"); any unknown facts appear as `[GroupQuest to supply]`, not invented numbers or dates.
- [ ] Console clean on party, campfire, invite and the landing page.
