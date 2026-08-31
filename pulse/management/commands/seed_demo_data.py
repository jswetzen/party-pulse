"""Populate the DB with a large batch of realistic fake respondents + answers, purely so
the "Interesting stats" suggestions panel (pulse/suggestions.py) and the rest of the host
console have something non-trivial to show during local/manual testing. Never intended to
run against a real event's database.

Design choices, spelled out because none of them are forced by the schema:

- **Idempotency = full wipe, not tagging.** Every `Respondent` and `Response` row is
  deleted before regenerating (both tables are 100% demo-generated content in any DB where
  this command is ever run -- a real event seeds guests through the guest app, never
  through this command). Tagging demo rows and only clearing "ours" would need a marker
  field nothing else uses; a blanket wipe is simpler and exactly as safe here. `Question`
  rows are never deleted by this command.
- **This command flips every non-system `Question` to LIVE.** `seed_questions` seeds the
  bank as all-`draft` (a real host reviews and promotes questions by hand in admin) but
  `compute_suggestions()`/`compute_breakdown()` only ever look at LIVE questions -- without
  this step the suggestions panel would have nothing to rank no matter how much respondent
  data exists. This is the one way this command touches `Question` rows; it never creates,
  edits the text/type/options of, or deletes any.
- **Ages are drawn from a weighted mixture of five clipped Gaussians**, not
  `random.randint(AGE_MIN, AGE_MAX)`, to look like an actual wedding guest list: a handful
  of kids/teens, a big cluster of 20s-40s friends, a smaller cluster of parents'-generation
  guests, and a few grandparent-age elders. See AGE_BANDS below.
- **A handful of (question, breakdown-dimension) pairs are deliberately biased** so the
  three ranked lists have real signal to surface instead of pure noise indistinguishable
  from sampling jitter -- see BOOLEAN_BIASES / MC_BIASES / NUMBER_BIASES. Every other
  question x dimension combination is intentionally left unbiased: with ~150 respondents
  split across 2-3 groups per dimension, unbiased combos already cluster near zero effect
  size on their own, which is what populates "Mest lika" without needing to force it.
- **Fixed random seed** (see RNG_SEED) so re-running with the same --count reproduces the
  same respondents/answers, making it easy to eyeball the same "interesting" entries again
  after a code change to suggestions.py.

Run from the repo root (needs the question bank seeded first):

    uv run python manage.py seed_questions
    uv run python manage.py seed_demo_data [--count 150]
"""

import random

from django.core.management.base import BaseCommand
from django.db import transaction

from pulse.models import Question, Respondent, Response

RNG_SEED = 20260829  # today's date on the dev box this was written on; any fixed int works

# ---------------------------------------------------------------------------
# Age distribution: weight, then a Gaussian (mean, stdev) clipped to (lo, hi).
# Weights sum to 1.0. Modeled as "mostly the couple's own generation, plus a
# realistic sprinkling of kids and elders" rather than uniform-random ages.
# ---------------------------------------------------------------------------
AGE_BANDS = [
    (0.05, 8, 3, 5, 12),      # kids
    (0.05, 16, 2, 13, 19),    # teens
    (0.55, 32, 7, 20, 49),    # core wedding-guest generation
    (0.25, 55, 6, 46, 65),    # parents' generation
    (0.10, 72, 6, 66, 85),    # grandparents' generation
]

SEX_CHOICES = [Respondent.Sex.MALE, Respondent.Sex.FEMALE]
SIDE_CHOICES = [Respondent.Side.BRIDE, Respondent.Side.GROOM, Respondent.Side.BOTH]
SIDE_WEIGHTS = [0.43, 0.43, 0.14]  # "both" is deliberately the rare option
RELATION_CHOICES = [Respondent.Relation.FRIEND, Respondent.Relation.FAMILY, Respondent.Relation.PLUS_ONE]
RELATION_WEIGHTS = [0.45, 0.35, 0.20]

# Chance a given respondent answers any given live question at all -- some missing answers
# per respondent is realistic (guests wander off, skip questions, etc.), a 100% answer rate
# is not.
ANSWER_RATE = 0.88

