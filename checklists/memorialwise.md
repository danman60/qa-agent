# MemorialWise — Vacation Albums E2E

Tests both legacy memorial flow and the new vacation/event/wedding/reunion/other album types, plus phase 2.5/2.75/3/4/5 features (photo interactions, map tab, share routes).

## Auth & Dashboard
- [ ] Verify you are logged in and on the dashboard (do NOT sign out — confirm by checking current page content)
- [ ] Dashboard shows "Your Albums" header (not "Your Memorials" — copy was generalized in phase 2)
- [ ] Existing seed memorial "Margaret Thompson" appears in the album grid with a Memorial type pill
- [ ] Dashboard "Create New" button opens an album type picker dialog (NOT a direct create form)

## Album Type Picker (phase 2)
- [ ] Type picker dialog shows 6 cards: Memorial, Vacation Album, Event, Wedding, Reunion, Album (other)
- [ ] Each card shows an icon (heart, mappin, star, gift, users, sparkles)
- [ ] Each card shows a one-line description
- [ ] Clicking a card closes the picker and opens the create dialog with that type preselected

## Create Memorial (legacy flow regression)
- [ ] Open type picker, click Memorial
- [ ] Form shows "Honoree Name", "Birth Date", "Passing Date" labels
- [ ] Submit with subject "QA Test Memorial" and skip dates
- [ ] After submit, lands on the memorial hub page
- [ ] Hub header reads "In loving memory of QA Test Memorial"

## Create Vacation Album (phase 2)
- [ ] Open type picker, click Vacation Album
- [ ] Form shows "Destination", "City / Region", "Trip Start", "Trip End" labels
- [ ] Submit with destination "QA Test Trip" and skip dates
- [ ] After submit, lands on the album hub page
- [ ] Hub header reads "Memories from QA Test Trip" (NOT "In loving memory")
- [ ] Hub shows tabs (at least Photos, Curate, Preview/Slideshow, Settings, Map)

## Memorial Hub Tabs (phase 3)
- [ ] For Margaret Thompson: header reads "In loving memory of Margaret Thompson"
- [ ] Photos tab loads with the 7 seed photos
- [ ] Curate tab loads — section noun says "Eras" for memorial
- [ ] Settings tab shows "Honoree Name" / "Birth Date" / "Passing Date" labels
- [ ] Map tab is visible (8th tab from phase 2.75)

## Vacation Hub Tabs (phase 3)
- [ ] For QA Test Trip: header reads "Memories from QA Test Trip"
- [ ] Curate tab — section noun says "Days" (NOT "Eras")
- [ ] Settings tab shows "Destination" / "Trip Start" / "Trip End" / "City / Region" labels
- [ ] Map tab is visible

## Photo Interactions on Hub (phase 2.5)
- [ ] On the Photos tab for Margaret Thompson, hover a photo
- [ ] Each photo card shows an upvote heart and comment bubble overlay
- [ ] Click the heart icon — count increments and the heart fills (optimistic UI)
- [ ] Click the comment bubble — a side drawer opens
- [ ] Type a test comment, submit — it appears in the thread

## Map Tab (phase 2.75)
- [ ] Open Map tab on the Margaret Thompson memorial
- [ ] If no geotagged photos exist, an empty state with EXIF tip is shown
- [ ] Map renders without console errors (Leaflet OSM tiles load)
- [ ] Untagged photos sidebar lists the seed photos (none have GPS)

## Public Share Page — /share/[id] (phase 5)
- [ ] Navigate to /share/a1111111-1111-1111-1111-111111111111 (Margaret Thompson memorial)
- [ ] Page loads without auth (middleware allowlist)
- [ ] Hero shows "In Loving Memory" + Margaret Thompson + year range
- [ ] Photo gallery renders the seed photos
- [ ] Each photo has the upvote heart + comment bubble overlay
- [ ] Comments section heading reads "Share Your Thoughts"
- [ ] Footer tagline reads "In Loving Memory"

## Public Share Page — Legacy Alias
- [ ] Navigate to /memorial/a1111111-1111-1111-1111-111111111111/live
- [ ] Page loads (alias still works after phase 5 rename)
- [ ] Same content as /share/[id]

## Public Page — Anonymous Photo Interactions
- [ ] On /share/[id], without logging in, click an upvote heart on a photo
- [ ] Count increments
- [ ] Reload the page — count persists (stored in mw_photo_upvotes)
- [ ] Click the comment bubble, type a comment with name "anon-tester"
- [ ] Submit — comment appears in the drawer

## Public Page — OG Meta Tags (phase 5)
- [ ] View page source on /share/[id]
- [ ] `<meta property="og:title">` is present and contains the album title
- [ ] `<meta property="og:description">` is present
- [ ] `<meta property="og:image">` is present (if hero photo set) or absent (graceful)

## Most Loved Badge (phase 5)
- [ ] On /share/[id] with photos that have upvotes from the previous test
- [ ] The top-3 most-upvoted photos show a "Most loved" badge ribbon
- [ ] Photos with 0 upvotes do NOT show the badge

## Slideshow Tab (phase 4)
- [ ] Open Slideshow tab on the Margaret Thompson memorial
- [ ] Generate button is present
- [ ] Generate a sequence — preview loads
- [ ] Optional Scenes section shows checkboxes: "Include map route", "Include crowd favorites", "Sort by most loved"

## Curate Tab Sort Strategy (phase 4)
- [ ] Open Curate tab
- [ ] A sort strategy radio shows at least "Chronological" and "Sort by most loved"
- [ ] Selecting "Sort by most loved" reorders the photo list (top-upvoted first)

## Music Tab Mood Filtering (phase 3)
- [ ] Open Music tab on a memorial — only memorial-appropriate moods shown (reflective, uplifting, hymnal)
- [ ] Open Music tab on QA Test Trip vacation — vacation moods shown (upbeat, adventurous, celebratory, uplifting)

## Contributor Upload Page
- [ ] Get the share code for QA Test Trip from the Settings tab
- [ ] Open /contribute/<code> in an incognito context (no auth)
- [ ] Page loads and shows album-type-aware copy ("Share Your Photos" not "Share a Memory")
- [ ] Upload zone is present
- [ ] The new "Where was this?" place picker is present below the drop zone (phase 2.5 deferred → manual place picker)

## Error & Edge Cases
- [ ] Navigate to a non-existent /memorial/00000000-0000-0000-0000-000000000000 → 404 or graceful redirect
- [ ] Navigate to /share/00000000-0000-0000-0000-000000000000 → notFound or graceful state
- [ ] Console errors during navigation → none expected
