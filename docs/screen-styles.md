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