# ---------------------------------------------------------------------------
# Per-number-question baseline (min, max, mean, stdev), inferred from each question's
# Swedish phrasing -- e.g. "how many countries have you visited" gets a wider, higher-mean
# range than "how many coffees do you drink on a normal day". Any number question not
# listed here (there shouldn't be any, given the seeded bank, but new questions added to
# the bank later would hit this) falls back to a generic small-count range.
# ---------------------------------------------------------------------------
NUMBER_BASELINES = {
    "På hur många bröllop har du varit i ditt liv?": (0, 25, 4, 4),
    "Hur många länder har du besökt?": (0, 40, 10, 8),
    "Hur många år har du känt brudparet (eller den du kom med)?": (0, 40, 8, 7),
    "Hur många låtar orkar du på dansgolvet innan första pausen?": (1, 15, 6, 3),
    "Hur många tal gissar du att det blir under middagen?": (0, 10, 3, 2),
    "Hur många bilder tror du att du tar med mobilen ikväll?": (0, 300, 40, 35),
    "Hur många kramar delar du ut innan kvällen är slut?": (0, 60, 12, 8),
    "Hur många koppar kaffe dricker du en vanlig dag?": (0, 8, 2, 1.2),
    "Hur många mil reste du för att komma hit idag?": (0, 800, 60, 90),
    "Hur många år tror du att brudparet känt varandra innan idag?": (1, 20, 7, 4),
    "Hur många barn tror du att brudparet har om tio år?": (0, 5, 2, 1),
    "Hur många kanelbullar kan du äta i ett sträck?": (1, 12, 3, 2),
    "Hur många av gästerna här ikväll känner du sedan innan?": (0, 80, 15, 12),
}
_GENERIC_NUMBER_BASELINE = (0, 20, 5, 4)

# ---------------------------------------------------------------------------
# Deliberate signal. Each entry biases ONE breakdown dimension of ONE question so that
# dimension's groups diverge (or, for the "similar" showcases, are simply left alone so
# they converge on the shared baseline) -- see module docstring. Dimensions match
# aggregations.py's breakdown fields: "sex", "age", "side", "relation". Group labels are
# the exact Swedish display labels compute_breakdown()/_group() produce.
#
# BOOLEAN_BIASES: {question_text: {dimension: {group_label: p_yes}}}. Any group label not
# listed for a biased question's chosen dimension, and any dimension not listed at all,
# uses that question's random baseline p (BASELINE_BOOL_P, built at runtime below).
# ---------------------------------------------------------------------------
BOOLEAN_BIASES = {
    # Big, clean divergence by sex -- feeds "Mest olika" / "Störst procentskillnad".
    "Har du gråtit av lycka under vigseln idag?": {
        "sex": {"Kvinna": 0.82, "Man": 0.30},
    },
    # Age-graded: young guests plan to dance all night, the oldest generation mostly not.
    "Tänker du dansa kvar tills orkestern packar ihop?": {
        "age": {"Under 20": 0.85, "20-talet": 0.75, "50 eller äldre": 0.15},
    },
    # Plus-ones are nervous meeting "the other side"'s family; friends/family of either
    # side mostly aren't -- thematically sensible, not just noise.
    "Var du lite nervös inför att träffa den andra sidans släkt ikväll?": {
        "relation": {"Plus en": 0.78, "Vän": 0.30, "Familj": 0.25},
    },
    # Friends are far more willing to improvise a speech than family members put on the
    # spot -- another relation-graded divergence, on a different question than the one
    # above so "Mest olika" isn't dominated by a single dimension.
    "Skulle du våga hålla ett improviserat tal om värden ropade upp dig just nu?": {
        "relation": {"Vän": 0.72, "Familj": 0.28},
    },
    # --- "Mest lika" showcases: intentionally NOT biased on these dimensions, despite a
    # naive guess that they might diverge (women catching bouquets more often; one side
    # feeling differently about pets-before-kids). Left to the shared random baseline.
    "Har du någon gång fångat brudbuketten på ett bröllop?": {},
    "Tror du att brudparet skaffar husdjur före barn?": {},
}

