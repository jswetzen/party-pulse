# Big-screen design exploration — reference

Working notes for the `/screen/` redesign work started 2026-09-03. This is the deep-dive
doc; `PLAN.md`'s "Big-screen design exploration" section has the short version and is the
first place other docs/code comments point back to — keep both in sync if either changes.

## Why this exists

The stock `/screen/` reveal (`pulse/templates/pulse/_screen_state.html`, the "GENERIC"
style below) is a plain bar-chart layout — it works for anything but doesn't feel like an
event moment. The goal was to explore genuinely different visual directions, not just
retint the existing one, and pick favorites to actually build.

## The 10 concepts

Drafted with 10 parallel subagents as a Claude Design canvas (one artboard each), every
number in every mockup pulled from the real seeded reception data (150 respondents, 5,290
responses — `manage.py seed_demo_data`), never invented. Canvas:
<https://claude.ai/code/artifact/6f4741a2-2e86-4130-808f-9ae4d70f2fc2>.

All 10 are also reachable as static, frozen-data previews in the app itself — useful for
judging a design on an actual phone/projector, which a canvas artboard is awkward for. Index
at `/screen/concepts/`, each one at `/screen/concepts/<n>/`
(`views.screen_concepts_index`/`screen_concept`, templates under
`pulse/templates/pulse/screen_concepts/`). These previews are **not** wired to
`BigScreenState` — frozen snapshot only, same number every time regardless of what's
actually revealed.

| # | Name | Aesthetic | Real data shown | Status |
|---|------|-----------|------------------|--------|
| 1 | Broadsheet | Swiss editorial minimalism | Cried at the ceremony, by side (58/61/65%) | preview only |
| 2 | Highscore | Retro CRT arcade scoreboard | Staying till the band packs up, ranked by age | preview only |
| 3 | **Bouquet** | Botanical wedding stationery | Pet-before-kids prediction, near 50/50 split | **implemented** |
| 4 | Fika Party | Confetti scrapbook | Best fika treat — real 3-way tie at 23% | preview only |
| 5 | Breaking Pulse | News-broadcast bulletin | How far guests traveled (34% from abroad) | preview only |
| 6 | Gauge Cluster | Automotive dashboard | Coffee cups/day, by relation to the couple | preview only |
| 7 | **Podium** | Game-show leaderboard reveal | Most-anticipated part of the evening | **implemented** |
| 8 | Chalkboard Café | Hand-lettered blackboard | Cinnamon-bun record: 10 in one sitting | preview only |
| 9 | Dansgolvet | Neon vaporwave dance floor | Dance-floor strategy breakdown | preview only |
| 10 | Root Terminal | Brutalist phosphor terminal | Multi-stat dump (age/countries/coffee/buns) | preview only |

## What "implemented" means: selectable reveal styles

Podium and Bouquet are not a global reskin — each is built around one specific data shape,
so they ship as **selectable per-question reveal styles**, picked live in the host console
next to breakdown/aggregation (`BigScreenState.style`), not a theme applied to everything:

- **Podium** ranks a single set of labelled scores (1st/2nd/3rd stage + a flexbox chip row
  for the rest). Only a real fit for a multiple-choice question's options at
  breakdown=Alla — a per-group breakdown would be several separate option distributions,
  not one ranked list.
