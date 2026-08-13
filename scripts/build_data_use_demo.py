"""Build the bilingual data-use demonstration flow, in English and Spanish.

This is the repo's worked example: a randomised methods demonstration in which
participants are split into two arms that receive the *same four questions* in
different formats, and the close reveals the experiment.

* **ARM 1** is the counter-example: dense prose, cold register, no progress
  cues, and a demand to type the answer.
* **ARM 2** is the recommendation: the same four constructs in plain language,
  as a tappable list, warm in tone and paced so the respondent knows where they
  are.

ARM 2 is not merely the easier arm - it is the pattern this repo recommends, and
the session's conclusion. Every choice in it is deliberate and worth copying:
tappable options over typed input, one idea per message, "Question 2 of 4" so
break-off is a decision and not confusion, and a warm but neutral voice - no
persuasion, no urgency, no incentives. ARM 1 exists to make the size of that
difference measurable rather than asserted.

The contrast is the pedagogical payload: it shows how much of a "finding" is
really an artefact of how the question was asked. It first ran in Spanish at the
Foro Nacional de Datos in Bogota; the English instance runs at Research Staff
Training 2026 in Jaipur.

Why one builder for two languages
---------------------------------
Because the alternative already bit this account. Seven flows here share one
identical break-off path that never reaches the publish widget - the same defect
copied six times as flows were cloned from one another, six of them published.
Two hand-maintained language variants of the same instrument is that same
machine, waiting. Here the structure exists once and the language tables carry
only strings, so a fix to the graph fixes both languages and `check_flow` runs
over both on every build.

Why the options are list pickers and not "reply with a number"
--------------------------------------------------------------
ARM 2 used to *simulate* answer options by printing a numbered menu and asking
people to type the digit. WhatsApp has real interactive messages, and IPA's house
preference is buttons and lists over open text, so ARM 2 now sends
`twilio/list-picker` menus and consent sends `twilio/quick-reply` buttons.

The approval rules are what make this cheap, and they are worth stating plainly
because they drive the whole design:

===========================  ==============================  ====================
Content type                 In-session (24h window)         Business-initiated
===========================  ==============================  ====================
twilio/text                  free                            needs approval
twilio/quick-reply           free                            needs approval
twilio/list-picker           free                            **not supported**
===========================  ==============================  ====================

So only the *opener* needs Meta approval, because only the opener is sent before
the respondent has said anything. Consent and all eight questions land inside the
24-hour window the opener's reply opens, so their templates are created and used
without ever being submitted. A list picker sent as the first message fails with
error 63016 - `check_flow` has a check for exactly that.

Answers still arrive as text, so the splits accept **tap or type**: a respondent
who ignores the menu and writes "1" is matched just the same. That also keeps the
retry machinery meaningful - it now only fires for genuinely unparsable typed
input, which is itself one of the findings the demo shows off.

Why the splits use `regex` and not `matches_any_of`
---------------------------------------------------
Because `matches_any_of` takes its alternatives as a single comma-delimited
string. A comma inside an option label silently becomes two alternatives,
neither of which is the label, and the respondent taps a real option and lands
on noMatch. In a regex a comma is just a character. Regex also lets the digit
form tolerate the punctuation people actually type - "1.", "1)", "(1)".

Both predicates are already case-insensitive and already trim surrounding
whitespace, so neither is what breaks.

The deeper fix is not the predicate though. It is that `check_language` **runs**
every condition it generates - every label, every digit, and a handful of things
nobody would ever tap - and checks where each one lands. A condition that looks
right and matches nothing is the same class of defect as a break-off that
publishes no row: invisible in the editor, obvious only in the data.

Run with `just build-demo-flow`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from requests_to_twilio import config as cfg  # noqa: E402
from requests_to_twilio import templates as tpl  # noqa: E402
from requests_to_twilio.flows import check_flow, evaluate_condition  # noqa: E402

FLOW_DIR = REPO_ROOT / "flows"
TEMPLATE_DIR = REPO_ROOT / "templates" / "generated"

FROM = "{{flow.channel.address}}"
TIMEOUT = "3600"  # 1 hour, as in the source flow

#: Deployed Functions service. Both languages share it: the encryption and
#: publish code is language-independent, and duplicating it would reintroduce
#: exactly the drift this builder exists to prevent.
ENCRYPT_SERVICE_SID = "ZS04f75bf125e71003387d709e77f1f6ad"
ENCRYPT_ENVIRONMENT_SID = "ZE73bad56bc5cba5c3c4b5fe6bcba2dc92"
ENCRYPT_FUNCTION_SID = "ZH477834617e55e948fc9149388bf1ef63"
PUBLISH_SERVICE_SID = "ZS04f75bf125e71003387d709e77f1f6ad"
PUBLISH_ENVIRONMENT_SID = "ZE73bad56bc5cba5c3c4b5fe6bcba2dc92"
PUBLISH_FUNCTION_SID = "ZH08a4b1579e90c2e3a6ea7829a153e842"

#: Studio requires the deployed URL on a run-function widget, not just the SIDs;
#: validation rejects the flow without it.
FUNCTIONS_DOMAIN = "rtt-survey-2647-prod.twil.io"
ENCRYPT_URL = f"https://{FUNCTIONS_DOMAIN}/encrypt-fields"
PUBLISH_URL = f"https://{FUNCTIONS_DOMAIN}/publish-motherduck"

QUESTION_KEYS = ("P1", "P2", "P3", "P4")


# ---------------------------------------------------------------------------
# Language tables.
#
# Everything below is strings. No structure, no transitions, no widget names -
# those live once, in build(). An option's label appears exactly once and is
# used for the list-picker item, the split condition and the code mapping, so
# the message a respondent sees and the value stored for them cannot disagree.
#
# check_language() enforces the limits on this text, and does it by *executing*
# the conditions it generates rather than by checking style rules:
#
#   * Length. A list-picker item title caps at 24 characters, its description at
#     72, a quick-reply button title at 25, and the list button at 20.
#   * Reachability. Every label and every position typed as a digit is pushed
#     through the generated pattern and must route to the store widget; junk
#     must not. A pattern loose enough to accept anything is worse than one that
#     accepts too little - it stores junk as a real answer and never re-asks.
#   * Agreement. Whatever the split accepts, the code mapping must code as that
#     option, not as `other`.
#
# Commas in labels are fine. They were not when the splits used matches_any_of,
# whose alternatives are one comma-delimited string; the regex predicate has no
# delimiter, which is the main reason for the switch.
#
# The one thing still forbidden by rule rather than by test is EMOJI IN A LABEL.
# Labels are compared literally against the reply body, so every byte has to
# survive a round trip through WhatsApp, Studio and the condition - and
# skin-tone and variation selectors make two visually identical labels different
# strings. A test cannot catch that, because it compares the string to itself.
# Warmth belongs in the message body, which nothing matches on; labels stay
# boring on purpose.
# ---------------------------------------------------------------------------

EN: dict[str, Any] = {
    "name": "English",
    "flow_suffix": "en",
    "language": "en",
    # Already reviewed in templates/rst2026_intro.json and pending Meta
    # approval. The opener is the only piece of this flow that needs it.
    "intro_template": "rst2026_intro",
    "description": "Data use demo (ARM1/ARM2 experiment) - English, RST Jaipur 2026",
    "consent": {
        "body": (
            "👋 Before we start - would you like to take part?\n\n"
            "It takes about 3 minutes. Taking part is voluntary, you can stop "
            "at any time by not replying, and your answers are confidential."
        ),
        "button_yes": "Yes I will take part",
        "button_no": "No thanks",
        "typed_yes": "1|yes|y",
        "typed_no": "2|no|n",
    },
    # ARM 1 - open and dense. Deliberately harder to answer; this is the arm
    # whose break-off and unusable-answer rates the session compares against.
    "arm1": {
        "P1": (
            "In the last four (4) weeks, on how many occasions have you drawn "
            "on quantitative or qualitative data (for example databases, "
            "administrative records, statistical reports, surveys, dashboards) "
            "as an input to decisions related to your work?\n\n"
            "_Reply with an exact number (e.g. 0, 3, 12)._"
        ),
        "P2": (
            "In the last four (4) weeks, in how many projects, tasks or formal "
            "processes you took part in was data or empirical evidence used as "
            "explicit support for decision making (for example adjusting "
            "strategy, allocating resources, redesigning a process)?\n\n"
            "_Reply with an exact number (e.g. 0, 3, 12)._"
        ),
        "P3": (
            "Over the last year, how often did you, or the organisation you "
            "work for, carry out systematic data collection (for example "
            "running surveys, filling in forms, extracting information from "
            "internal systems, updating databases) that was then used as an "
            "input for decisions in your work?\n\n"
            '_Reply with a short phrase (e.g. "never", "sometimes", "almost '
            'always", or an approximate percentage)._'
        ),
        "P4": (
            "Over the last year, when you needed to collect information or "
            "data to support decisions in your work, what was the main mode of "
            "data collection you used?\n\n"
            "_For example: online surveys, paper forms, phone interviews, "
            "internal administrative records, analysis of existing databases._"
        ),
    },
    # ARM 2 - the recommended pattern. Same four constructs, as tappable lists,
    # one idea per message, with a progress cue so the respondent always knows
    # how much is left. Emoji are used sparingly and only in bodies.
    "arm2": {
        "button": "Choose an answer",
        "P1": {
            "body": (
                "📊 Question 1 of 4\n\n"
                "In the last 4 weeks, how many times have you used data to "
                "make decisions in your work?"
            ),
            "options": [
                ("p1_0", "0 times", "I did not use data in the last 4 weeks"),
                ("p1_1_2", "1-2 times", "Once or twice"),
                ("p1_3_5", "3-5 times", "A handful of times"),
                ("p1_6_10", "6-10 times", "Most weeks"),
                ("p1_gt10", "More than 10 times", "Almost daily"),
            ],
        },
        "P2": {
            "body": (
                "Question 2 of 4\n\n"
                "In the last 4 weeks, in how many projects was data used to "
                "make decisions?"
            ),
            "options": [
                ("p2_0", "0 projects", "No project used data"),
                ("p2_1", "1 project", "One project used data"),
                ("p2_2_3", "2-3 projects", "A few projects used data"),
                ("p2_4_5", "4-5 projects", "Several projects used data"),
                ("p2_gt5", "More than 5 projects", "Most or all projects used data"),
            ],
        },
        "P3": {
            "body": (
                "Question 3 of 4 - almost there\n\n"
                "Over the last year, how often did you or your organisation "
                "collect the data used to make decisions?"
            ),
            "options": [
                (
                    "p3_never",
                    "We never collect data",
                    "We only use data others collect",
                ),
                ("p3_lt25", "A few projects", "Less than 25% of projects"),
                ("p3_25_50", "About half", "Between 25% and 50% of projects"),
                ("p3_51_75", "Most projects", "Between 51% and 75% of projects"),
                ("p3_gt75", "Almost all projects", "More than 75% of projects"),
            ],
        },
        "P4": {
            "body": (
                "🙌 Last one - question 4 of 4\n\n"
                "Over the last year, when you needed to collect information, "
                "which mode did you mainly use?"
            ),
            "options": [
                ("p4_capi", "In person (CAPI)", "Face-to-face interviews"),
                ("p4_cati", "Phone (CATI)", "Interviews by phone call"),
                ("p4_web", "Web or online", "Self-completed online forms"),
                ("p4_whatsapp", "WhatsApp", "Surveys over WhatsApp like this one"),
                (
                    "p4_other",
                    "Another mode",
                    "Administrative records or something else",
                ),
            ],
        },
    },
    "error_numeric": (
        "Please reply with a number only.\n\n"
        "_Reply with an exact number (e.g. 0, 3, 12)._\n\n"
        "I am a bot and cannot understand everything that is written to me."
    ),
    # {button} is filled in from the same table entry the list picker uses, so
    # the nudge can never name a button that is not on screen.
    "error_option": (
        "No problem - I could not read that one.\n\n"
        "Tap *{button}* on the message above and pick from the list. You can "
        "also just reply with the number of your answer.\n\n"
        "I am a bot, so I only understand the options."
    ),
    "close_complete": (
        "🙏 Thank you for completing the survey.\n\n"
        "You took part in an experiment with two versions of the same survey: "
        "one asking openly in dense prose, and one asking the same things in "
        "plain language with tappable answers. You saw one of them.\n\n"
        "The second version is the one we recommend, and in the session we will "
        "show what your group's answers looked like - and how much the format "
        "alone changed them.\n\n"
        "See you there."
    ),
    "close_declined": (
        "Thank you for your reply. We understand and respect your decision not "
        "to take part in the exercise.\n\n"
        "You are still very welcome in the session, where we will look at how "
        "WhatsApp data collection works and where it goes wrong."
    ),
    "close_incomplete": (
        "Thank you for the answers you gave - they are recorded.\n\n"
        "We will look at how the format of a question changes the answers it "
        "gets, live in the session. See you there."
    ),
}

ES: dict[str, Any] = {
    "name": "Spanish",
    "flow_suffix": "es",
    "language": "es",
    "intro_template": "data_use_demo_intro_es",
    "description": "Data use demo (ARM1/ARM2 experiment) - Spanish",
    "consent": {
        "body": (
            "👋 Antes de empezar - ¿quieres participar?\n\n"
            "Toma unos 3 minutos. La participación es voluntaria, puedes "
            "dejar de responder en cualquier momento y tus respuestas son "
            "confidenciales."
        ),
        "button_yes": "Sí participo",
        "button_no": "No gracias",
        "typed_yes": "1|si|sí|s",
        "typed_no": "2|no|n",
    },
    "arm1": {
        "P1": (
            "Durante las últimas cuatro (4) semanas, ¿en cuántas "
            "ocasiones has recurrido al uso de información basada en datos "
            "cuantitativos o cualitativos (por ejemplo, bases de datos, "
            "registros administrativos, informes estadísticos, encuestas, "
            "paneles de control) como insumo para adoptar decisiones "
            "relacionadas con tu trabajo?\n\n"
            "_Responde con un número exacto (ej.: 0, 3, 12)._"
        ),
        "P2": (
            "Durante las últimas cuatro (4) semanas, ¿en cuántos "
            "proyectos, tareas o procesos formales de tu organización en los "
            "que participaste se utilizaron datos o evidencia empírica como "
            "apoyo explícito para la toma de decisiones (por ejemplo, ajuste "
            "de estrategias, asignación de recursos, rediseño de "
            "procesos)?\n\n"
            "_Responde con un número exacto (ej.: 0, 3, 12)._"
        ),
        "P3": (
            "Durante el último año, ¿con qué frecuencia tú, "
            "o la entidad en la que trabajas, llevaron a cabo actividades de "
            "recolección sistemática de datos (por ejemplo, "
            "aplicación de encuestas, diligenciamiento de formularios, "
            "extracción de información de sistemas internos, "
            "actualización de bases de datos) que luego fueron utilizados "
            "como insumo para la toma de decisiones en tu trabajo?\n\n"
            '_Escribe una frase breve (ej.: "nunca", "a veces", "casi '
            'siempre", o un porcentaje aproximado)._'
        ),
        "P4": (
            "Durante el último año, cuando fue necesario recolectar "
            "información o datos para apoyar la toma de decisiones en tu "
            "trabajo, ¿cuál fue la modalidad predominante de "
            "recolección de datos que utilizaste?\n\n"
            "_Por ejemplo: encuestas en línea, formularios en papel, "
            "entrevistas telefónicas, registros administrativos internos, "
            "análisis de bases de datos existentes._"
        ),
    },
    "arm2": {
        "button": "Elige tu respuesta",
        "P1": {
            "body": (
                "📊 Pregunta 1 de 4\n\n"
                "En las últimas 4 semanas, ¿cuántas veces has "
                "usado datos para tomar decisiones en tu trabajo?"
            ),
            "options": [
                ("p1_0", "0 veces", "No usé datos en las últimas 4 semanas"),
                ("p1_1_2", "1-2 veces", "Una o dos veces"),
                ("p1_3_5", "3-5 veces", "Unas pocas veces"),
                ("p1_6_10", "6-10 veces", "Casi todas las semanas"),
                ("p1_gt10", "Más de 10 veces", "Casi a diario"),
            ],
        },
        "P2": {
            "body": (
                "Pregunta 2 de 4\n\n"
                "En las últimas 4 semanas, ¿en cuántos proyectos "
                "se usaron datos para tomar decisiones?"
            ),
            "options": [
                ("p2_0", "0 proyectos", "Ningún proyecto usó datos"),
                ("p2_1", "1 proyecto", "Un proyecto usó datos"),
                ("p2_2_3", "2-3 proyectos", "Algunos proyectos usaron datos"),
                ("p2_4_5", "4-5 proyectos", "Varios proyectos usaron datos"),
                ("p2_gt5", "Más de 5 proyectos", "La mayoría o todos"),
            ],
        },
        "P3": {
            "body": (
                "Pregunta 3 de 4 - ya casi\n\n"
                "Durante el último año, ¿con qué frecuencia "
                "recolectaste tú o tu entidad los datos usados para tomar "
                "decisiones?"
            ),
            "options": [
                (
                    "p3_never",
                    "Nunca recolectamos",
                    "Solo usamos datos que recolectan otros",
                ),
                ("p3_lt25", "Pocos proyectos", "Menos del 25% de los proyectos"),
                ("p3_25_50", "Cerca de la mitad", "Entre el 25% y el 50%"),
                ("p3_51_75", "La mayoría", "Entre el 51% y el 75%"),
                ("p3_gt75", "Casi todos", "Más del 75% de los proyectos"),
            ],
        },
        "P4": {
            "body": (
                "🙌 La última - pregunta 4 de 4\n\n"
                "En el último año, cuando fue necesario recolectar "
                "información, ¿qué modalidad usaste "
                "principalmente?"
            ),
            "options": [
                ("p4_capi", "Presencial (CAPI)", "Entrevistas cara a cara"),
                ("p4_cati", "Telefónica (CATI)", "Entrevistas por llamada"),
                ("p4_web", "Web o en línea", "Formularios en línea"),
                ("p4_whatsapp", "Por WhatsApp", "Encuestas por WhatsApp como esta"),
                ("p4_other", "Otra modalidad", "Registros administrativos u otra"),
            ],
        },
    },
    "error_numeric": (
        "Por favor responde solo con un número.\n\n"
        "_Responde con un número exacto (ej.: 0, 3, 12)._\n\n"
        "Soy un robot y no entiendo todo lo que me escribes."
    ),
    "error_option": (
        "Sin problema - no pude leer esa respuesta.\n\n"
        "Toca *{button}* en el mensaje anterior y selecciona de la lista. "
        "También puedes responder con el número de tu "
        "respuesta.\n\n"
        "Soy un robot, así que solo entiendo las opciones."
    ),
    "close_complete": (
        "🙏 Gracias por completar la encuesta.\n\n"
        "Hiciste parte de un experimento con dos versiones de la misma "
        "encuesta: una que pregunta de forma abierta y compleja, y otra que "
        "pregunta lo mismo en lenguaje sencillo con respuestas que se pueden "
        "tocar. Tú viste una de las dos.\n\n"
        "La segunda es la que recomendamos, y en la sesión mostraremos "
        "cómo se vieron las respuestas de tu grupo - y cuánto "
        "cambió el formato por sí solo.\n\n"
        "Nos vemos allá."
    ),
    "close_declined": (
        "Gracias por tu respuesta. Entendemos y respetamos que hayas decidido "
        "no participar en el ejercicio.\n\n"
        "Aun así, estás muy bienvenid@ a la sesión, donde "
        "hablaremos de cómo funciona la recolección de datos por "
        "WhatsApp y de sus riesgos."
    ),
    "close_incomplete": (
        "Gracias por las respuestas que alcanzaste a dar - quedaron "
        "registradas.\n\n"
        "Veremos cómo el formato de una pregunta cambia las respuestas "
        "que recibe, en vivo durante la sesión. Nos vemos allá."
    ),
}

LANGS: dict[str, dict[str, Any]] = {"en": EN, "es": ES}


class BuildError(Exception):
    """Raised when a language table or a content SID is unusable."""


#: Codepoint ranges that make a string risky to compare byte-for-byte: emoji and
#: pictographs, plus the variation selectors and zero-width joiner that let two
#: visually identical strings differ. Accented Latin letters are deliberately not
#: here - Spanish needs them and they compare fine.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0xFE00, 0xFE0F),
    (0x200D, 0x200D),
    (0x2B00, 0x2BFF),
)


def _has_emoji(text: str) -> bool:
    """Report whether the string holds a character unsafe to match literally."""
    return any(
        any(low <= ord(char) <= high for low, high in _EMOJI_RANGES) for char in text
    )


# ---------------------------------------------------------------------------
# Validation of the language tables themselves.
# ---------------------------------------------------------------------------


def check_language(lang: str) -> list[str]:
    """Return every way a language table would produce a broken message.

    Args:
        lang: Key into :data:`LANGS`.

    Returns:
        Human-readable problems. Empty means the table is usable.

    These are the limits Twilio and Studio impose rather than house style, and
    each one fails in a way that is hard to spot by reading: an over-long item
    title is rejected at template-create time with a generic error, and a comma
    in a label is not rejected at all - it just quietly produces a condition
    that can never match, which is precisely the "answer stored, respondent
    stranded" defect this repo now refuses to deploy.

    """
    table = LANGS[lang]
    problems: list[str] = []

    consent = table["consent"]
    for field_name in ("button_yes", "button_no"):
        title = consent[field_name]
        if len(title) > 25:
            problems.append(
                f"{lang}: consent {field_name} is {len(title)} chars, "
                f"quick-reply titles cap at 25: {title!r}"
            )
        if _has_emoji(title):
            problems.append(
                f"{lang}: consent {field_name} contains an emoji; button "
                f"labels are matched literally, keep them plain: {title!r}"
            )

    for key in QUESTION_KEYS:
        question = table["arm2"][key]
        if len(question["body"]) > 1024:
            problems.append(f"{lang}: ARM2 {key} body exceeds 1024 chars")
        options = question["options"]
        if not 1 <= len(options) <= 10:
            problems.append(
                f"{lang}: ARM2 {key} has {len(options)} options, "
                "list-picker allows 1 to 10"
            )
        seen: set[str] = set()
        for option in options:
            option_id, item, description = option[0], option[1], option[2]
            if len(item) > 24:
                problems.append(
                    f"{lang}: ARM2 {key} item is {len(item)} chars, cap is 24: {item!r}"
                )
            if len(description) > 72:
                problems.append(
                    f"{lang}: ARM2 {key} description is {len(description)} "
                    f"chars, cap is 72: {description!r}"
                )
            if _has_emoji(item):
                problems.append(
                    f"{lang}: ARM2 {key} item contains an emoji; item labels "
                    f"are matched literally against the reply, keep them "
                    f"plain and put warmth in the body: {item!r}"
                )
            if item.casefold() in seen:
                problems.append(
                    f"{lang}: ARM2 {key} repeats the item label {item!r}, so "
                    "the stored code would be ambiguous"
                )
            seen.add(item.casefold())
            if len(option_id) > 200:
                problems.append(f"{lang}: ARM2 {key} id exceeds 200 chars")

    # Meta's limit on the button that opens a list. Twilio's documentation gives
    # no maximum for this field, so this is the platform limit rather than a
    # documented Twilio one - kept because being conservative here costs nothing.
    if len(table["arm2"]["button"]) > 20:
        problems.append(
            f"{lang}: list button {table['arm2']['button']!r} exceeds the "
            "20 characters WhatsApp shows on a list button"
        )

    problems.extend(_check_options_are_matchable(lang))
    problems.extend(_check_consent_is_matchable(lang))
    return problems


def _check_options_are_matchable(lang: str) -> list[str]:
    """Run every option through its own split condition and see where it lands.

    Reading a condition and believing it is how an unreachable option survives
    review. This executes it instead, using the same semantics Studio uses, and
    checks all three things that have to hold: every label routes to the store
    widget, every position typed as a digit routes there too, and something
    nobody would ever tap does *not*.

    That last one matters as much as the first two. A pattern loose enough to
    match anything accepts junk as a real answer, which is worse than rejecting
    a real one - the respondent is never asked again and the row looks complete.
    """
    problems = []
    for key in QUESTION_KEYS:
        options = LANGS[lang]["arm2"][key]["options"]
        pattern = answer_pattern(options)

        for index, option in enumerate(options, start=1):
            item = option[1]
            for reply in (item, str(index), f"{index}.", f" {item} ", item.upper()):
                if not evaluate_condition("regex", pattern, reply):
                    problems.append(
                        f"{lang}: ARM2 {key} would not accept {reply!r}, so the "
                        f"option {item!r} is unreachable"
                    )

        for junk in ("banana", "", "0", str(len(options) + 1), "yes please"):
            if evaluate_condition("regex", pattern, junk):
                problems.append(
                    f"{lang}: ARM2 {key} accepts {junk!r} as a valid answer, so "
                    "junk would be stored as a real response"
                )

        # The split and the code mapping must agree. If the split is the more
        # tolerant of the two, a respondent is recorded as having answered
        # while their answer codes as `other` - which reads in the data as a
        # broken option rather than as the tolerance working.
        for index, option in enumerate(options, start=1):
            item = option[1]
            wanted = option_code(option, index)
            for reply in (item, str(index), f"{index}.", f"({index})", item.upper()):
                accepted = evaluate_condition("regex", pattern, reply)
                code = expected_code(options, reply)
                if accepted and code == "other":
                    problems.append(
                        f"{lang}: ARM2 {key} accepts {reply!r} but codes it as "
                        "'other'; the split is more tolerant than the mapping"
                    )
                if accepted and code not in ("other", wanted):
                    problems.append(
                        f"{lang}: ARM2 {key} codes {reply!r} as {code}, "
                        f"expected {wanted}"
                    )
    return problems


def _check_consent_is_matchable(lang: str) -> list[str]:
    """Check consent routes yes to yes, no to no, and nothing to both."""
    consent = LANGS[lang]["consent"]
    yes = word_pattern([consent["button_yes"]] + consent["typed_yes"].split("|"))
    no = word_pattern([consent["button_no"]] + consent["typed_no"].split("|"))

    problems = []
    for label, pattern, replies in (
        ("yes", yes, [consent["button_yes"], *consent["typed_yes"].split("|")]),
        ("no", no, [consent["button_no"], *consent["typed_no"].split("|")]),
    ):
        for reply in replies:
            if not evaluate_condition("regex", pattern, reply):
                problems.append(f"{lang}: consent {label} would not accept {reply!r}")

    # Consent is the one place an ambiguous match is unacceptable: a reply that
    # satisfies both branches would enrol someone by transition order.
    for reply in [
        consent["button_yes"],
        consent["button_no"],
        *consent["typed_yes"].split("|"),
        *consent["typed_no"].split("|"),
    ]:
        if evaluate_condition("regex", yes, reply) and evaluate_condition(
            "regex", no, reply
        ):
            problems.append(
                f"{lang}: consent reply {reply!r} matches both yes and no, so "
                "participation would be decided by transition order"
            )
    return problems


# ---------------------------------------------------------------------------
# Content template definitions.
#
# These are emitted rather than hand-written so the option text has exactly one
# home. None of them is ever submitted to Meta: quick-reply does not need
# approval in session and list-picker cannot be approved at all.
# ---------------------------------------------------------------------------


def consent_template_name(lang: str) -> str:
    """Friendly name of the consent quick-reply template for a language."""
    return f"data_use_demo_consent_{lang}"


def question_template_name(lang: str, key: str) -> str:
    """Friendly name of an ARM 2 list-picker template for a language."""
    return f"data_use_demo_arm2_{key.lower()}_{lang}"


def _generated_note(name: str, purpose: str) -> list[str]:
    return [
        "GENERATED by scripts/build_data_use_demo.py - do not hand-edit.",
        "Edit the language table in that script and re-run `just build-demo-flow`;",
        "the same strings drive this template, the flow's split conditions and the",
        "code stored for each answer, so editing here alone would desynchronise them.",
        "",
        purpose,
        "",
        "Create it with `just template-create` and never submit it to Meta.",
        f"Referenced by the flow as {name}.",
    ]


def consent_definition(lang: str) -> dict[str, Any]:
    """Build the consent quick-reply content template definition."""
    table = LANGS[lang]
    consent = table["consent"]
    name = consent_template_name(lang)
    return {
        "_comment": _generated_note(
            name,
            "Consent, as two quick-reply buttons. Sent inside the 24-hour window "
            "the opener's reply opens, so it needs no Meta approval. The buttons "
            "carry payload ids, but the flow splits on the message body so a "
            "typed reply works too.",
        ),
        "friendly_name": name,
        "language": table["language"],
        "types": {
            # A text fallback for any channel that cannot render buttons. The
            # numbers only appear here, in the fallback - the WhatsApp path has
            # no digits to type.
            "twilio/text": {
                "body": (
                    f"{consent['body']}\n\n"
                    f"1 - {consent['button_yes']}\n2 - {consent['button_no']}"
                )
            },
            "twilio/quick-reply": {
                "body": consent["body"],
                "actions": [
                    {"title": consent["button_yes"], "id": "consent_yes"},
                    {"title": consent["button_no"], "id": "consent_no"},
                ],
            },
        },
    }


def question_definition(lang: str, key: str) -> dict[str, Any]:
    """Build an ARM 2 list-picker content template definition."""
    table = LANGS[lang]
    question = table["arm2"][key]
    name = question_template_name(lang, key)
    numbered = "\n".join(
        f"{index} - {option[1]}"
        for index, option in enumerate(question["options"], start=1)
    )
    return {
        "_comment": _generated_note(
            name,
            f"ARM 2 {key}, as a tappable list of {len(question['options'])} "
            "options. list-picker cannot be submitted for approval and cannot "
            "open a session; it only works inside the 24-hour window, which is "
            "why every ARM 2 question sits after the opener and consent.",
        ),
        "friendly_name": name,
        "language": table["language"],
        "types": {
            "twilio/text": {"body": f"{question['body']}\n\n{numbered}"},
            "twilio/list-picker": {
                "body": question["body"],
                "button": table["arm2"]["button"],
                "items": [
                    {"id": option[0], "item": option[1], "description": option[2]}
                    for option in question["options"]
                ],
            },
        },
    }


def template_definitions(lang: str) -> dict[str, dict[str, Any]]:
    """Every content template this language's flow needs, by friendly name."""
    definitions = {consent_template_name(lang): consent_definition(lang)}
    for key in QUESTION_KEYS:
        definitions[question_template_name(lang, key)] = question_definition(lang, key)
    return definitions


