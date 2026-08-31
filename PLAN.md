# Party Pulse — plan

A party app for a wedding reception. Guests answer a fun/quirky anonymous questionnaire tagged
with light demographics; results are shown live on a big screen during the reception — either as
raw statistics, or as a guessing game where the bride and groom guess stats about their guests
before they're revealed.

Self-hosted (podman on the owner's homelab), single-event use but built to not throw away
afterward if it's fun.

## Status (2026-07-27)

**Deployed and live**: https://partypuls.mikro.swetzen.com (mikro-iac CT 123 — see that repo's
`docs/party-pulse.md` for the deployment side; this doc stays scoped to the app itself). Host
login: `johan` (password set directly on the CT, not in any config file). 39 questions are seeded
(`pulse/management/commands/data/questions.json`, loaded via `manage.py seed_questions`) — a
previous 28-question pass was revised for a dry (alcohol-free) reception and broadened beyond
pure party logistics, see git history on that JSON file.

MVP build steps 1-4 are implemented and verified end-to-end, including on the real deployment,
not just locally. Several real bugs only surfaced once actually deployed/used for real (not just
curled) — all fixed, see that repo's git log for the specifics: two separate CSRF issues (a
reverse-proxy HTTPS/origin mismatch, and two htmx forms with no CSRF token at all — the second
one silently broke the core "answer a question" flow with zero visible error), a stale-respondent
localStorage recovery gap (404 dead end → now redirects to a friendly "create a new identity"
screen), and a UI bug where the answer-choice buttons' primary/secondary styling implied one
option was "featured" over equally-valid others (fixed with a dedicated equal-weight `.choice`
component). The admin's question list also gained bulk actions (multi-select → make live/draft/
archive) — originally only bulk-delete existed.

Not yet built: step 5 (guessing-game mode is a selectable field in the host console and data
model, but the big-screen template doesn't yet render it differently from statistics mode — see
"Guessing game mode" below) and step 6 (suggestion review goes through the plain Django admin,
which already gained the bulk-action treatment above — a dedicated host-console queue UI would
be pure polish at this point, not a functional gap).

## Anonymity model

"Party anonymous," not "research anonymous." No k-anonymity/cell-suppression enforcement in the
app. Host is trusted to visually judge in real time whether a breakdown is too identifying and
simply skip showing it.

## Identity model