- **Bouquet** has one botanical diagram type per `Question.Type` (originally boolean-only;
  multiple_choice and number added 2026-09-05 — see "Session summary" below):
  - **boolean** — one ja/nej split as a single two-ended vine, geometry from the real
    yes_pct (`views._bouquet_geometry_boolean`).
  - **multiple_choice** — a fanned arrangement, one stem per option, stem height linear in
    the real pct (0..100, same semantics as GENERIC's own bar-fill width — not
    renormalized to the group's max), tallest/highest-pct option centered and the rest
    flanking outward by rank (`views._bouquet_geometry_multiple_choice`).
  - **number** — the single aggregated value as one centerpiece stem, deliberately *not*
    spatially scaled by magnitude — NUMBER questions (age, cinnamon buns, coffee cups/day,
    …) have no declared min/max in the schema, so a "tall stem = big number" mapping would
    be a fabricated scale, not real data (`views._bouquet_geometry_number`).
  That trio is breakdown=Alla only. As of 2026-09-06, every other breakdown (Kön/Åldersgrupp/
  Sida/Relation) gets a **"Ribbon Rows"** sibling of each — one horizontal row per group
  instead of one shared diagram — so Bouquet fits any question type at any breakdown, same
  as GENERIC:
  - **boolean_grouped** — each row is a shrunken two-color ja/nej track, split at that
    group's own yes_pct, with a seam flower hue-shifted to whichever side won
    (`views._bouquet_geometry_boolean_grouped`).
  - **multiple_choice_grouped** — each row strings every option's blossom along one
    horizontal stem, ranked left-to-right (not centre-out like the single-group fan); only
    the row's own top scorer(s) — plural on a genuine tie — get a prominent label, everyone
    else a smaller one below (`views._bouquet_geometry_multiple_choice_grouped`).
  - **number_grouped** — each row repeats the single-group idea (one flower, one honest
    value, no invented scale) once per group (`views._bouquet_geometry_number_grouped`).
  Row height is (fixed rows band) / (group count), so 2-group breakdowns (Kön) get roomier
  rows than the 5-group one (Åldersgrupp) — see "Session summary (2026-09-06)" for the real
  legibility problems this surfaced and how they were fixed.

`views.style_is_compatible()` is the actual gate. Picking an incompatible style (or
revealing before anyone's answered) falls back to GENERIC **server-side** —
`screen_control.html` has a small script that disables the options that would fall back,
but that's a UI hint mirroring the real check, not the check itself. As of 2026-09-06,
BOUQUET no longer has an incompatible breakdown at all (only PODIUM can still fall back on
a breakdown mismatch) — the script/hint text still exist for PODIUM and for "nobody's
answered yet", just not for BOUQUET's breakdown any more.

### File map

- `pulse/models.py` — `BigScreenState.Style` choices + `style` field (migration `0005`).
- `pulse/views.py` — `style_is_compatible`, `_rank_podium`,
  `_bouquet_geometry_boolean`/`_multiple_choice`/`_number` (breakdown=Alla) and their
  `_grouped` siblings (every other breakdown), `_podium_question_font_size`, and the
  `screen_state()`/`screen_control()` wiring.
- `pulse/templates/pulse/screen_styles/_podium.html` — Podium's data-driven markup,
  included from `_screen_state.html`'s revealed branch.
- `pulse/templates/pulse/screen_styles/_bouquet.html` — Bouquet's dispatcher: includes
  `_bouquet_decor.html` (paper grain/frame/corner sprigs + the shared `#bq-blossom` flower
  glyph every diagram type `<use>`s), the header (plus the "uppdelat efter ..." subline
  when breakdown != Alla), one of `_bouquet_visual_boolean.html` / `_multiple_choice.html` /
  `_number.html` / `_boolean_grouped.html` / `_multiple_choice_grouped.html` /
  `_number_grouped.html` by `bouquet.kind`, then `_bouquet_footer.html`.
- `pulse/static/pulse/screen_styles.css` — both styles' CSS, class-scoped
  (`.style-podium …` / `.style-bouquet …`) so it can't collide with `style.css` (it
  already would have: both mockups originally used a plain `.eyebrow` class that
  `style.css` also defines).
- `pulse/templates/pulse/screen_display.html` — loads the Google Fonts + scoped CSS once
  (not per 2s poll), and the JS "fit 1920×1080 to whatever's actually plugged in" scaler
  (`#style-canvas`/`#style-canvas-viewport`), reused from the concept-preview shell.
- `pulse/tests/test_screen_styles.py` — compatibility gate, ranking/geometry math, and the
  fallback behavior.

## Session summary (2026-09-03/04)

1. Drafted the 10 concepts (design canvas + real data, see above).
2. Built `/screen/concepts/1–10/` static preview routes so they're viewable on a phone —
   the design canvas itself is awkward to view/compare there.
3. Refreshed the local podman container to serve them; you picked **Podium** and
   **Bouquet**.
4. Talked through generalization scope before building — see "Decisions already made"
   below — landed on: Podium stays multiple-choice-only (not extended to rank breakdown
   groups), and the host picks the style live per reveal (no per-question default field
   yet).
5. Built both as real, data-driven `BigScreenState.style` options with server-side
   compatibility gating and GENERIC fallback (see above). 14 new tests, all passing
   alongside the existing 68. Committed.

### Decisions already made (don't re-litigate without new information)

- **Podium scope: multiple-choice only**, not extended to rank breakdown groups
  (boolean/number by group) too. That extension is real, separate work — the podium/chip
  layout would need reworking for a variable item count as more than a data-source swap —
  deliberately deferred, not forgotten. See "Possible next steps" below.
- **Style is picked live in the host console**, not preset per question ahead of time.
  Simpler, matches how breakdown/aggregation already work, but means the host makes the
  choice in the moment during the reception rather than once while writing the question
  bank.

## Session summary (2026-09-04, later)

Asked to check why Bouquet "doesn't feel like an integrated page, more like an iframe."
Confirmed by screenshotting the live `/screen/` route (headless Chrome at a deliberately
non-16:9 window size, `192.168.1.59:8000`) rather than just reading the templates — the
bug wasn't visible from the source alone. Two separate causes, both now fixed:

1. **A raw template comment was rendering as visible page text.** `screen_display.html`'s
   head comment used `{# ... #}` across multiple lines; Django's inline comment tag does
   not support multi-line content (confirmed directly against this project's Django 6.0 —
   the un-parsed `{# #}` is left as literal text). That text sat in `<head>`, and HTML5
   parsing kicks a stray non-whitespace character token out of `<head>` into the top of
   `<body>` — so the entire comment string rendered at the very top of *every* `/screen/`
   page load, Podium included. It's just much harder to notice on Podium (near-black text
   on a near-black stage) than on Bouquet (visible cream-on-ink text sitting above a pale
   card). Fixed by switching to the `comment`/`endcomment` block tag, which does support
   multi-line content.
2. **The letterbox behind the scaled canvas had no background.** `#style-canvas-viewport`
   is `position:fixed;inset:0` with no `background`, so whenever the scaled 1920×1080
   `#style-canvas` doesn't exactly fill the real viewport's aspect ratio (true for almost
   any real window/projector), the gap showed `body`'s dark `--ink` straight through —
   a hard seam around Bouquet's pale wedding-stationery card that read as "a box floating
   on a different page." Fixed by giving `#style-canvas-viewport.style-bouquet` a solid
   background matching the outer stop of `.style-bouquet .stage`'s own radial-gradient
   (`#ece0c6`), so the letterbox is invisible instead of off-brand.

Podium's letterbox seam is the same mechanism, initially left alone since it wasn't the one
asked for and its dark stage made the seam much less obvious — then fixed on request right
after, the same way: `#style-canvas-viewport.style-podium` gets `.stage`'s own base
linear-gradient (`180deg, #1c0716 0%, #170513 45%, #0c0209 100%`, minus the centered radial
spotlight glow, which only makes sense within the stage itself) rather than a flat color, so
the vertical letterbox bars fade into the same gradient instead of a mismatched solid.

All 82 tests still pass (`uv run pytest`); this was a template/CSS fix, no test coverage
changed. Verified visually with before/after screenshots, not just by reading the diff.

## Session summary (2026-09-05)

Asked to implement all diagram types for Bouquet (not Podium) — i.e. give it a real,
data-driven diagram for `multiple_choice` and `number` questions too, alongside the
original boolean-only vine, so any question type can reveal in Bouquet's voice at
breakdown=Alla (Podium stays multiple-choice-only, unchanged).

