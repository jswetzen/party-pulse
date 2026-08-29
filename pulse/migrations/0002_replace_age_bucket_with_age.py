# Replaces Respondent.age_bucket (a self-reported "which decade" dropdown) with
# Respondent.age (an exact integer). Rationale: "20-talet" etc. was ambiguous
# self-report UX (a 19- or 30-year-old could round either way), and an exact age
# is both easier to enter correctly and is the raw data future features like
# "oldest guest" / "highest average age per side" will need -- a bucket alone
# can't be un-bucketed. See PLAN.md "Demographics".
#
# This app already collected real guest data at the 2026-07-27 event, so this
# migration converts existing age_bucket values instead of just dropping the
# column and letting `age` come out null/zero for anyone who already answered.
# There's no way to recover the *exact* age each guest actually is from a stored
# bucket, so we use the bucket's midpoint as the best available stand-in
# (20s->25, 30s->35, 40s->45, 50+->55, picked as the middle of each ~10-year
# span -- 50+ has no natural upper bound, so 55 just continues the same
# 10-year-span convention rather than guessing a real ceiling). This keeps
# aggregate stats (averages, bucket counts) roughly sane instead of introducing
# a pile of nulls or zeros into historical data. Any host-facing display of an
# *individual* migrated guest's age will look artificially round (25/35/45/55);
# that's an acceptable, visible trade-off for not losing the row's bucket
# information entirely.
#
# The reverse direction (age -> age_bucket) is NOT lossy in the same way --
# Respondent.age_bucket_label() (pulse/models.py) derives the exact same decade
# label from any age, so `migrations.RunPython`'s reverse function below reuses
# that instead of duplicating the bucket boundaries here.
#
# Operation order below is more deliberate than it looks: age_bucket is loosened
# to nullable *before* being dropped (rather than dropped directly), purely so
# that this migration stays cleanly reversible against a non-empty table --
# SQLite's schema-change strategy rebuilds the whole table, and re-adding a NOT
# NULL column with no default (which is what a plain, un-loosened RemoveField
# would reverse into) fails outright once rows exist. Loosening first means the
# reverse path re-adds age_bucket as nullable, repopulates it via the RunPython
# step's reverse function, and only then re-tightens it to NOT NULL -- so
# `migrate pulse 0001` works on a real, populated database, not just an empty
# dev one.
import django.core.validators
from django.db import migrations, models

_ORIGINAL_AGE_BUCKET_CHOICES = [
    ("20s", "20-talet"),
    ("30s", "30-talet"),
    ("40s", "40-talet"),
    ("50+", "50 eller äldre"),
]


def bucket_to_age(apps, schema_editor):
    Respondent = apps.get_model("pulse", "Respondent")
    midpoint = {"20s": 25, "30s": 35, "40s": 45, "50+": 55}
    for respondent in Respondent.objects.all():
        respondent.age = midpoint[respondent.age_bucket]
        respondent.save(update_fields=["age"])


def age_to_bucket(apps, schema_editor):
    # Only reachable by explicitly unapplying this migration (`migrate pulse 0001`);
    # reuses the real bucketing rule from pulse/models.py rather than re-deriving decade
    # boundaries here, so the two can't drift out of sync.
    from pulse.models import age_bucket_label

    label_to_bucket = {
        "Under 20": "20s",  # closest available bucket pre-migration; see module docstring above
        "20-talet": "20s",
        "30-talet": "30s",
        "40-talet": "40s",
        "50 eller äldre": "50+",
    }
    Respondent = apps.get_model("pulse", "Respondent")
    for respondent in Respondent.objects.all():
        respondent.age_bucket = label_to_bucket[age_bucket_label(respondent.age)]
        respondent.save(update_fields=["age_bucket"])


class Migration(migrations.Migration):

    dependencies = [
        ("pulse", "0001_initial"),
    ]

    operations = [
        # 1. Add the new column nullable so we can populate it row-by-row below
        #    before enforcing NOT NULL -- adding it NOT NULL up front would force
        #    a single default value onto every existing row.
        migrations.AddField(
            model_name="respondent",
            name="age",
            field=models.PositiveSmallIntegerField(null=True),
        ),
        # 2. Loosen age_bucket to nullable *before* it's dropped -- see the
        #    "Operation order" note above for why this matters for reversibility.
        migrations.AlterField(
            model_name="respondent",
            name="age_bucket",
            field=models.CharField(choices=_ORIGINAL_AGE_BUCKET_CHOICES, max_length=8, null=True),
        ),
        migrations.RunPython(bucket_to_age, age_to_bucket),
        # 3. Now that every row has a real age, tighten the column to match the
        #    model (NOT NULL, with the guest-facing bounds/messages).
        migrations.AlterField(
            model_name="respondent",
            name="age",
            field=models.PositiveSmallIntegerField(
                validators=[
                    django.core.validators.MinValueValidator(1, message="Åldern måste vara minst 1 år."),
                    django.core.validators.MaxValueValidator(
                        119, message="Åldern verkar inte stämma, kolla att du skrev rätt."
                    ),
                ]
            ),
        ),
        migrations.RemoveField(
            model_name="respondent",
            name="age_bucket",
        ),
    ]
