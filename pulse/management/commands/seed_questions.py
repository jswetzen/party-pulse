"""Seed the question bank with a curated set of fun wedding-reception questions.

Idempotent: keyed on ``text_sv`` via ``get_or_create``, so re-running never
creates duplicates. Everything is seeded as ``draft`` — the host reviews and
flips individual questions to ``live`` from the Django admin.

Run from the repo root:

    uv run python manage.py seed_questions
"""

from django.core.management.base import BaseCommand

from pulse.models import Question

# Each entry: (text_sv, type, options). ``options`` is only meaningful for
# multiple_choice; it stays [] for boolean/number. Ordering in this list is
# preserved into Question.order so the host console shows them grouped nicely.
QUESTIONS = [
    # --- Boolean (ja/nej) ------------------------------------------------
    (
        "Har du gråtit under vigseln idag?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Har du övat ditt tal framför spegeln innan ikväll?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Har du någon gång fångat brudbuketten på ett bröllop?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Tänker du dansa kvar tills orkestern packar ihop?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Har du redan hunnit ta minst en selfie ikväll?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Kan du minst en skål-visa helt utantill?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Har du någon gång svurit på att du aldrig skulle gifta dig?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Tror du att brudparet skaffar husdjur före barn?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Har du smugit med ett par bekväma reservskor för dansgolvet?",
        Question.Type.BOOLEAN,
        [],
    ),
    (
        "Var du lite nervös inför att träffa den andra sidans släkt ikväll?",
        Question.Type.BOOLEAN,
        [],
    ),
    # --- Multiple choice (flerval) ---------------------------------------
    (
        "Hur känner du brudparet bäst?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Vän sedan barnsben",
            "Kollega eller studiekamrat",
            "Släkt",
            "Vi träffades på krogen en gång",
            "Plus one till någon annan",
        ],
    ),
    (
        "Vad är din strategi på dansgolvet?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Först ut, sist kvar",
            "Väntar tåligt på \"Dancing Queen\"",
            "Vaggar diskret vid baren",
            "Håller mig till bordet, tack",
        ],
    ),
    (
        "Vilken del av kvällen ser du mest fram emot?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Maten",
            "Talen",
            "Dansen",
            "Tårtan",
            "Baren",
        ],
    ),
    (
        "Var hittar man dig oftast under kvällen?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Mitt på dansgolvet",
            "Vid baren",
            "Vid dessertbordet",
            "På en välbehövlig frisk luft-paus",
            "Djupt i ett hjärtligt samtal i soffhörnet",
        ],
    ),
    (
        "Vad har du helst i glaset ikväll?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Bubbel hela vägen",
            "En kall öl",
            "Ett glas vin",
            "En stadig drink",
            "Alkoholfritt, jag kör ikväll",
        ],
    ),
    (
        "Hur gick det med klädkoden?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Prickade den perfekt",
            "Overdressed med flit",
            "Bekvämt underklädd",
            "Bytte outfit i sista sekund",
        ],
    ),
    (
        "Hur långt reste du för att komma hit?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Bara runt hörnet",
            "Några mil",
            "Halva landet",
            "Ända från utlandet",
        ],
    ),
    (
        "Vilken bröllopstradition gillar du mest?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Klinga i glaset så brudparet kysser varandra",
            "Brudparets första dans",
            "Tårtskärningen",
            "Kasta brudbuketten",
            "Skåltalen",
        ],
    ),
    (
        "Vad hade du med dig i present-väg?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Ett kuvert med pengar",
            "Något från önskelistan",
            "En egen kreativ överraskning",
            "Min närvaro är gåva nog",
        ],
    ),
    (
        "När tänker du smyga hem ikväll?",
        Question.Type.MULTIPLE_CHOICE,
        [
            "Jag är kvar till frukost",
            "När baren stänger",
            "Efter tårtan",
            "Innan sista bussen",
            "Vet ej, kvällen får styra",
        ],
    ),
    # --- Number (nummer) -------------------------------------------------
    (
        "På hur många bröllop har du varit i ditt liv?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många länder har du besökt?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många år har du känt brudparet (eller den du kom med)?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många glas tror du att du hinner med ikväll?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många låtar orkar du på dansgolvet innan första pausen?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många tal gissar du att det blir under middagen?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många bilder tror du att du tar med mobilen ikväll?",
        Question.Type.NUMBER,
        [],
    ),
    (
        "Hur många kramar delar du ut innan kvällen är slut?",
        Question.Type.NUMBER,
        [],
    ),
]


class Command(BaseCommand):
    help = "Seed the question bank with curated wedding-reception questions (idempotent)."

    def handle(self, *args, **options):
        created_count = 0
        existing_count = 0

        for order, (text_sv, qtype, opts) in enumerate(QUESTIONS):
            _, created = Question.objects.get_or_create(
                text_sv=text_sv,
                defaults={
                    "type": qtype,
                    "options": opts,
                    "order": order,
                    "status": Question.Status.DRAFT,
                    "source": Question.Source.HOST,
                },
            )
            if created:
                created_count += 1
            else:
                existing_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seedade frågebanken: {created_count} nya, "
                f"{existing_count} fanns redan (totalt {len(QUESTIONS)} i banken)."
            )
        )