1. Refactored `_bouquet.html` (previously all boolean-specific markup) into a thin
   dispatcher over `bouquet.kind`, extracting the non-data-driven chrome (paper
   grain/frame/corner sprigs) into `_bouquet_decor.html` and the bottom rule/count into
   `_bouquet_footer.html`, shared across all three diagram types. Verified this pure
   refactor was pixel-identical to the pre-refactor boolean render via before/after
   screenshots before building anything new on top of it.
2. Along the way, introduced (then had to fix) a shared reusable flower glyph,
   `#bq-blossom`, meant to be the one "data-bearing flower" `<use>`d by all three diagram
   types. First attempt defined it as an SVG `<symbol viewBox="...">` — renders offset and
   clipped on this project's headless Chrome (confirmed in an isolated minimal test page,
   not just asserted). Fixed by matching the pattern the file's own corner-sprig decorations
   already used successfully: a plain `<g id="...">` (no viewBox on the `<g>` itself)
   referenced via `<use>` inside an outer `<svg>` that supplies its own viewBox/width/height.
   Documented in `_bouquet_decor.html`'s own comment so it isn't reintroduced later.
3. Added `_bouquet_geometry_multiple_choice()` (fanned arrangement: one stem per option,
   height linear in the real pct — same 0..100 semantics as GENERIC's own bar-fill width,
   not renormalized to the group's max — highest-pct option in the centre slot, the rest
   flanking outward by rank via a small centre-out slot-ordering helper) and
   `_bouquet_geometry_number()` (the single aggregated value as one centerpiece stem,
   deliberately not spatially scaled by magnitude, since NUMBER questions have no declared
   domain in the schema and a fake min/max would misrepresent the data) to `views.py`, fully
   unit-tested. Widened `style_is_compatible` so BOUQUET fits any `Question.Type` at
   breakdown=Alla (previously boolean-only), and updated `screen_control.html`'s picker/JS/
   hint text and the `BigScreenState.Style` doc comment to match.
