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
- **Bouquet** shows one ja/nej split as a single two-ended vine, geometry computed from the
  real yes_pct (`views._bouquet_geometry`). Only a real fit for a boolean question at
  breakdown=Alla — a per-group breakdown would be several splits, a different design.

`views.style_is_compatible()` is the actual gate. Picking an incompatible style (or
revealing before anyone's answered) falls back to GENERIC **server-side** —
`screen_control.html` has a small script that disables the options that would fall back,
but that's a UI hint mirroring the real check, not the check itself.

### File map

- `pulse/models.py` — `BigScreenState.Style` choices + `style` field (migration `0005`).
- `pulse/views.py` — `style_is_compatible`, `_rank_podium`, `_bouquet_geometry`,
  `_podium_question_font_size`, and the `screen_state()`/`screen_control()` wiring.
- `pulse/templates/pulse/screen_styles/_podium.html`, `_bouquet.html` — the data-driven
  markup, included from `_screen_state.html`'s revealed branch.
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
   counts that don't neatly map to "3 podium slots."
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