# MC_BIASES: {question_text: {dimension: {group_label: {option: weight, ...}}}}. A group
# label present here gets this exact weight table (rng.choices handles normalization);
# absent group labels fall back to that question's random baseline weights.
MC_BIASES = {
    # Older guests skew hot-drink/non-fizzy, under-20s skew sweet/fizzy -- age-graded
    # multiple-choice divergence (TVD-scored) to complement the boolean ones above.
    "Vad fyller du glaset med ikväll?": {
        "age": {
            "50 eller äldre": {
                "Kaffe eller te": 5,
                "Saft eller lemonad": 1,
                "Ett stort glas kallt vatten": 1,
                "Något bubbligt och alkoholfritt": 1,
                "Fläderdryck eller något med bär": 2,
            },
            "Under 20": {
                "Kaffe eller te": 0.3,
                "Saft eller lemonad": 5,
                "Ett stort glas kallt vatten": 1,
                "Något bubbligt och alkoholfritt": 4,
                "Fläderdryck eller något med bär": 1,
            },
        },
    },
    # Family looks forward to the speeches, friends look forward to the dancing --
    # relation-graded divergence on a question with 5 options (a richer TVD case than a
    # 2-option one).
    "Vilken del av kvällen ser du mest fram emot?": {
        "relation": {
            "Familj": {"Maten": 1, "Talen": 5, "Dansen": 1, "Tårtan": 1.5, "Fikat": 1},
            "Vän": {"Maten": 1, "Talen": 1, "Dansen": 5, "Tårtan": 1, "Fikat": 1},
        },
    },
    # --- "Mest lika" showcases: dress-code outcome by relation, and travel-distance
    # bucket by sex -- both left unbiased on purpose.
    "Hur gick det med klädkoden?": {},
    "Hur långt reste du för att komma hit?": {},
}

# NUMBER_BIASES: {question_text: {dimension: {group_label: (mean, stdev)}}}. A listed group
# label overrides the mean/stdev used when sampling that group's answers (still clipped to
# the question's baseline min/max); unlisted labels use the question's baseline mean/stdev.
NUMBER_BIASES = {
    # Family (skews older, per AGE_BANDS) drinks noticeably more coffee than the baseline
    # -- relation-graded divergence on a NUMBER question (standardized-mean-diff scored).
    "Hur många koppar kaffe dricker du en vanlig dag?": {
        "relation": {"Familj": (4.5, 1.3)},
    },
    # A silly, sharply divergent one by side, purely for a fun "most different" entry.
    "Hur många kanelbullar kan du äta i ett sträck?": {
        "side": {"Brudens sida": (6, 2), "Brudgummens sida": (1.5, 1)},
    },
    # Under-20s take way more phone photos than the 50+ crowd.
    "Hur många bilder tror du att du tar med mobilen ikväll?": {
        "age": {"Under 20": (140, 40), "50 eller äldre": (12, 8)},
    },
    # --- "Mest lika" showcases: travel distance by side, and years-known-the-couple by
    # sex -- both left unbiased on purpose (you might expect e.g. one side to have
    # travelled further if the venue is "local" to the other side, but here it's the same
    # shared distribution for both).
    "Hur många mil reste du för att komma hit idag?": {},
    "Hur många år har du känt brudparet (eller den du kom med)?": {},
}


def _dim_label(respondent: Respondent, dimension: str) -> str:
    if dimension == "sex":
        return respondent.get_sex_display()
    if dimension == "age":
        return respondent.age_bucket_label
    if dimension == "side":
        return respondent.get_side_display()
    if dimension == "relation":
        return respondent.get_relation_display()
    raise ValueError(f"unknown dimension: {dimension}")