4. Built the two new template partials (`_bouquet_visual_multiple_choice.html`,
   `_bouquet_visual_number.html`) and their CSS against that geometry contract, verifying
   with real headless-Chrome screenshots against realistic seeded data (5-option questions
   with long Swedish labels, a 3-digit NUMBER value, multiple aggregations) rather than
   trusting the markup by inspection — caught and fixed real layout issues this way (the
   number diagram's value/tag/flower spacing, in particular) before calling it done.

All 91 tests pass (82 existing + 9 new/changed in `test_screen_styles.py`), all three
diagram types confirmed visually. See "File map" above for the current shape of
`pulse/templates/pulse/screen_styles/`.

## Session summary (2026-09-06)

Asked to extend Bouquet to grouped breakdowns (Kön/Åldersgrupp/Sida/Relation), not just
breakdown=Alla — the "Possible next steps" #2 follow-up above, now done rather than
deferred. A design-exploration pass happened first (six concept mockups, a Claude Design
canvas, real feedback on legibility) and picked **"Ribbon Rows"**: one horizontal row per
group, generalizing each of the three existing single-group diagrams into a row instead of
inventing a fourth layout. This session built that for real, against the actual running app
and real seeded data, not just against the mockups.

1. Widened `style_is_compatible()` so BOUQUET fits any breakdown, same as GENERIC (PODIUM
   unchanged — still MC + breakdown=Alla only). Restructured `screen_state()`'s BOUQUET
   branch to check `state.breakdown == OVERALL` first (existing single-group path,
   untouched) vs. else (new grouped path, dispatching on question type same as before).
2. Added `_bouquet_geometry_boolean_grouped`/`_multiple_choice_grouped`/`_number_grouped`,
   each taking the real `compute_breakdown()` dict for a non-Alla breakdown and returning
   one row per group in the same order `compute_breakdown()` already returns (chronological
   for AGE, lexicographic otherwise — never re-sorted). Row height is a fixed rows band
   (bounded by the header's divider above and the footer's rule below) divided by the group
   count, so 2-group breakdowns (Kön) get roomier rows than 5-group ones (Åldersgrupp).
3. **The mockups' own pixel offsets didn't transfer directly**, exactly as anticipated going
   in — they had their own slim single-line header, while the real `_bouquet.html` has a
   taller one (eyebrow + a headline that can wrap to 2 lines + a divider), plus a footer, so
   the real rows band is smaller and starts/ends in different places. Verified the real
   budget by screenshotting the actual running app rather than trusting the mockup's
   assumptions, and sized `_BOUQUET_GROUPED_ROWS_TOP/_BOTTOM` off that.
4. **First real screenshot of multiple_choice_grouped at breakdown=age (5 groups × 5
   options, the actual hardest case) revealed a genuine layout bug the mockup couldn't have
   caught**: the winner's label was drawn as two stacked lines (name, then pct below it) —
   copied from the RibbonRows mockup, which had roughly double this app's real per-row
   vertical budget (~180px rows there vs. ~88px here at 5 groups). At 88px, the two-line
   label's own height collided with the row above and below it — visible in the screenshot
   as overlapping text, not visible from reading the template. Fixed by combining the
   winner's name+pct into one line ("Fikat 30,8%"), same shape as the smaller non-winner
   labels, and by reworking the sizing formula to compute font sizes *first* (legibility
   floors win) and derive `bloom_max` from whatever vertical room is left over — not the
   other way around — so a label can never collide with a neighboring row regardless of
   group count. Re-verified with a fresh screenshot before moving on.
5. **Second real bug, also only visible on screen**: the boolean_grouped seam flower's
   "recolor to the winning side" used `hue-rotate(150deg)` on a first guess, which rotated
   the rose glyph past green into teal — a third, unrelated color, not "the nej color".
   Measured the two colors' actual HSL hues (`#c98a83` rose ≈ 6°, `#8a9873` olive ≈ 83°) and
   used the ~77° difference instead. Also caught in the same pass: the ja/nej pct labels
   were positioned with a fixed CSS padding around the seam flower, which was narrower than
   the flower's own (row-height-scaled) radius at small group counts, so the flower painted
   over the tail of the text. Fixed by computing an explicit per-reveal `label_gap` (half the
   flower's own diameter plus clearance) in Python and positioning the labels at
   `seam_x ± label_gap` directly, rather than leaving the gap to a fixed CSS padding.
6. Also caught by screenshot rather than by inspection: boolean_grouped's initial font/
   flower-size formula scaled the same way multiple_choice_grouped's tightly-constrained one
   does, but boolean_grouped has no vertical stacking problem (the flower and both pct
   labels sit on one horizontal line, vertically centered) — so it was needlessly small (15px
   pct text at 5 groups) when the real constraint (this project's own "secondary numbers
   never below ~26px" standard) had much more headroom to spend. Raised its floors
   accordingly once the actual (generous) available space was confirmed on screen.
7. Verified all four required real-data combinations end to end against the actual running
   container (`Question.objects.get(id=16)` × age, `id=8` × age, the system age question ×
   relation with avg, plus the small-group cases `id=8` × sex and `id=16` × side) and
   confirmed the existing breakdown=Alla diagrams still render unchanged (no regression from
   restructuring `screen_state()`'s BOUQUET branch).
8. Added a shared "uppdelat efter {{ breakdown }}" subline to `_bouquet.html`'s header,
   shown only when breakdown != Alla, positioned below the header's divider (whose position
   is fixed regardless of how many lines the headline itself wraps to) rather than trying to
   flow directly under the headline — safe regardless of question length.
9. Updated `screen_control.html`: Bukett's `<option>` no longer declares
   `data-requires-breakdown="overall"` and the hint paragraph now describes it as fitting any
   breakdown, same as Standard.

One deliberate scope-trim from the mockup: a genuine tie for first place in
multiple_choice_grouped renders both tied blooms with the prominent "winner" label (decided
by `pct == row's own max`, not `rank == 0`) but skips the mockup's tie-arc-plus-"DELAD 1:A"
connector flourish — a nice-to-have, not load-bearing for reading the data correctly, traded
for shipping time.

104 tests pass (91 existing + 13 new/changed in `test_screen_styles.py`), all six diagram
kinds (three single-group, three grouped) confirmed visually against the real running
container and real seeded data. Screenshots taken during this session (kept, not deleted):
`mc_age2.png` (the hardest case, post-fix), `bool_age3.png`, `num_relation.png`,
`bool_sex.png`, `mc_side.png`, `mc_overall_regression.png` — plus the earlier
pre-fix/broken versions (`mc_age.png`, `bool_age.png`, `bool_age2.png`) kept alongside them
as a record of what the two real bugs above actually looked like before the fix.

## Session summary (2026-09-07)

Reported: viewing `/screen/` on a phone, the Podium/Bouquet reveal's scaling was jumpy.
`screen_display.html` renders Podium/Bouquet into a fixed 1920x1080 `#style-canvas`, scaled
to fit whatever's plugged in — until this session, via JS (`fitStyleCanvas()`, reading
`window.innerWidth/innerHeight`, re-run on `window.resize` and every `htmx:afterSwap`).

Two likely causes, diagnosed from the code plus well-established (MDN/WebKit-documented)
mobile-browser behavior — **not** reproduced on an actual phone from this dev sandbox, worth
being explicit about since that gap matters and this project has a habit of saying so rather
than overclaiming:
1. **Confirmed present** (by direct inspection): the page had no `<meta name="viewport">`
   tag at all. Without one, mobile browsers render into a virtual ~980px-wide desktop
   viewport and scale the whole page down to fit the real screen — so `fitStyleCanvas()`'s
   `innerWidth/innerHeight` reads never matched the real device pixels.
2. **Well-founded, not directly reproduced**: mobile browsers fire `resize` repeatedly while
   the address bar shows/hides during scroll; with no debounce, `fitStyleCanvas()` would
   re-run several times in quick succession, each with a slightly different `innerHeight` —
   visible as the canvas stepping through scale/position values rather than settling once.

Fix chosen over a smaller JS patch (add the meta tag + debounce): replace the whole
scaling mechanism with pure CSS **container query units** (`cqw`/`cqh`), removing the
`resize`-listener bug class entirely rather than mitigating it, since a prototype proved
cheap enough to be worth it. Prototyped and verified in isolation first (headless-Chrome
screenshots at five sizes, matching the old JS's exact scale/centering output) before
touching the real template — two non-obvious gotchas surfaced there and are preserved in
`screen_display.html`'s own comment: `calc()` dividing a length by a bare number (not
another length) silently produces a length instead of the unitless factor `scale()` needs;
and `position:absolute;margin:auto` does not center a box larger than its container (auto
margins never go negative), so centering is an explicit `translate()` calc instead.

Only `screen_display.html` changed — confirmed (by grep and by reasoning about what the
mechanism actually touches) that `_podium.html`, every `_bouquet*.html`, and
`screen_styles.css` only depend on the fixed 1920x1080 canvas existing, not on how it gets
scaled. `/screen/concepts/<n>/`'s own preview shell (`screen_concept_base.html`) has a
*separate* copy of the same JS-scaler idea (different ids, `#concept-viewport`/
`#concept-scale`) — already has its own viewport meta tag so it doesn't have bug #1, but
would have the same bug #2 in principle. Deliberately left alone this session (out of
scope: those are frozen-data preview routes, not the live host tool) — noted here rather
than silently leaving a known-equivalent bug unmentioned.

Verified against the real running container: Podium and Bouquet reveals, plus the idle and
GENERIC-style states (which don't use `#style-canvas` at all and must be unaffected), each
screenshotted at 1920x1080 (fills edge-to-edge, scale=1), 390x844 (phone portrait — the
actual reported case), 844x390 (phone landscape), and 3840x1080 (ultrawide/4K projector —
regression-guarded since it has its own git history in this file). All centered, fully
visible, letterbox blending into each style's own background with no seam. 104 tests still
pass (template-only change).

## Session summary (2026-09-07, follow-up: Firefox regression from the cqw/cqh rewrite)

Reported live, on the real deployment (partypuls.mikro.swetzen.com, maximized window, real
2560x1068 viewport, an up-to-date Firefox): the Podium/Bouquet canvas filled only the top-left
~3/4 of the screen, unscaled, instead of letterboxing to fit. This is the exact regression the
session above's own text worried about without knowing it yet -- that rewrite's prototyping
was headless-Chrome only, and this bug is Firefox-only.

Root cause, confirmed by inspecting `getComputedStyle(#style-canvas)` on the live page:
`transform: "none"`. The CSS-only rewrite's `--scale: min(calc(100cqw / 1920px), calc(100cqh /
1080px))` divides a `cqw`/`cqh` length by a `px` length to get the unitless factor `scale()`
needs -- and Chrome resolves that fine, but Firefox's `calc()` only reliably turns a
length-divided-by-length into a unitless `<number>` when both operands carry the *identical*
unit token (`px / px`, `vw / vw`); mixed units like `cqw / px` resolve to nothing, and the
whole `transform` declaration is dropped as invalid rather than degrading gracefully. This is
a real, still-open cross-engine spec gap, not a mistake in the earlier session's reasoning --
`CSS.supports('width', '1cqw')` is `true` in the same Firefox, so it isn't a
`@supports`-detectable missing feature either; it's a silent computed-value failure.

Fix: register `--cw`/`--ch` as typed custom properties via `@property` (`syntax: "<length>"`),
which forces the browser to resolve `100cqw`/`100cqh` to a real internal length value instead
of leaving them as raw cqw-unit token text for `calc()` to divide -- then derive the unitless
ratio via `tan(atan2(a, b))` instead of `calc(a / b)`, which sidesteps the mixed-unit
restriction because trig functions cast their arguments to angles/numbers rather than
carrying the length type through the division. Two more non-obvious gotchas surfaced while
verifying this (both preserved in `screen_display.html`'s own comment, alongside the two the
2026-09-06 rewrite already documented there):
  - `@property`'s `inherits` flag defaults to `false` in every reference example this was
    drafted from, which is wrong here: `--cw`/`--ch` are *set* on `#style-canvas-viewport` but
    *read* via `var()` on `#style-canvas`, a descendant. With `inherits: false` a registered
    custom property does not cascade to children at all, so the descendant saw the
    initial-value `0px` instead of the real size, and `--scale` silently collapsed to `0` --
    an even worse failure (canvas scaled to nothing) than the original bug (canvas merely
    unscaled). Caught immediately by testing in a real Firefox rather than trusting the
    pattern, which was the entire point of insisting on that verification here.
  - `@property` itself needs Firefox 128+/Chrome 85+ -- comfortably below this project's
    actual target browsers (a recent Chrome, a recent Firefox), so it isn't trading one
    compatibility gap for a worse one.

