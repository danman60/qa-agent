# LilLearner Android — Full Flow Tests

## Flow 1: App Launch & Home Screen
- [ ] App launches and the Home screen is visible with child name and categories
- [ ] "Home" title visible at top
- [ ] Child name "Stella" is displayed with avatar circle
- [ ] "Add" button visible next to child avatar
- [ ] Day card shows "Stella's Day" with date and logged count
- [ ] "Curricula" button visible on home screen
- [ ] "AI Tutor" button visible on home screen
- [ ] "Quick Log" button visible (dark/black button with microphone icon)
- [ ] "Categories" section header visible
- [ ] Category cards visible: "Reading Lessons", "Math Lessons", "Language Arts", "Printing"
- [ ] Each category card shows "0 logged" or a number

## Flow 2: Bottom Tab Navigation
- [ ] Bottom tab bar visible with 4 tabs
- [ ] Tap the "Progress" tab in the bottom tab bar and verify the Progress screen loads
- [ ] Progress screen shows content (stats, categories, or empty state)
- [ ] Tap the "Reports" tab in the bottom tab bar and verify the Reports screen loads
- [ ] Reports screen shows content (report list or generate button)
- [ ] Tap the "Profile" tab in the bottom tab bar and verify the Profile screen loads
- [ ] Profile screen shows user info or sign-in prompt
- [ ] Tap the "Home" tab in the bottom tab bar and verify the Home screen loads

## Flow 3: Category Logging
- [ ] On Home screen, tap the "Reading Lessons" category card and verify a logging screen appears
- [ ] The logging screen has input fields or skill options
- [ ] Tap back or close to return to Home screen
- [ ] On Home screen, tap the "Math Lessons" category card and verify a logging screen appears
- [ ] Tap back or close to return to Home screen

## Flow 4: Curricula
- [ ] On Home screen, tap the "Curricula" button and verify a curricula screen loads
- [ ] Curricula screen shows "No curricula yet" message or a list of curricula
- [ ] Screen has a title or header related to curricula
- [ ] Tap the back arrow or use device back to return to Home screen
- [ ] Verify the Home screen is visible with category cards

## Flow 5: AI Tutor
- [ ] On Home screen, tap the "AI Tutor" button and verify a tutor screen loads
- [ ] Navigate back to Home screen using back arrow or device back
- [ ] Verify the Home screen is visible with category cards

## Flow 6: Quick Log
- [ ] On Home screen, tap the "Quick Log" button and verify a voice or quick entry interface appears
- [ ] Navigate back or dismiss the quick log
- [ ] Verify the Home screen is visible with category cards

## Flow 7: Profile
- [ ] Tap the "Profile" tab in the bottom bar and verify the Profile screen loads
- [ ] Profile screen shows user email or display name

## Flow 8: Child Management
- [ ] Tap the "Home" tab to return to Home screen first
- [ ] On Home screen, tap the "Add" button next to child avatars and verify the "Add Learner" or "Add a Little Learner" screen appears
- [ ] The add-child form shows Name and Birthdate fields
- [ ] Tap back without saving to return to Home
- [ ] Verify "Stella" is still shown as the active child

## Flow 9: Scroll & Layout
- [ ] Tap the "Home" tab to ensure you are on the Home screen
- [ ] Scroll down on Home screen and verify more content is visible below
- [ ] Scroll back up and verify the top content with child name is visible again
- [ ] All text is readable (no overlapping, no clipping)
- [ ] Tap any visible button and verify it responds (no dead zones)