class Command(BaseCommand):
    help = "Wipe and regenerate a large batch of fake demo respondents + answers (local/manual testing only)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=150, help="Number of fake respondents to create (default 150).")

    def handle(self, *args, **options):
        count = options["count"]
        rng = random.Random(RNG_SEED)

        with transaction.atomic():
            deleted_responses = Response.objects.all().count()
            deleted_respondents = Respondent.objects.all().count()
            Response.objects.all().delete()  # cascades from Respondent too, but be explicit
            Respondent.objects.all().delete()

            live_questions_qs = Question.objects.filter(is_system=False)
            flipped = live_questions_qs.exclude(status=Question.Status.LIVE).update(status=Question.Status.LIVE)
            live_questions = list(live_questions_qs)
            if not live_questions:
                self.stdout.write(
                    self.style.WARNING(
                        "Inga frågor i frågebanken -- kör `manage.py seed_questions` först."
                    )
                )
                return
            system_age_question = Question.system_age_question()

            # Pre-roll each question's random "baseline" so unbiased dimensions of a biased
            # question, and every dimension of an unbiased question, still get a believable
            # non-50/50 (or non-uniform) shape instead of every question secretly looking
            # identical.
            baseline_bool_p = {
                q.text_sv: rng.uniform(0.3, 0.7) for q in live_questions if q.type == Question.Type.BOOLEAN
            }
            baseline_mc_weights = {
                q.text_sv: {opt: rng.uniform(1, 4) for opt in q.options}
                for q in live_questions
                if q.type == Question.Type.MULTIPLE_CHOICE
            }

            respondents = []
            for _ in range(count):
                band_weights = [b[0] for b in AGE_BANDS]
                band = rng.choices(AGE_BANDS, weights=band_weights, k=1)[0]
                _, mean, stdev, lo, hi = band
                age = round(rng.gauss(mean, stdev))
                age = max(lo, min(hi, age))

                sex = rng.choice(SEX_CHOICES)
                side = rng.choices(SIDE_CHOICES, weights=SIDE_WEIGHTS, k=1)[0]
                relation = rng.choices(RELATION_CHOICES, weights=RELATION_WEIGHTS, k=1)[0]

                respondents.append(Respondent(age=age, sex=sex, side=side, relation=relation))

            Respondent.objects.bulk_create(respondents)

            age_responses = [
                Response(respondent=r, question=system_age_question, answer={"value": r.age}) for r in respondents
            ]
            Response.objects.bulk_create(age_responses)

            answer_responses = []
            for r in respondents:
                for q in live_questions:
                    if rng.random() > ANSWER_RATE:
                        continue  # this respondent skipped this question -- realistic missingness
                    value = self._answer_value(q, r, rng, baseline_bool_p, baseline_mc_weights)
                    answer_responses.append(Response(respondent=r, question=q, answer={"value": value}))
            Response.objects.bulk_create(answer_responses)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rensade {deleted_respondents} respondenter / {deleted_responses} svar. "
                f"Skapade {len(respondents)} nya respondenter, {len(age_responses)} ålderssvar, "
                f"{len(answer_responses)} vanliga svar över {len(live_questions)} live-frågor "
                f"({flipped} frågor sattes till live)."
            )
        )

    @staticmethod
    def _answer_value(question: Question, respondent: Respondent, rng, baseline_bool_p, baseline_mc_weights):
        if question.type == Question.Type.BOOLEAN:
            p = baseline_bool_p[question.text_sv]
            for dimension, overrides in BOOLEAN_BIASES.get(question.text_sv, {}).items():
                label = _dim_label(respondent, dimension)
                if label in overrides:
                    p = overrides[label]
                    break
            return rng.random() < p

        if question.type == Question.Type.MULTIPLE_CHOICE:
            weights_map = baseline_mc_weights[question.text_sv]
            for dimension, overrides in MC_BIASES.get(question.text_sv, {}).items():
                label = _dim_label(respondent, dimension)
                if label in overrides:
                    weights_map = overrides[label]
                    break
            options = list(weights_map.keys())
            weights = [weights_map[opt] for opt in options]
            return rng.choices(options, weights=weights, k=1)[0]

        if question.type == Question.Type.NUMBER:
            lo, hi, mean, stdev = NUMBER_BASELINES.get(question.text_sv, _GENERIC_NUMBER_BASELINE)
            for dimension, overrides in NUMBER_BIASES.get(question.text_sv, {}).items():
                label = _dim_label(respondent, dimension)
                if label in overrides:
                    mean, stdev = overrides[label]
                    break
            value = round(rng.gauss(mean, stdev))
            return max(lo, min(hi, value))

        raise ValueError(f"unknown question type: {question.type}")