**Verified how, honestly**: real (non-headless-only) Firefox 152.0.4 and Chromium
148.0.7778.167, both driven via WebDriver (geckodriver+Selenium, chromedriver+Selenium) from
this dev sandbox against a local `manage.py runserver` with a seeded Bouquet reveal --
`getComputedStyle(#style-canvas).transform` and `getBoundingClientRect()` checked at
1920x1080, 390x844 (phone portrait), 844x390 (phone landscape), 2560x1068 (the exact size that
broke live), and 3840x1080 (ultrawide/4K). Before the fix, Firefox reproduced the reported bug
exactly (`transform: none`, pinned unscaled 1920x1080 rect) at every size while Chrome already
showed a correct scale matrix; after the fix, both engines show a real `scale()` matrix and a
correctly letterboxed/centered rect at all five sizes. **Not verified**: an actual physical
phone (same caveat as the session above -- these "phone" sizes are desktop-browser window
dimensions, not a real mobile viewport/address-bar situation) and the live production
deployment itself (only this dev sandbox's local server, on a fresh SQLite DB seeded via
`seed_questions`/`seed_demo_data`, was checked). 106 tests pass (template-only change; the
count grew from 104 via unrelated commits earlier on this branch).

An earlier draft of this fix (both `@property` declarations using `inherits: false`, the
`inherits: true` gotcha above not yet found) was tested and rejected in this same session --
worth recording since it's a reminder that "the documented workaround exists" and "this
specific adaptation of it works" are different claims, and only the second one is what actual
verification in a real engine can confirm.

## Session summary (2026-09-08: Ribbon Rows number/line overlap + left-right alignment)

Reported from Johan's visual review of the age-bucket change (5→6 groups, commit f1185d2):
on Bouquet's Ribbon Rows (grouped) visuals at breakdown=Åldersgrupp, "numbers overlap with
the lines/tracks and become illegible", and "left-right alignment looks off — extra empty
space on the left, too little on the right." Reproduced first by screenshotting the real
running app (headless Chromium, `manage.py runserver` + real seeded data) at all three
grouped diagram types and 6 groups (Åldersgrupp), per this project's own established
convention of never trusting a layout fix reasoned about from source alone. Two separate,
unrelated real bugs, both confirmed visually before touching code:

