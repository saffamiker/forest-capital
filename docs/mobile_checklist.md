# Mobile Visual Verification Checklist

Manual checklist for the mobile-responsive implementation. jsdom cannot
evaluate `@media` breakpoints, so the automated suite
(`mobile-responsive.test.tsx`) asserts the responsive utility classes
and the drawer's React-state behaviour — but the actual rendered layout
must be eyeballed in a real browser.

## Viewports to test

Use Chrome / Safari device emulation (or real devices) at:

- **iPhone SE — 375 × 667 — portrait** (the tightest common width)
- **iPhone SE — 667 × 375 — landscape**
- **iPhone 14 — 390 × 844 — portrait**
- **iPad — 768 × 1024 — portrait** (the `sm:` tier)
- **Desktop — 1280 × 800 and 1440 × 900** (confirm no desktop regression)

Also drag the width down to **320px** once — nothing should overflow
horizontally and no element should be clipped.

---

## Global (every page)

- [ ] No horizontal page scroll at 320px; the page never shifts sideways.
- [ ] The top nav bar is fixed and never scrolls away.
- [ ] Pinch-to-zoom works (viewport allows `maximum-scale=5`).
- [ ] Content clears the home-bar / gesture area on a notched phone.
- [ ] A tap on any button gives a brief press-in (active scale).
- [ ] With OS "reduce motion" on, the drawer / bottom sheets do not animate.

## Navigation (Commit 2)

- [ ] Below 1024px: the horizontal nav is gone, a ☰ hamburger shows top-left.
- [ ] Tapping ☰ slides in the left drawer over a dark overlay; ☰ becomes ✕.
- [ ] Drawer shows the three groups — Analysis / AI and Review / Output.
- [ ] The active route is highlighted in the drawer.
- [ ] Tapping a nav item navigates and closes the drawer.
- [ ] Tapping the overlay closes the drawer; Escape closes it.
- [ ] The mode switcher (Analyst/Commentary/Present) is in the drawer.
- [ ] Drawer footer shows the user email and a Sign-out button.
- [ ] At 1024px+ the original horizontal nav is back, unchanged.
- [ ] Below 380px the Testing Mode pill shows the 🧪 glyph only.

## Dashboard (Commit 3)

- [ ] Metric tiles are 2-up on mobile, 4-up on desktop.
- [ ] Strategy table shows the reduced column set; "← scroll →" hint visible.
- [ ] The Strategy column stays frozen while the metric columns scroll.
- [ ] "More columns" reveals the rest; "Fewer columns" collapses them.
- [ ] Tapping a row opens the strategy detail as a full-screen overlay
      with a ✕ close button; ✕ returns to the Dashboard.
- [ ] Cumulative and Efficient Frontier charts fill the width; legends wrap.

## Analytics (Commit 4)

- [ ] All four tables scroll horizontally with a frozen first column.
- [ ] Chart titles truncate with an ellipsis — never wrap to two lines.
- [ ] The export button / ⓘ icon stay tappable in the header row.
- [ ] Sensitivity charts stack one per row.
- [ ] The methodology accordion rows are full-width and easy to tap.

## Council & Academic Review (Commit 5)

- [ ] The question input and its button stack full-width on mobile.
- [ ] The Academic Review card stacks; the Run button is full-width.
- [ ] Agent cards and the verdict sections stack full-width.
- [ ] Tapping a ⓘ or a Data-Explain button opens a bottom sheet that
      slides up (≈60vh), is scrollable, and has a drag handle + ✕.

## Reports (Commit 6)

- [ ] Document-generation and deliverable cards are one per row.
- [ ] Team Activity filter bar stacks; date range is full-width.
- [ ] In Presentation View on mobile the three charts are a swipeable
      carousel with ◀ ▶ and a ●○○ indicator; one chart per screen.
- [ ] The Academic Export modal is full-screen on mobile.

## Settings (Commit 7)

- [ ] All sections stack vertically.
- [ ] The Users table (sysadmin) scrolls with a frozen Name/Email column.
- [ ] The Academic Documents upload row stacks; Upload is full-width;
      the delete button is an easy 44px tap target.

## QA / Statistical Evidence / Regime (Commit 9)

- [ ] Every matrix table scrolls horizontally with a frozen first column.
- [ ] All charts fill the width and remain legible.
- [ ] Side-by-side panels stack on a narrow screen.

## Floating components (Commit 8)

- [ ] The TestRunner panel is a full-width bottom sheet on mobile,
      capped at 50vh (40vh in landscape), scrollable, clear of the home bar.
- [ ] The free-form Suggest button sits above the panel — they never overlap.
- [ ] The What's New modal is full-screen on mobile; the entry list
      scrolls while "Got it" stays pinned at the bottom.
- [ ] Site-tour tooltips are centred on screen on mobile; Back / Next /
      Skip are all easy 44px tap targets.
- [ ] The Advisor panel never overflows the viewport width.

## Touch targets (Commits 1 & 10)

- [ ] ⓘ InfoIcons, chart/table export buttons, nav items, toggle switches
      and table-row actions are all comfortably tappable (≥44px) on mobile.