# ---------------------------------------------------------------------------
# Widget builders.
# ---------------------------------------------------------------------------


def send(name, body, next_state, *, x=0, y=0):
    """Build a one-way message widget."""
    return {
        "name": name,
        "type": "send-message",
        "properties": {"offset": {"x": x, "y": y}, "from": FROM, "body": body},
        "transitions": [
            {"event": "sent", "next": next_state},
            {"event": "failed", "next": next_state},
        ],
    }


def ask(name, body, on_reply, *, x=0, y=0, on_timeout="mark_no_reply"):
    """Build a free-text question. Wires reply, timeout and deliveryFailure."""
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": {
            "offset": {"x": x, "y": y},
            "from": FROM,
            "body": body,
            "timeout": TIMEOUT,
        },
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": on_timeout},
            {"event": "deliveryFailure", "next": "mark_delivery_failed"},
        ],
    }


def ask_content(name, content_sid, on_reply, *, x=0, y=0, variables=None):
    """Build a question that sends a content template instead of a body.

    Used for the interactive messages - the consent buttons and the ARM 2
    lists. Studio needs `message_type` set as well as the SID, otherwise it
    treats the widget as a plain body and sends nothing.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
        "timeout": TIMEOUT,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-and-wait-for-reply",
        "properties": properties,
        "transitions": [
            {"event": "incomingMessage", "next": on_reply},
            {"event": "timeout", "next": "mark_no_reply"},
            {"event": "deliveryFailure", "next": "mark_delivery_failed"},
        ],
    }


def set_vars(name, pairs, next_state, *, x=0, y=0):
    """Set flow variables."""
    return {
        "name": name,
        "type": "set-variables",
        "properties": {
            "offset": {"x": x, "y": y},
            "variables": [{"key": k, "value": v} for k, v in pairs],
        },
        "transitions": [{"event": "next", "next": next_state}],
    }


def split(name, tested, branches, default, *, x=0, y=0, condition="equal_to"):
    """Branch on a value. `default` is the mandatory noMatch destination.

    `branches` is a list of (value, destination) or (value, destination, label).
    For `matches_any_of`, value is a comma-delimited string of alternatives -
    see the comma warning in the language-table comment. The label is what the
    Studio console shows on the transition, so it is worth setting whenever the
    raw value is a long list: someone will read this canvas mid-session.
    """
    transitions = [{"event": "noMatch", "next": default}]
    for branch in branches:
        value, destination = branch[0], branch[1]
        label = branch[2] if len(branch) > 2 else f"is {value}"
        transitions.append(
            {
                "event": "match",
                "next": destination,
                "conditions": [
                    {
                        "friendly_name": label[:60],
                        "arguments": [tested],
                        "type": condition,
                        "value": value,
                    }
                ],
            }
        )
    return {
        "name": name,
        "type": "split-based-on",
        "properties": {"offset": {"x": x, "y": y}, "input": tested},
        "transitions": transitions,
    }


def arm_x(arm):
    """Lay ARM 1 and ARM 2 out side by side in the Studio canvas."""
    return -700 if arm == "ARM1" else 500


#: Characters that mean something to every regex flavour and so must be escaped
#: in a literal. Deliberately not Python's `re.escape`, which also escapes a
#: space as "\ " - valid in Python, an error in a JavaScript unicode-mode regex,
#: and we do not control which engine Studio runs.
_REGEX_SPECIAL = set("\\^$.|?*+()[]{}")


def escape_literal(text: str) -> str:
    """Escape a string so a regex matches it literally."""
    return "".join("\\" + char if char in _REGEX_SPECIAL else char for char in text)


def answer_pattern(options) -> str:
    """Build the regex that recognises any valid answer to a list question.

    Args:
        options: ``(id, item, description)`` triples in display order.

    Returns:
        A Studio `regex` condition value.

    Accepts the tapped item label *or* its position typed as a digit, because a
    respondent who ignores the menu and writes "3" has still answered. Storing
    only one of the two forms is how a flow ends up with an answer column that
    is half labels and half numbers.

    Regex rather than `matches_any_of` for one structural reason: that predicate
    takes its alternatives as a single comma-delimited string, so a comma inside
    an option label silently becomes two alternatives that never match. Here a
    comma is just a character. It also lets the digit form tolerate the
    punctuation people actually type - "1.", "1)", "(1)".

    Studio anchors the pattern to the whole string, so the alternation must be
    wrapped: an unwrapped `a|b` can bind as `(^a)|(b$)` and match "xxb".

    """
    alternatives: list[str] = []
    for index, option in enumerate(options, start=1):
        alternatives.append(escape_literal(option[1]))
        alternatives.append(rf"\(?{index}[.)]?")
    return r"(?:\s*(?:" + "|".join(alternatives) + r")\s*)"


def word_pattern(words) -> str:
    """Build a regex matching any of these literal words, tolerating padding."""
    return (
        r"(?:\s*(?:"
        + "|".join(escape_literal(word) for word in words if word)
        + r")\s*)"
    )


#: Punctuation people add around a typed option number: "1.", "1)", "(1)".
#: Stripped before coding so the split and the code agree about what counts.
_STRIPPED_PUNCTUATION = (".", ")", "(")


def normalise_reply(reply: str) -> str:
    """Reduce a reply to the form the code mapping compares against.

    This is the Python twin of the Liquid filter chain in :func:`code_mapping`.
    Both exist so the two can be checked against each other: the split decides
    whether a reply counts as an answer, the mapping decides which answer it
    was, and if they disagree a respondent gets credited with answering while
    their answer codes as `other`.
    """
    text = reply.strip().casefold()
    for char in _STRIPPED_PUNCTUATION:
        text = text.replace(char, "")
    return text.strip()


def option_code(option, index: int) -> str:
    """Return the value stored for an option: its own code, or its position.

    An option may carry an explicit code as a fourth element. That exists for
    the answers that are *offered on a scale but are not points on it* - "Prefer
    not to say", "Don't know". Left to their position they would code as a 6 on
    a 5-point item and be silently averaged in, which is the kind of error that
    survives all the way into a published mean.
    """
    return str(option[3]) if len(option) > 3 else str(index)


def expected_code(options, reply: str) -> str:
    """Return the code the flow will store for this reply, or "other"."""
    normalised = normalise_reply(reply)
    for index, option in enumerate(options, start=1):
        if normalised in (normalise_reply(option[1]), str(index)):
            return option_code(option, index)
    return "other"


def code_mapping(widget: str, options) -> str:
    """Build Liquid that normalises a reply to its option number.

    The stored answer must not depend on whether the respondent tapped or
    typed. A tap puts the item's label in the message body; a typed reply puts
    a digit there. This collapses both to the option's position, and leaves
    anything else as `other` rather than silently coding it as a real answer.

    The filter chain mirrors :func:`normalise_reply` exactly, because the split
    condition is deliberately tolerant of casing and trailing punctuation - and
    a mapping that were stricter would quietly code those replies as `other`
    while the split had already recorded them as answered.
    """
    removals = "".join(f' | replace: "{char}", ""' for char in _STRIPPED_PUNCTUATION)
    clauses = []
    for index, option in enumerate(options, start=1):
        clauses.append(
            f'{{% when "{normalise_reply(option[1])}" or "{index}" %}}'
            f"{option_code(option, index)}"
        )
    return (
        f"{{% assign reply = widgets.{widget}.inbound.Body "
        f"| strip | downcase{removals} | strip %}}"
        f"{{% case reply %}}{''.join(clauses)}"
        "{% else %}other{% endcase %}"
    )


def open_question(arm, key, body, *, y, next_state):
    """ARM 1: ask, accept whatever arrives, move on.

    No validation, deliberately. Validating an open answer would turn ARM 1
    into ARM 2 and destroy the comparison the demo exists to make.
    """
    name = f"{arm}_{key}"
    return [
        ask(name, body, f"store_{name}", x=arm_x(arm), y=y),
        set_vars(
            f"store_{name}",
            [(f"{name}_status", "answered")],
            next_state,
            x=arm_x(arm),
            y=y + 80,
        ),
    ]


def list_question(arm, key, content_sid, options, error_body, *, y, next_state):
    """ARM 2: send a list, accept a tap or a typed number, retry twice, move on.

    Seven widgets, mirroring the account's house pattern: ask, validate, store,
    count the retry, decide, nudge, give up. The retry limit matters ethically
    as much as technically - re-asking someone who cannot answer is badgering a
    volunteer.
    """
    name = f"{arm}_{key}"
    validate = f"split_{name}"
    retry = f"retry_{name}"
    give_up = f"giveup_{name}"
    error_widget = f"error_{name}"
    x = arm_x(arm)

    return [
        ask_content(name, content_sid, validate, x=x, y=y),
        split(
            validate,
            f"{{{{widgets.{name}.inbound.Body}}}}",
            [
                (
                    answer_pattern(options),
                    f"store_{name}",
                    f"tapped or typed one of {len(options)} options",
                )
            ],
            retry,
            x=x,
            y=y + 80,
            condition="regex",
        ),
        set_vars(
            f"store_{name}",
            [
                (f"{name}_status", "answered"),
                (f"{name}_code", code_mapping(name, options)),
            ],
            next_state,
            x=x,
            y=y + 160,
        ),
        set_vars(
            retry,
            [
                (
                    f"tries_{name}",
                    f"{{% assign current = flow.variables.tries_{name} "
                    "| default: 0 %}{{ current | plus: 1 }}",
                )
            ],
            f"check_{name}",
            x=x + 260,
            y=y + 80,
        ),
        split(
            f"check_{name}",
            f"{{{{flow.variables.tries_{name}}}}}",
            [("1", error_widget), ("2", error_widget)],
            give_up,
            x=x + 260,
            y=y + 160,
        ),
        send(error_widget, error_body, name, x=x + 520, y=y + 160),
        set_vars(
            give_up,
            [(f"{name}_status", "multierror"), (f"{name}_code", "")],
            next_state,
            x=x + 260,
            y=y + 240,
        ),
    ]


# ---------------------------------------------------------------------------
# Flow assembly.
# ---------------------------------------------------------------------------


def build(lang: str, content_sids: dict[str, str]) -> dict[str, Any]:
    """Assemble one language's flow definition.

    Args:
        lang: Key into :data:`LANGS`.
        content_sids: Friendly name to HX SID, for every template the flow
            references. Taking these as an argument rather than reading them
            from Twilio keeps this function pure and testable offline.

    Returns:
        The flow definition, ready for `rtt flow deploy`.

    Raises:
        BuildError: If the language table is invalid or a SID is missing.

    """
    if lang not in LANGS:
        raise BuildError(f"unknown language {lang!r}; expected one of {sorted(LANGS)}")

    problems = check_language(lang)
    if problems:
        raise BuildError(
            f"{lang} language table is not usable:\n"
            + "\n".join(f"  {p}" for p in problems)
        )

    table = LANGS[lang]
    needed = [table["intro_template"], consent_template_name(lang)]
    needed += [question_template_name(lang, key) for key in QUESTION_KEYS]
    missing = [name for name in needed if not content_sids.get(name)]
    if missing:
        raise BuildError("missing content SIDs for: " + ", ".join(missing))

    states: list[dict[str, Any]] = [
        {
            "name": "Trigger",
            "type": "trigger",
            "properties": {"offset": {"x": 0, "y": -1100}},
            "transitions": [
                # `rtt launch` creates executions over the REST API, which fires
                # incomingRequest - NOT incomingMessage. Leaving it unrouted
                # ends the execution at the trigger without sending anything,
                # and the launcher still reports it as "active".
                {"event": "incomingRequest", "next": "intro"},
                # Someone messaging the number cold gets the same opening.
                {"event": "incomingMessage", "next": "intro"},
                {"event": "incomingCall"},
                {"event": "incomingConversationMessage"},
                {"event": "incomingParent"},
            ],
        },
        # The opening must be an approved template and must WAIT for a reply.
        # It is the only business-initiated message in the flow, so it is the
        # only one Meta reviews. Waiting means the respondent's reply opens the
        # 24-hour window, after which the buttons and lists below are free.
        ask_content(
            "intro",
            content_sids[table["intro_template"]],
            "consent",
            x=0,
            y=-950,
            variables=[{"key": "1", "value": "{{flow.data.name}}"}],
        ),
        ask_content(
            "consent",
            content_sids[consent_template_name(lang)],
            "split_consent",
            x=0,
            y=-820,
        ),
        # Tap or type, in either language's words. Declining and failing to
        # parse both lead to the same place: nobody is enrolled by ambiguity.
        split(
            "split_consent",
            "{{widgets.consent.inbound.Body}}",
            [
                (
                    word_pattern(
                        [table["consent"]["button_yes"]]
                        + table["consent"]["typed_yes"].split("|")
                    ),
                    "record_consent",
                    "consented",
                ),
                (
                    word_pattern(
                        [table["consent"]["button_no"]]
                        + table["consent"]["typed_no"].split("|")
                    ),
                    "record_declined",
                    "declined",
                ),
            ],
            "record_declined",
            x=0,
            y=-700,
            condition="regex",
        ),
        set_vars("record_consent", [("set_consent", "yes")], "split_arm", x=0, y=-580),
        set_vars(
            "record_declined",
            [("set_consent", "no"), ("outcome", "declined")],
            "finish",
            x=400,
            y=-580,
        ),
        # The arm is preloaded, not drawn here: randomisation happens offline in
        # the sample file, so it is reproducible and balance can be checked
        # before anyone is contacted.
        split(
            "split_arm",
            "{{flow.data.arm}}",
            [("1", "ARM1_P1"), ("2", "ARM2_P1")],
            "ARM1_P1",
            x=0,
            y=-460,
        ),
    ]

    for index, key in enumerate(QUESTION_KEYS):
        following = (
            f"ARM1_{QUESTION_KEYS[index + 1]}"
            if index + 1 < len(QUESTION_KEYS)
            else "mark_complete"
        )
        states.extend(
            open_question(
                "ARM1",
                key,
                table["arm1"][key],
                y=-300 + index * 340,
                next_state=following,
            )
        )

    for index, key in enumerate(QUESTION_KEYS):
        following = (
            f"ARM2_{QUESTION_KEYS[index + 1]}"
            if index + 1 < len(QUESTION_KEYS)
            else "mark_complete"
        )
        states.extend(
            list_question(
                "ARM2",
                key,
                content_sids[question_template_name(lang, key)],
                table["arm2"][key]["options"],
                # Named from the same entry the list picker uses, so the nudge
                # cannot end up telling someone to tap a button that is not
                # there.
                table["error_option"].format(button=table["arm2"]["button"]),
                y=-300 + index * 340,
                next_state=following,
            )
        )

    states.extend(
        [
            set_vars(
                "mark_complete",
                [("set_complete", "1"), ("outcome", "complete")],
                "finish",
                x=0,
                y=1200,
            ),
            set_vars(
                "mark_no_reply",
                [("set_no_reply", "1"), ("outcome", "incomplete")],
                "finish",
                x=900,
                y=1200,
            ),
            set_vars(
                "mark_delivery_failed",
                [("set_fail", "1"), ("outcome", "incomplete")],
                "finish",
                x=1300,
                y=1200,
            ),
            # Single convergence point. Every terminal path arrives here, so a
            # row is published whatever happened - complete, declined, timed
            # out or undeliverable.
            set_vars(
                "finish",
                [("set_time_fin", "{{flow.variables.now}}")],
                "function_encrypt",
                x=0,
                y=1320,
            ),
            {
                "name": "function_encrypt",
                "type": "run-function",
                "properties": {
                    "offset": {"x": 0, "y": 1440},
                    "service_sid": ENCRYPT_SERVICE_SID,
                    "environment_sid": ENCRYPT_ENVIRONMENT_SID,
                    "function_sid": ENCRYPT_FUNCTION_SID,
                    "url": ENCRYPT_URL,
                    "parameters": [
                        {"key": "enc_name", "value": "{{flow.data.name}}"},
                        {
                            "key": "enc_p_number_original",
                            "value": "{{contact.channel.address}}",
                        },
                    ],
                },
                "transitions": [
                    {"event": "success", "next": "publish_motherduck"},
                    {"event": "fail", "next": "publish_motherduck"},
                ],
            },
        ]
    )

    published: list[tuple[str, str]] = [
        ("caseid", "{{flow.data.caseid}}"),
        ("lang", lang),
        ("arm", "{{flow.data.arm}}"),
        ("enc_name", "{{widgets.function_encrypt.parsed.enc_name}}"),
        (
            "enc_p_number_original",
            "{{widgets.function_encrypt.parsed.enc_p_number_original}}",
        ),
        ("set_consent", "{{flow.variables.set_consent}}"),
        ("set_complete", "{{flow.variables.set_complete}}"),
        ("set_no_reply", "{{flow.variables.set_no_reply}}"),
        ("set_fail", "{{flow.variables.set_fail}}"),
        ("outcome", "{{flow.variables.outcome}}"),
    ]
    for arm in ("ARM1", "ARM2"):
        for key in QUESTION_KEYS:
            name = f"{arm}_{key}"
            # Raw reply, then its status, then - for the list arm only - the
            # normalised option number. Order matters: the status column has to
            # sit beside its answer for the unpaired-answers check to read the
            # pairing, and for an analyst to read it in the warehouse.
            published.append((name, f"{{{{widgets.{name}.inbound.Body}}}}"))
            published.append(
                (f"{name}_status", f"{{{{flow.variables.{name}_status}}}}")
            )
            if arm == "ARM2":
                published.append(
                    (f"{name}_code", f"{{{{flow.variables.{name}_code}}}}")
                )

    states.append(
        {
            "name": "publish_motherduck",
            "type": "run-function",
            "properties": {
                "offset": {"x": 0, "y": 1560},
                "service_sid": PUBLISH_SERVICE_SID,
                "environment_sid": PUBLISH_ENVIRONMENT_SID,
                "function_sid": PUBLISH_FUNCTION_SID,
                "url": PUBLISH_URL,
                "parameters": [{"key": k, "value": v} for k, v in published],
            },
            "transitions": [
                {"event": "success", "next": "split_closing"},
                {"event": "fail", "next": "split_closing"},
            ],
        }
    )

    states.extend(
        [
            split(
                "split_closing",
                "{{flow.variables.outcome}}",
                [
                    ("complete", "close_complete"),
                    ("declined", "close_declined"),
                    ("incomplete", "close_incomplete"),
                ],
                "close_incomplete",
                x=0,
                y=1680,
            ),
            send(
                "close_complete",
                table["close_complete"],
                "close_complete",
                x=-400,
                y=1800,
            ),
            send(
                "close_declined", table["close_declined"], "close_declined", x=0, y=1800
            ),
            send(
                "close_incomplete",
                table["close_incomplete"],
                "close_incomplete",
                x=400,
                y=1800,
            ),
        ]
    )

    # The closing messages are terminal: drop their outgoing transitions so the
    # flow ends rather than looping back into itself.
    for state in states:
        if state["name"].startswith("close_"):
            state["transitions"] = [{"event": "sent"}, {"event": "failed"}]

    return {
        "description": table["description"],
        "states": states,
        "initial_state": "Trigger",
        "flags": {"allow_concurrent_calls": True},
    }


def flow_path(lang: str) -> Path:
    """Where a language's flow definition is written."""
    return FLOW_DIR / f"data_use_demo_{LANGS[lang]['flow_suffix']}.json"


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def write_template_definitions(lang: str) -> list[Path]:
    """Write the generated content template definitions for a language."""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, definition in template_definitions(lang).items():
        path = TEMPLATE_DIR / f"{name}.json"
        path.write_text(
            json.dumps(definition, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def resolve_sids(lang: str) -> tuple[dict[str, str], list[str]]:
    """Look up the flow's content templates on the account by friendly name.

    Returns:
        The SIDs found, and the friendly names that do not exist yet.

    Resolving by name rather than pasting HX SIDs into this file means a
    recreated template is picked up on the next build instead of leaving a flow
    pointing at something that no longer exists.

    """
    cfg.load_env()
    client = cfg.twilio_client()

    table = LANGS[lang]
    wanted = [table["intro_template"], consent_template_name(lang)]
    wanted += [question_template_name(lang, key) for key in QUESTION_KEYS]

    found: dict[str, str] = {}
    missing: list[str] = []
    for name in wanted:
        existing = tpl.find_by_name(client, name)
        if existing is None:
            missing.append(name)
        else:
            found[name] = existing.sid
    return found, missing


def build_one(lang: str) -> bool:
    """Emit one language's templates and flow. Returns True on success."""
    print(f"\n=== {LANGS[lang]['name']} ({lang}) ===")

    problems = check_language(lang)
    if problems:
        print("  language table is not usable:")
        for problem in problems:
            print(f"    {problem}")
        return False

    for path in write_template_definitions(lang):
        print(f"  template  {path.relative_to(REPO_ROOT).as_posix()}")

    found, missing = resolve_sids(lang)
    if missing:
        print("\n  Cannot build the flow yet - these content templates do not")
        print("  exist on this account. Create them, then re-run this build:")
        for name in missing:
            if name == LANGS[lang]["intro_template"]:
                where = f"templates/{name}.json"
                note = "  # opener: needs Meta approval before a real round"
            else:
                where = f"templates/generated/{name}.json"
                note = "  # in-session only: never submit this one"
            print(f"    just template-create {where}{note}")
        return False

    definition = build(lang, found)
    path = flow_path(lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    questions = sum(
        1 for s in definition["states"] if s["type"] == "send-and-wait-for-reply"
    )
    print(f"  flow      {path.relative_to(REPO_ROOT).as_posix()}")
    print(f"            {len(definition['states'])} widgets, {questions} questions")

    findings = check_flow(definition)
    errors = [f for f in findings if f.severity == "error"]
    if not findings:
        print("  check     all checks passed")
    for finding in findings:
        print(f"  check     [{finding.severity}] {finding.code}  {finding.summary}")
        for line in finding.detail[:6]:
            print(f"                {line}")
    return not errors


def main() -> None:
    """Build one or both languages."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lang",
        choices=[*sorted(LANGS), "both"],
        default="both",
        help="Language to build. Defaults to both, which is the point.",
    )
    args = parser.parse_args()

    languages = sorted(LANGS) if args.lang == "both" else [args.lang]
    ok = [build_one(lang) for lang in languages]

    if not all(ok):
        print("\nNot every language built cleanly - see above.")
        raise SystemExit(1)

    print("\nBoth languages built from one structure, so a fix to the graph")
    print("lands in both. Deploy with:")
    for lang in languages:
        rel = flow_path(lang).relative_to(REPO_ROOT).as_posix()
        print(f"  just flow-deploy {rel}")


if __name__ == "__main__":
    main()