1. **Numbers overlapping tracks — boolean_grouped only.** `.bg-pct-ja`/`.bg-pct-nej` were
   positioned at `top:row.row_center` with `transform: translateY(-50%)` — exactly the same
   vertical center the `.bg-track` line itself uses — so the 7px track line was drawn
   straight through the vertical middle of the "66,7% ja"/"33,3% nej" digits, cutting the
   glyphs in half visually (confirmed by pixel-cropping the screenshot: the colored line ran
   directly across the digit strokes). multiple_choice_grouped and number_grouped were not
   affected — mcg's winner/other labels were already offset above/below their bloom via
   `_BOUQUET_MCG_GAP`, and number_grouped has no line/track at all. Fixed by adding
   `row.pct_top` (`views._bouquet_geometry_boolean_grouped`, floored not rounded — see its
   own `_BOUQUET_BG_ROUNDING_SLACK` comment for why floor matters here), positioning the pct
   labels bottom-anchored above the track's own centerline instead of vertically centered on
   it (`translateY(-100%)` in CSS, not `-50%`). `pct_font_size`'s existing legibility floor
   is now also clamped by how much vertical room half a row actually has above the track,
   the same "floor first, shrink to fit if it doesn't clear" pattern
   `multiple_choice_grouped`'s `bloom_max` already used — at 6 groups (this app's tightest
   real case) the floor barely fit and needed the clamp to actually engage (font dropped
   from 22px to 20px at 6 groups only; 2/3/4-group cases were already roomy enough and are
   unaffected).