No accounts/login. On first visit, the guest app generates an opaque device token in
localStorage, mapping 1:1 to a `Respondent` server-side (implemented as: the Respondent's UUID
primary key *is* the token). A single device can hold multiple tokens/respondents (for shared
phones) — the guest creates a new identity with a locally-stored alias and switches between them.
The alias is NEVER sent to the server — only the opaque token/respondent id is (see
`pulse/templates/pulse/identity.html`'s inline JS). No live push notifications; new questions
only appear when the guest manually reopens/refreshes.

## Demographics

Per respondent (fixed set, columns not EAV — don't over-engineer): exact age (an integer, not a
self-reported decade bucket — "20-talet" as a dropdown was ambiguous UX and a bucket alone can't
later be un-bucketed for things like "oldest guest"; the decade label is now derived from the
exact age at aggregation/display time, see `age_bucket_label()`), sex (male/female), side
(bride's/groom's/both), relation (friend/family/plus-one). Required at first entry. See
`Respondent` in `pulse/models.py`.

## Question types

All three from day one, generic engine not type-by-type special casing: Yes/No, Multiple choice
(single-select, fixed options), Number (e.g. "how many countries have you visited?").

## Generic stats engine

Every response has a typed answer (boolean/selected option/number). Breakdown/aggregation logic
is generic across types, parameterized by (question, grouping dimension, aggregation function) —
see `pulse/aggregations.py`, covered by `pulse/tests/test_aggregations.py`:
- Grouping dimension (host-chosen): overall, by sex, by age bucket, by side, by relation
- Aggregation: Boolean → % yes per group. Multiple choice → % per option per group. Number → avg
  (default) / median / min / max / count-above-threshold per group.

New aggregations (histograms, "closest guess wins" leaderboards) can be added later without
restructuring, since `_aggregate_group` is the only place that branches on question type.

## Data model

Implemented in `pulse/models.py`:

- **Respondent**: id (uuid), age, sex, side, relation, created_at
- **Question**: id, text_sv (Swedish), type (boolean|multiple_choice|number), options (json, for
  multiple_choice), status (draft|live|archived), **order** (int — added during refinement; the
  original idea's data model specified host-console "reorder" but had no field to reorder by),
  source (host|guest_suggested), suggested_by_respondent (nullable), suggestion_review_status
  (pending|promoted|archived, nullable), created_at
- **Response**: id, respondent (fk), question (fk), answer (typed json), answered_at, unique
  constraint (respondent, question)
- **BigScreenState** (singleton, host-controlled, polled by the screen display): mode
  (statistics|guessing_game), question (nullable), breakdown (overall|sex|age|side|relation),
  aggregation (nullable, for number questions), aggregation_threshold, revealed (boolean)

## Routes / apps (three front-ends, one shared backend)

- **Guest app** (`/`, `/r/<uuid>/...`): identity picker (create new local identity with
  demographics, or resume existing local alias); questionnaire showing only live questions the
  current respondent hasn't answered; suggest-a-question free text (goes in as
  guest_suggested/pending)
- **Host console** (`/host/...`): login-gated (see "Host console auth" below); dashboard listing
  live-question and pending-suggestion counts; big-screen control panel (pick question → mode →
  breakdown [+ aggregation for number questions] → "show question" / "reveal" button); question
  management and suggestion review happen in the Django admin (`/admin/pulse/question/`), which
  the plan always intended to get "almost for free"
- **Big screen display** (`/screen/`): full-screen, no interaction, polls
  `/screen/state/` every 2s via htmx (`hx-trigger="every 2s"` — explicitly chosen over
  websockets/SSE, "start simple with polling"), renders question first with no stats, then on
  reveal shows the statistic per chosen breakdown/aggregation.
- **Guest entry QR sign** (`/qr/`): unauthenticated static signage page — QR code (server-rendered
  SVG, `pulse/qr.py`) pointing at `/`, plus playful Swedish copy, meant to be pulled up on a lobby
  screen or printed and taped up. Linked from the host console's nav grid. Same dark party palette
  on screen; `@media print` swaps to plain white/ink so a printed copy doesn't try to lay down a
  full-bleed dark background. Deliberately no htmx/live state — it's a static poster, not a view
  onto anything that changes during the party.

## Reveal semantics

Reveal always means "show the chosen statistic/breakdown" — NEVER an individual guest's identity
or answer. Only aggregated group stats are ever shown (`pulse/aggregations.py` only ever returns
grouped counts/percentages/aggregates, never a `Response` row).

## Refinements made while scaffolding (gaps in the original idea)

- **Host console auth**: the idea said "simple shared-password gated" without a mechanism.
  Resolved by reusing Django's built-in auth (`django.contrib.auth`) with one shared operator
  account (`manage.py createsuperuser`), `@login_required` on host views, `LOGIN_URL`/
  `LOGIN_REDIRECT_URL` pointed at `/host/login/`. This also means the Django admin (question
  manager, suggestion queue) shares the same login — one password for the host to remember.
- **Big screen access**: left deliberately unauthenticated — it's read-only aggregates, never
  individual answers, and gating it would just be friction for casting it to a TV. Relies on the
  venue network being effectively private, same assumption the original idea made about hosting.
- **Missing `order` field**: the host-console spec said "create/edit/**reorder**/status" but the
  original data model draft had nothing to reorder by. Added `Question.order` (int), exposed as
  `list_editable` in the admin.
- **No CDN dependencies**: htmx is vendored into `pulse/static/pulse/vendor/htmx.min.js` rather
  than loaded from a CDN. The idea's own "Hosting" section says "the real risk for the event is
  venue network reliability, not the code" — pulling a JS framework from the internet at runtime
  would directly contradict that.
- **Env-based settings**: `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS` read from env vars
  (`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`), with a hard failure at startup
  if `DJANGO_DEBUG` isn't `1` and no secret key is set — so a misconfigured production deploy
  fails loudly at boot instead of silently running with Django's generated placeholder key. See
  `.env.example`.
- **Container default DB path**: found via an actual `podman build && podman run` smoke test —
  without an explicit `DJANGO_DB_PATH`, sqlite tried to write `/app/db.sqlite3`, which isn't
  writable by the non-root container user, and failed with an opaque `unable to open database
  file` error. Fixed by baking `ENV DJANGO_DB_PATH=/data/db.sqlite3` into the image itself (see
  `Dockerfile`) so forgetting to set it in the deployment config degrades to "works but doesn't
  persist across a volume remount" instead of "container doesn't start."
- **Testing**: the idea didn't mention a test strategy. Added `pytest`/`pytest-django` covering
  `pulse/aggregations.py` — the one module with real logic (grouping + per-type aggregation
  math) — since a wrong stat shown live on the big screen is the most visible possible bug.
- **Suggestion review UX**: rather than building a bespoke review queue, suggestions submitted
  via the guest app land as `Question(status=draft, suggestion_review_status=pending)` and are
  reviewed/promoted from the Django admin's question list, filterable by
  `suggestion_review_status`. Matches the plan's own framing that admin gives this "almost for
  free."
- **Visual design**: the idea's own text never specified a look — only "styling for projector
  legibility" as a late polish step. Gave it a real, specific identity rather than default
  framework styling: a warm dusk palette (deep ink, rose, champagne gold — the reception at
  night, which is when every surface here is actually used), self-hosted Fraunces for headlines
  and the big-screen reveal numbers (one variable-weight woff2, vendored for the same
  no-runtime-CDN reason as htmx — `pulse/static/pulse/fonts/`), and a signature "pulse" waveform
  motif (`pulse/templates/pulse/_pulse_line.html`) used as the big screen's idle/thinking state
  and as the divider each reveal grows from. Verified in an actual browser (Playwright,
  screenshots at each state), not just curl — which is how two real bugs surfaced that no
  status-code check would have caught:
  - `aggregations._group` was keying breakdown groups by the raw stored value (`"bride"`) instead
    of the Swedish display label (`"Brudens sida"`) — the big screen was showing raw enum values
    to guests. Fixed by grouping on `get_<field>_display()`.
  - Django's Swedish locale renders `25.0` as `25,0` in templates. That's correct for the visible
    percentage text, but the same interpolation was also used in raw `style="width:{{ pct }}%"` —
    a comma there is invalid CSS, so every stat bar silently ignored its width and fell back to a
    default, meaning **all bars rendered the same length regardless of the actual percentage**.
    Fixed with `{% load l10n %}` + the `|unlocalize` filter specifically where a number crosses
    into CSS/HTML-attribute context, while leaving the human-readable text correctly
    comma-formatted.

## Interesting stats suggestions

A read-only "Intressanta fynd" panel on the host console (`pulse/templates/pulse/screen_control.html`,
next to but visually separate from the existing screen-control form) surfaces auto-ranked
(question, breakdown, group) findings so the host doesn't have to manually scan ~39 questions x 4
breakdowns to find something worth showing. Global scan, recomputed synchronously on every
`screen_control` page load (`pulse/suggestions.py`'s `compute_suggestions()`) — no caching, no
background job; confirmed cheap at this app's real scale (39 seeded questions x 4 breakdowns x a
handful of groups each, run twice per page load — once per scoring strategy, see below).

Three separate top-5 lists (`compute_suggestions()` returns a `SuggestionLists` with
`most_different` / `most_similar` / `biggest_pct_gap`), not one combined ranking — refined from an
earlier single-list version once it became clear a single top-10 let a handful of extreme findings
crowd out the "uncanny similarity" ones. All three still carry `is_small_sample` (below
`MIN_SAMPLE_SIZE = 5` responses; never excluded, this app's "party anonymous" model trusts the host
to judge live, not the app to silently hide data) and still only pre-fill the screen_control form
via `?question=&breakdown=` — see the click-through paragraph below, unchanged.

Scoring is a pluggable strategy (`pulse/suggestions.py`'s `SCORING_STRATEGIES` registry), both
slots now implemented, each one function per question type (mirrors
`aggregations._aggregate_group`'s "one function branching on type" pattern, not type-specific
duplication):

- **`"effect_size"`** feeds "Mest olika" (top 5 by |score| descending) and "Mest lika" (bottom 5 by
  |score|, i.e. closest to zero) — the *same* scored pool sliced from both ends in one
  `_score_pool()` call, not two independent computations, per the original decision that "uncanny
  similarity" is just this score's low end:
  - Boolean: Cohen's h between the group's yes-fraction and the overall yes-fraction.
  - Multiple choice: total variation distance between the group's option distribution and the
    overall distribution (bounded to [0, 1] regardless of option count, so it stays comparable
    across questions).
  - Number: standardized mean difference — group mean minus overall mean, divided by the overall
    population's stdev (`statistics.pstdev`, not sample stdev — PLAN.md's anonymity model treats
    the guest list as the whole population, not a sample of a larger one).
- **`"simple_pct_gap"`** feeds "Störst procentskillnad" (top 5 by |score| descending, its own
  `_score_pool()` call — a different formula, not a re-slice of the effect_size pool): a
  deliberately plain, non-normalized magnitude, for a host who'd rather eyeball a raw gap than
  reason about Cohen's h:
  - Boolean: `|group_yes_pct - overall_yes_pct|` in percentage points.
  - Multiple choice: the single option with the largest `|group_option_pct - overall_option_pct|`
    gap (the blurb names which option) — a per-option max, not the aggregate TVD above.
  - Number: raw `group_mean - overall_mean`, signed, in the question's own units — no division by
    stdev.

  Mirrors effect_size's "nothing to compare" guards (skips a boolean/number question whose overall
  is degenerate — 0%/100%, or zero spread — same as effect_size does) so both strategies agree on
  which candidates are worth ranking at all, even though they score real candidates differently.