2. **Alignment — all three grouped types.** The shared left-hand label column
   (`.bg-label`/`.mcg-label`/`.ng-label`) has an explicit `width` (`label_width`, 420px) but
   declared no `text-align` of its own, so it silently inherited `text-align: center` from
   the ambient `.screen` page wrapper (`style.css`, used for the guest-facing pages' centered
   layout) — every other piece of Bouquet text either has no explicit width (so text-align
   can't visibly move it) or sets its own alignment, so this one slipped through unnoticed
   since 2026-09-06. The pixel geometry itself was already symmetric (`label_left=140`
   mirrors `1920 - (track_left+track_width)=140` exactly), but the inherited center-alignment
   shifted the *visible* group-name text rightward within its column, so the row read as
   "empty on the left, cramped on the right" even though the underlying numbers were correct
   — a CSS inheritance bug, not a geometry bug, which is exactly why reproducing visually
   mattered here: the geometry alone looked fine on paper. Fixed with one `text-align: left`
   rule per label class.

Verified with real headless-Chromium screenshots, before and after, for all three grouped
diagram types at Åldersgrupp (6 groups) and a Kön (2 groups) spot-check to confirm the
already-working small-group case wasn't regressed — all six renders confirmed correct
(numbers clear of any line, labels flush left, no row-to-row collision even at 6 groups).
123 tests pass (116 existing + a new parametrized geometry test asserting the pct label
never overlaps its own track or a neighboring row at 2/3/4/6 groups, plus a CSS-text
regression guard for the three label selectors' `text-align: left`, since the alignment bug
was pure CSS inheritance that a geometry-only test can't see).

## Session summary (2026-09-08, follow-up: Ribbon Rows row-to-row spacing)

Reported from Johan looking at the real running container (the exact live boolean_grouped
Åldersgrupp reveal above, 137 svar): "number alignment is better, but not element alignment"
— explicitly distinct from the number/track-overlap and left-right text bugs the session
above had just fixed, so this was diagnosed as a separate, still-open bug in the same area
rather than a re-check of the same one. Investigated by measuring the actual rendered page
with headless Chromium via CDP (`puppeteer-core` driving a `nix-shell -p chromium` binary,
`getBoundingClientRect()`/pixel-sampling the real PNG output — the same "don't trust the
Python numbers alone" discipline as every prior session in this file) rather than re-reading
the geometry function's formulas and assuming they were right.

The seam flower, the track, and the group-name label all measured **exactly** coincident on
`row.row_center` for every one of the 6 Åldersgrupp rows (`label.cy == track.cy == seam.cy`
to the pixel, confirmed both via `getBoundingClientRect()` and, for the flower specifically,
by locating its yellow center-dot's own rendered pixels in a 4x-supersampled crop — 0.5px off
the box's geometric center, i.e. correctly centered within anti-aliasing noise) — so *within*
any single row, "is the flower on the line, is the line level with the label" was already
correct and not the bug. The real defect only shows up **between** rows: diffing consecutive
rows' measured `track.cy` gave `74, 72, 74, 72, 74` px gaps instead of a uniform `73`px, i.e.
a real, alternating ±1px drift in the shared row-spacing rhythm across the whole diagram —
exactly the "row-to-row consistency... does it drift" failure mode this session was
specifically asked to check for, and exactly why it wasn't visible from inspecting any one
row's own geometry.

Root cause, found by reasoning about `_bouquet_geometry_boolean_grouped`'s
`row_center = round(row_top + row_height / 2)` (identical in the multiple_choice_grouped and
number_grouped siblings — a genuinely shared root cause, per this file's own "all three plot
the same row band" convention): at exactly 6 groups, the fixed 438px row band divides evenly
into a **whole-number** `row_height` (73.0) — the one group-count this app actually has where
that's true (2 groups → 219.0, still whole but only one gap exists so alternation can't show;
3 → 146.0, `row_height/2` is 73.0 exactly, no tie; 6 → 73.0, `row_height/2` is 36.5, landing
*every single row* exactly on a rounding tie). Python's builtin `round()` breaks an exact .5
tie via banker's rounding (nearest *even* integer), and because 73 is odd, each successive
row's target value's integer part flips parity (450.5, 523.5, 596.5, ...) — so `round()`
alternates which way it rounds, row after row. Confirmed by hand-deriving the exact sequence
before touching any code, then matching it against the measured screenshot numbers.

Fixed with a new `_round_half_up()` helper (`math.floor(x + 0.5)`, always breaks a tie the
same direction) replacing the plain `round()` used for `row_top`/`row_center` in all three
`_bouquet_geometry_*_grouped` functions — the smallest change that fixes the shared root
cause everywhere it appears, rather than patching boolean_grouped alone. Verified
algebraically first (a standalone script confirmed uniform `73,73,73,73,73` gaps after the
change, vs. the old `74,72,74,72,74`), then confirmed against the real rebuilt container:
`track.cy` now reads `451, 524, 597, 670, 743, 816` — a perfectly uniform 73px stride.
multiple_choice_grouped and number_grouped weren't independently re-verified against a
screenshot this session (no live data was on hand to drive them at 6 groups, and they share
the exact same formula/fix, already covered by the new parametrized geometry test below) —
said plainly per this project's own "don't overclaim verification" habit.

One existing test's hard-coded expectation (`kvinna["row_center"] == 742` in the 2-group
boolean_grouped test) baked in the old banker's-rounding artifact and had to be updated to
`743` — not a behavior regression, the old value was simply the rounding bug's own output for
that particular tie. 126 tests pass (123 existing + a new parametrized test, across all three
grouped diagram types, asserting every row-to-row `row_center` gap at 6 groups is identical
and equal to the true 73px row height, which the row_top-only regression test from the
session above didn't and couldn't catch since `row_top` itself was never affected — only the
downstream `row_center` add-half-then-round step was).

## Session summary (2026-09-08, follow-up 2: Ribbon Rows horizontal spacing)

Reported from Johan looking at the same live boolean_grouped Åldersgrupp reveal as the two
sessions above (137 svar): vertical row-to-row spacing now reads fine, but "something about
the horizontal spacing is still off" — explicitly a third, separate bug from the
number/track overlap, the label `text-align`, and the row-spacing rounding parity, and not
precisely diagnosed when the work started. Measured first, guessed at never: headless
Chromium (puppeteer-core over a `nix-shell -p chromium` binary) against the real running
container, pulling `getBoundingClientRect()` for the track, seam flower, both pct labels and
the group-name/count text ink of all six rows, plus a per-column ink-coverage scan of the
rendered PNG.

**Three of the four suspected mechanisms were disproved with hard numbers first**, which
matters because the obvious suspects were all "rendered pixels don't match what Python
intended" — and none of them were:

- The seam flower is *exactly* centred on its own `seam_x` in every row (measured
  `seam.cx` = 1512/1656/1475/1643/1475/1200 against `seam_x` = the same six values;
  `transform: translate(-50%,-50%)` derives the centring from the SVG's own `width`/`height`,
  so it self-corrects at any `flower_size`, and `#bq-blossom`'s petals are mirror-symmetric
  about x=0 so the ink is centred too). Not a fixed-offset-vs-dynamic-size bug.
- Both pct labels sit at exactly `seam_x ± label_gap` (measured gap 26px on both sides in all
  six rows, `translate(-100%,·)` / `translate(0,·)` anchoring their near edges) — symmetric.
- `seam_x` itself matches `track_left + track_width × ja_pct` to the pixel at every real
  ja_pct from 50,0% to 89,3%.
- The rendered `label_left`/`label_width`/`track_left`/`track_width` matched their Python
  values exactly (140/420/620/1160).

**The real bug was that those Python values were wrong**, not that the CSS mispositioned
them: the left-hand label column reserved 420px, plus a 60px gutter before the data band, for
group-name text that renders **46–156px** wide in *every* real breakdown (measured: the
widest name in the app, "Brudgummens sida", is 156px at its own 3-group font size; the
6-group Åldersgrupp names are 46–86px). So every row carried a **393px void** between the
group name and the start of its own track (measured as one continuous run of zero ink from
x=226 to x=618 in the rendered PNG), and the data band's centre sat at x=1200 — **240px right
of x=960**, the axis the headline (ink measured 372.6..1547.4, centre 960.0), the "uppdelat
efter …" subline, the divider and the footer count are all centred on. That reads exactly as
what was reported: the diagram pushed right, a hole under the headline. The a506c73
`text-align: left` fix was correct and is untouched, but it *enlarged* this particular void
(from ~240px to ~394px) by moving the name from the middle of its column to the left edge,
which is why the complaint survived it.

Both numbers were hardcoded and unrelated to anything the column actually holds — and 480px
is not what the layout was designed to spend: the RibbonRows/Generalization mockups this
whole layout came from specify `.bool-label { width: 300px }` with the track starting
immediately after it. The implementation had drifted to 480px without that being a decision
anyone made.

Fixed in `views.py` by making the band derive from the label column instead of being three
independent magic numbers: `_BOUQUET_GROUPED_LABEL_WIDTH` 420 → 260 (the measured worst case
this column must hold on one line is "Brudgummens sida" at
`_bouquet_grouped_label_font_sizes`' 32px cap = 217.5px, so 260 clears it by ~42px and the
name can still never wrap into the next row), a new explicit
`_BOUQUET_GROUPED_LABEL_GAP = 40` (total label zone 300px, the mockup's own number), and
`_BOUQUET_GROUPED_TRACK_LEFT`/`_TRACK_WIDTH` computed from those (440/1340) so the band's
right margin equals the label column's left margin *by construction* rather than by the old
pair's coincidence.

`number_grouped` is deliberately excluded: its row is a small fixed-width cluster
(flower/value/tag), not a band-spanning track, so it keeps its own `_BOUQUET_NG_FLOWER_LEFT`
(620) and renders **pixel-identical** to before (measured: bloom 600..640, value at 750, tag
at 1070, unchanged). Dragging it left with the band would only have traded the void on its
right for a bigger one. Its own horizontal balance (cluster ends at x=1131, then nothing
until the frame at 1854, and the "MEDEL" tag sitting ~270px from the number it labels,
because those offsets are worst-case-sized and don't scale down with the row's real
flower/value sizes) is a real but *separate*, unreported design question — noted here rather
than redesigned as a side effect of someone else's bug fix.

Measured before/after on the real rebuilt container, 1920x1080, headless Chromium:

| | before | after |
|---|---|---|
| label zone (column + gutter) | 480px for ≤156px of text | 300px |
| largest void inside a row | 393px (x=226..618) | 213px (x=226..438) |
| data band | 620..1780, centre 1200 | 440..1780, centre 1110 |
| rows-band ink centroid | x=1219.8 | x=1138.0 (stage axis 960) |
| track/seam/label vertical | `cy` 451/524/597/670/743/816 | unchanged, still a uniform 73px stride |

Verified across group counts, all against the real container and real data: boolean_grouped
at Åldersgrupp (6), Sida (3 — the "Brudgummens sida" longest-label case, confirmed still one
line: label box height 45.18px at width 260, identical to its height at 420) and Kön (2);
multiple_choice_grouped at both 6 and 2 groups; number_grouped at 6 and 3 groups
(pixel-identical, as intended); plus a breakdown=Alla boolean reveal to confirm the
single-group diagrams (which use the separate `_BOUQUET_TRACK_LEFT`/`_WIDTH` pair) are
untouched. 130 tests pass (126 + 4 new: the label column being identical across all three
grouped kinds, the label zone holding the widest possible group name without stranding it,
the band's margins being symmetric on the stage, and number_grouped's cluster *not* moving
with the band; the label-zone one was confirmed to actually fail against the old 420/60
constants before being kept).

**A real side effect worth recording, since it wasn't the assignment**: widening the band
also widens `multiple_choice_grouped`'s per-option slots (1160/m → 1340/m), which measurably
reduces that diagram's own, *separate* label-collision bug — its option labels are
`white-space: nowrap`, centred on their bloom, and simply wider than a slot when the option
text is long. At Åldersgrupp (6 groups) the one overlapping pair (13.7px) is now **gone**; at
Kön (2 groups) five overlapping pairs of up to **61.2px** are down to one pair at 25.2px.
Still not fixed, and it can't be fixed by widening alone — "Något bubbligt och alkoholfritt
27,0%" is ~265px of nowrap text against a ~268px slot, and shrinking the font enough to fit
would drop below this project's own legibility floor. That needs its own decision
(truncation, shorter option labels, or dropping the non-winner labels at small group counts)
and is listed under "Possible next steps" below rather than half-fixed here.

**Not verified**: the same latent edge the geometry has always had — at a group with ja_pct
≥ ~96% or ≤ ~4% the outer pct label would run past the track's end (into the stationery
frame) or back into the label gutter, since `seam_x ± label_gap` is not clamped to the band.
The real data across all 182 (boolean question × breakdown × group) cells currently spans
9,1%–89,3%, so nothing in the app hits it today; left alone deliberately rather than adding
an unmeasured text-width fudge factor to a fix that isn't about it.

## Possible next steps

Roughly in order of how self-contained each one is — none of this is started.

1. **Wire up more of the remaining 8 concepts**, if a favorite emerges from actually using
   Podium/Bouquet at a real event. Same pattern as this session: figure out the real
   compatibility rule for the concept's data shape, extract+scope its CSS, make its
   hardcoded numbers into template variables, add a `BigScreenState.Style` value.
2. **Extend Podium to rank breakdown groups**, not just MC options — e.g. rank age groups
   by yes% on a boolean question, or relations by average on a number question. Doable
   since `_rank_podium`'s shape (label, score) pairs is already breakdown-agnostic; the
   real work is the podium/chip *layout*, which currently assumes "the MC options for one
   question" as the source and would need a second data path plus handling for group
   counts that don't neatly map to "3 podium slots." (Bouquet's equivalent of this —
   per-group breakdowns for all three diagram types, "Ribbon Rows" — shipped 2026-09-06, see
   that session summary above; this Podium extension is the one still-open piece of the
   original two-item follow-up.)
2a. **multiple_choice_grouped's option labels still collide horizontally at small group
   counts.** Each label is `white-space: nowrap` and centred on its own bloom, so a long
   option ("Något bubbligt och alkoholfritt 27,0%" ≈ 265px) simply doesn't fit its slot
   (band width / option count ≈ 268px at 5 options). Measured 2026-09-08 after the band
   widened: gone at 6 groups, one remaining pair overlapping 25,2px at 2 groups (it was five
   pairs, up to 61,2px, before). Not fixable by widening further, and shrinking the font to
   fit would break this project's legibility floor — so it needs a real decision:
   truncate with an ellipsis, shorten the option texts themselves in the question bank, or
   show only the winner's label at small group counts. Deliberately left open rather than
   half-fixed; see the 2026-09-08 follow-up-2 session summary for the numbers.
2b. **A tie-arc connector for multiple_choice_grouped**, matching the RibbonRows mockup's
   "DELAD 1:A" flourish when two blooms tie for first in a row — currently both tied blooms
   just get the prominent label independently (see the 2026-09-06 session summary's
   "deliberate scope-trim" note); the connector itself is pure decoration, not a
   readability fix, which is why it was skipped rather than being a bug.
3. **A per-question default style.** Add a field to `Question` (e.g.
   `default_style`), set once in the admin while writing/reviewing the question bank, so
   `screen_control` pre-selects it automatically instead of the host repicking Podium vs.
   Bouquet vs. Standard live for each of the ~40 questions during the reception. The
   compatibility gate (`style_is_compatible`) doesn't change — this only changes what's
   pre-selected in the form.
4. **An "auto" style.** A `BigScreenState.Style.AUTO` value that means "use the best
   compatible special style for whatever's revealed, else GENERIC" — computed the same way
   `style_is_compatible` already checks, just applied automatically instead of requiring
   the host to notice a question is MC/boolean and flip the dropdown. Natural middle
   ground before #5.
5. **Change the actual default.** Once auto-selection (#4) has been used for real and
   feels right, change `BigScreenState.Style`'s Django field `default=` away from
   `GENERIC` — either to `AUTO` (if #4 shipped) or straight to `PODIUM`/`BOUQUET` for a
   fresh `BigScreenState` row. Cheap, one-line change, but only do it after using the
   feature live — don't flip the default speculatively.
6. **Port `/screen/concepts/<n>/`'s own JS scaler** (`screen_concept_base.html`,
   `#concept-viewport`/`#concept-scale`) to the same `cqw`/`cqh` container-query-units
   technique `screen_display.html` got in the 2026-09-07 session above — same
   resize-listener bug class in principle, just not the one that was actually reported
   (deliberately left alone that session as out of scope: those are frozen-data preview
   routes, not the live host tool). Small, mechanical, same pattern already proven to work.