Clicking a suggestion card links back to `screen_control` with `?question=&breakdown=` query
params, which only pre-select those two fields in the form (`views.screen_control`'s
`prefill_question_id`/`prefill_breakdown`) — it never writes to the `BigScreenState` singleton or
reveals anything; the host still has to press "Visa fråga" / "Visa resultat" themselves, same as
always. Covered by `pulse/tests/test_suggestions.py` (per-type scoring math for both strategies,
small-sample flagging flowing through all three lists, and that "Mest olika"/"Mest lika" are
genuinely opposite tails of one pool) and `pulse/tests/test_views.py` (prefill-without-persisting).

**Status (2026-08-29): shipped on `feature/age-as-number`** (commits `5e51989`, `cc4bde0`, not yet
on `main` — pending Johan's review), demo-verified end to end via `pulse/management/commands/
seed_demo_data.py` (a new, separate-from-`seed_questions` command: wipes and regenerates 150
fake respondents + ~5,300 answers with a fixed seed, deliberately biasing a handful of
question×breakdown pairs so all three lists have real signal to show, rather than pure noise).

**Group-vs-group ("pairwise") comparisons, shipped 2026-08-29 (same day, follow-up commit)**:
every score above was originally **group vs. the overall population average** only (e.g.
"kvinnor" vs. everyone). Both `"effect_size"` and `"simple_pct_gap"` now also score every
unordered pair of groups directly against each other within a breakdown (e.g. "kvinnor" vs
"män" head-to-head), feeding three new lists — "Mest olika, grupp mot grupp" / "Mest lika,
grupp mot grupp" / "Störst procentskillnad, grupp mot grupp" — alongside the original three
on the host console.

Design calls made:
- **Additive, not a replacement or a 4th variant bolted onto each existing list.** The
  original three vs-overall lists are unchanged; three new pairwise lists were added
  (`SuggestionLists` grew from 3 fields to 6). Rejected folding pairwise findings into the
  *same* ranked lists as vs-overall ones: a pairwise finding needs two group labels/sample
  sizes where a vs-overall one needs one, so a shared list would either lose that
  distinction in the UI or force the template to branch per-entry anyway — six clearly
  labeled lists reads better than one ambiguous one.
- **Scoring functions generalized, not duplicated.** Every per-type scoring function
  (`_boolean_effect_size`, `_multiple_choice_effect_size`, `_number_effect_size`, and the
  three `_pct_gap_*` siblings) now takes `(label_a, values_a, label_b, values_b)` instead of
  `(group_label, overall_values, group_values)`. A vs-overall finding is produced by calling
  the exact same functions with `label_b="totalt"`, `values_b=`the question's overall
  values — there is no separate "pairwise" scoring codepath.
- **Number's effect size needed one genuine formula change, not just a rename.** The
  original standardized-mean-difference always divided by the *overall* population's stdev.
  For a real (group_a, group_b) pair there's no single natural "the" population to divide
  by any more, and dividing by one side's own stdev would make `|score|` — and therefore the
  ranked position — depend on which group arbitrarily landed as "b" (see
  `_score_pool`'s `itertools.combinations` order, which is incidental). Pairwise number
  comparisons instead divide by a size-weighted **pooled population variance** across both
  groups (Cohen's d's usual construction), which is symmetric under swapping a/b. Known
  edge case: two groups that are each internally uniform (no spread of their own) have a
  pooled variance of exactly 0 even with a huge mean gap between them, so that pair is
  skipped for `"effect_size"` (still appears under `"simple_pct_gap"`, which never divides
  by anything) — covered by
  `test_number_pairwise_with_no_internal_spread_in_either_group_produces_no_finding`.
- **The old per-group "nothing to compare" guards (boolean: overall is 0%/100%; number:
  overall has zero spread) were hoisted to once-per-question**, not applied per pair. Every
  group's (and every pair of groups') values are a subset of a question's overall values, so
  if the overall pool has no variance, no group *or pair* drawn from it can differ either —
  a property of the question as a whole, not of which one or two groups are being compared.
  This also made the guard cheaper (one check per question instead of one per group).
- **Small-sample flagging applies per-group on both sides.** `PairwiseSuggestion.is_small_sample`
  is `True` if *either* group is below `MIN_SAMPLE_SIZE`, and the blurb/template show both
  groups' sample sizes (not just one), covered by
  `test_boolean_pairwise_small_sample_flagged_when_either_side_is_small`.
- **No new database queries.** Pairwise reuses the exact same per-question `Response` query
  and per-breakdown `aggregations._group()` result already computed for the vs-overall pool
  — `_score_pool` now returns `(vs_overall_pool, pairwise_pool)` from one pass, and only adds
  in-memory `itertools.combinations` arithmetic over groups already loaded. Age (up to 5
  decade buckets) is the one breakdown where pair count grows meaningfully — C(5,2)=10 pairs
  vs. sex's single pair — but that's still cheap per question at this app's scale.
  Measured directly against `seed_demo_data`'s 39-question/150-respondent/~5,300-answer
  dataset: `compute_suggestions()` took ~320-400ms before this change and ~345-355ms after
  (5-run min: 317ms → 343ms), i.e. no perceptible page-load slowdown, not just a theoretical
  argument.

Covered by 9 new tests in `pulse/tests/test_suggestions.py` (24 total now, one per question type's
pairwise effect_size/simple_pct_gap math, the pooled-variance edge case, small-sample
flagging on either side, that pairwise lists are additive alongside the original three, and
that age's breakdown produces exactly C(5,2)=10 pairs without truncation or blowup) plus all
pre-existing vs-overall tests passing unchanged.

**Combined "top 10, all categories" highlights, shipped 2026-08-30**: the six lists above are
each genuinely useful on their own, but Johan wanted a single "just show me the best stuff"
view at the top of the panel too, rather than making the host scan six separately-headed
lists to find what to show next. The obvious naive approach — pool every entry from all six
lists and sort by `|score|` — is actively wrong here: the six lists' scores live on
incomparable units (Cohen's h, total variation distance, a standardized mean difference, raw
percentage points, and a raw number-question unit difference), so a literal cross-scale sort
would let whichever unit happens to produce the largest raw numbers dominate the top 10 for
reasons that have nothing to do with which finding is actually most interesting. Raised with
Johan directly; he made three explicit calls, implemented exactly as decided (no
re-litigation here):

1. **Merging algorithm: a round-robin interleave, not a numeric cross-scale sort.** Take
   rank-1 from each of the four "extreme" (magnitude-ranked) pools — `most_different`,
   `most_different_pairwise`, `biggest_pct_gap`, `biggest_pct_gap_pairwise`, in that fixed
   order — then rank-2 from each, and so on until the highlights list is full. A pool that
   runs out (or was never long enough — e.g. fewer than 4 breakdowns having any pairwise
   pairs at all) is silently skipped for the rest of the round-robin, never padded and never
   an error. This sidesteps the incomparable-units problem entirely: nothing ever compares a
   Cohen's h to a raw percentage point, the four pools just take turns contributing their own
   already-correctly-sorted best candidates.
2. **Up to 2 of the 10 slots are reserved for "Mest lika" (similarity) highlights** — index 0
   (the single best/lowest-`|score|` entry) of `most_similar` and of
   `most_similar_pairwise`, one slot each. A magnitude-ranked round-robin over the four
   "extreme" pools would never naturally surface a similarity finding (it's definitionally
   the *low* end of a pool sorted by descending `|score|`), so without this carve-out the
   combined view would silently lose the "uncanny agreement" findings the two "Mest lika"
   lists exist to surface at all. If one of the two similarity pools is empty, only 1 slot is
   reserved (from whichever pool has entries); if both are empty, 0 are reserved and the
   round-robin over the four extreme pools gets to use all 10 slots instead — never an error,
   never a forced-empty slot.
3. **The six existing sections stay, unchanged, just collapsed by default** below the new
   combined view, via a plain `<details>`/`<summary>` disclosure widget in
   `screen_control.html` — no JS framework, matching this app's htmx-only/no-build-step
   approach (see "Tech stack decision" below). Nothing was removed or renamed; a host who
   wants to browse one category at a time (e.g. only "Störst procentskillnad, grupp mot
   grupp") can still expand it and see exactly what was there before this change.

Implementation is a **presentation-layer composition step, not new scoring**: a new
`Highlight` dataclass (`pulse/suggestions.py`) wraps an existing `Suggestion` or
`PairwiseSuggestion` instance — never re-scores or recomputes anything — with whatever a
combined card needs to render standalone: `category`/`category_label` (which of the six
lists it came from, reusing the exact Swedish `<h3>` headings already in the template
verbatim, e.g. "Mest olika, grupp mot grupp"), `score_label` ("Avvikelse" for the two
effect_size-based categories, "Skillnad" for the two simple_pct_gap-based ones — matching
each section's own existing score-line wording), `is_pairwise` plus a pre-formatted
`group_display` ("kvinnor" vs. "kvinnor vs män") and `sample_size_display` ("n=6" vs. "n=6 vs
n=4") so the template never has to branch on vs-overall-vs-pairwise shape itself, and
`is_small_sample`/`score_display`/`blurb`/`question_id`/`question_text`/`breakdown`/
`breakdown_label` carried straight through. Deliberately does NOT carry the raw `score`
float — keeping it off this dataclass makes a future "just sort by score" regression
structurally impossible, not just discouraged by convention. `SuggestionLists` grew a
`highlights: list[Highlight]` field, built by a new `_build_highlights()` after the six pools
are assembled (round-robin + reservation exactly as described above, plus a `seen`-set
de-dup pass across *both* phases keyed on `(question_id, breakdown, group-or-frozenset-of-
group-pair)` — needed because the same `(question, breakdown, group)` vs-overall finding can
legitimately be the extreme of *both* the effect_size and simple_pct_gap pools at once, and a
small enough pool can make `most_similar[0]` literally the same entry as
`most_different[0]`; either way the combined view shows that finding once, not twice under
two labels).

`screen_control.html`: a new "Topp 10, alla kategorier" card list sits at the top of the
`.suggestions` section (same `.suggestion-card`/`.suggestion-list` markup and click-through
as every other card — still only pre-fills the form via `?question=&breakdown=`, still never
writes state), with a small `.badge--category` tag per card naming its source category and a
`.suggestion-card--similarity` left-border accent on the (at most two) reserved "Mest lika"
picks so they read as intentionally pinned rather than randomly out of place among the
higher-magnitude round-robin picks. The six original sections follow inside a single
`<details>` (closed by default) with one `<summary>Visa alla sex kategorier var för
sig</summary>` — Johan's decision left the exact collapse granularity (one `<details>` for
all six vs. one per section) to implementation; one shared disclosure was chosen since the
six sections already read as one coherent "detailed breakdown" unit once collapsed out of the
way, rather than something a host would want to reveal one at a time.

Covered by 8 new tests in `pulse/tests/test_suggestions.py` (32 total now): the round-robin +
reservation logic is checked via an independent reference re-implementation of the spec
(`_expected_highlight_keys()`, operating only on the six already-tested pools — a merge-logic
test, deliberately not re-deriving the underlying per-type scoring math those other tests
already cover) for a normal varied dataset, a heavy-scoring-ties dataset (the scenario most
likely to make two categories pick the exact same finding), a completely-empty-pairwise-pools
dataset (every respondent sharing identical demographics, so zero pairs exist anywhere), and
a pools-shorter-than-`TOP_N` dataset — plus direct checks that category/score labels match
their source list, that exactly one reserved slot exists per non-empty similarity pool (and
never more), that the combined list never exceeds 10 entries, and that it's an empty list
(not an error) when every one of the six pools is empty.

## Backlog / explicitly out of scope for now

Guest upvoting on suggested questions; statistics projections/trends over the evening; live push
notifications; more demographic fields beyond the four listed (easy to add later, don't build a
flexible EAV model preemptively); larger question bank and demographic field brainstorm beyond
the 28 seeded questions (content work, not architecture).

## Suggested build order (MVP first)

1. ~~Data model + backend API~~ — done
2. ~~Guest app identity + seeded questions~~ — done (28 questions via `seed_questions`)
3. ~~Host console question manager (via admin) + big screen control panel~~ — done
4. ~~Big screen display with polling, statistics mode only~~ — done
5. **Guessing game mode** — `BigScreenState.mode` exists and is selectable in the host console,
   but `pulse/templates/pulse/_screen_state.html` doesn't yet render it differently from
   statistics mode (no "couple guesses first, then reveal" framing/UI). Next thing to build.
6. Dedicated suggestion submission + review queue UI (currently: admin) — optional polish, admin
   already covers the functional need
7. Polish: styling pass for projector legibility beyond the current baseline CSS, verify Swedish
   copy throughout

## Tech stack decision

Django + HTMX + SQLite, managed with `uv` (lockfile + container build both use it — see
`pyproject.toml`/`uv.lock` and the multi-stage `Dockerfile`). Rationale: owner knows Django (won't
personally read the code — Claude Code will build/maintain it, but Django familiarity de-risks
review if needed); Django admin gives the host console's question manager and suggestion-review
queue almost for free; HTMX avoids any Node/JS build step, sidestepping known pain running
Next.js-style frontends in containers; polling is trivial via `hx-trigger="every 2s"` matching the
"start simple with polling" decision; SQLite is sufficient at wedding-guest-list scale (Postgres a
two-line swap later if needed); runs cleanly under podman as gunicorn + WSGI, nothing
Django-specific fights container runtimes.

Considered alternatives: FastAPI+Jinja2+HTMX (lighter but loses free Django admin), Node+SvelteKit
(nicer big-screen animations but new ecosystem + still has a JS build step), plain static
HTML+vanilla JS (zero framework risk but hand-rolled polling/state logic).

## Hosting

Self-hosted on the owner's Proxmox homelab via podman, following the same pattern as this
household's other small apps (see `mikro-iac` repo's `docs/poc-lyrics.md` for the conventions
this repo's `Dockerfile`/`.github/workflows/docker-build.yml` mirror: multi-stage Alpine build,
`uv sync --frozen`, image published to `ghcr.io/jswetzen/party-pulse` on push to `main`, pulled
by a deployment CT). Single small web app + SQLite on a mounted `/data` volume, no separate DB
container needed. The mikro-iac side (terraform CT, Traefik route, secrets snippet) is a separate
follow-up, not part of this repo.

Entire guest-facing UI, host console, and question bank are in Swedish.
