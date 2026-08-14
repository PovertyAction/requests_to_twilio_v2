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
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from twilio.rest import Client  # noqa: E402

from requests_to_twilio import config as cfg  # noqa: E402
from requests_to_twilio import templates as tpl  # noqa: E402
from requests_to_twilio.flows import check_flow, evaluate_condition  # noqa: E402

FLOW_DIR = REPO_ROOT / "flows"
TEMPLATE_DIR = REPO_ROOT / "templates" / "generated"
CODEBOOK_DIR = REPO_ROOT / "codebook"

FROM = "{{flow.channel.address}}"
TIMEOUT = "3600"  # 1 hour, as in the source flow

#: Deployed Functions service. Both languages share it: the encryption and
#: publish code is language-independent, and duplicating it would reintroduce
#: exactly the drift this builder exists to prevent.
#: The Functions service `just deploy-functions` creates. Everything about the
#: deployment is looked up from this one name at build time rather than pasted
#: in as SIDs: a `ZS`/`ZE`/`ZH` triple and a `*.twil.io` host are per-account,
#: and the domain carries a random suffix that cannot even be guessed. Hard-
#: coding them meant `just build-demo-flow` emitted a flow pointing at the
#: author's own subaccount, which 404s for everybody else - the single thing
#: that stopped another team running this.
FUNCTIONS_SERVICE_NAME = "rtt-survey"
ENCRYPT_FUNCTION_NAME = "encrypt_fields"
PUBLISH_FUNCTION_NAME = "publish_motherduck"

#: The paths those functions are deployed at, from `deploy_twilio_functions.py`.
ENCRYPT_PATH = "/encrypt-fields"
PUBLISH_PATH = "/publish-motherduck"

QUESTION_KEYS = ("P1", "P2", "P3", "P4")

#: Studio has no `now` variable - `{{flow.variables.now}}` renders empty, which
#: is how set_time_fin came to be blank on every row of the first live test. The
#: Liquid date filter does accept the literal string 'now', which is the
#: documented way to stamp a time.
#:
#: The flow no longer stamps times at all, and this is why.
#:
#: Studio renders `now` in Twilio's own timezone, not UTC - measured live, a
#: stamp of 09:12:05 sat beside a submitted_at of 16:12:06 UTC in the same row.
#: Liquid cannot convert it: the date filter has no timezone directive, and the
#: %s (epoch) escape is not supported either. Asking for %s does not error, it
#: writes the literal string "%s" into the column, which a live round proved.
#:
#: There is no way to produce a UTC timestamp from inside a Studio widget, so
#: the flow stops pretending. Both ends come from sources that are UTC by
#: construction:
#:
#:   start     execution.date_created, from the Studio API via `rtt fetch`
#:   end       submitted_at, stamped server-side by the publish Function
#:
#: `execution_sid` is published so the two join exactly. That column earns its
#: place regardless - without it a warehouse row cannot be traced back to the
#: execution log that produced it.


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
    # The two ends of the bookend, and the only two templates in this flow that
    # Meta ever sees. Everything between them is in session and free.
    "intro_template": "rst2026_intro",
    "close_template": "rst2026_close",
    "description": "Data use demo (ARM1/ARM2 experiment) - English, RST Jaipur 2026",
    "consent": {
        "body": (
            "👋 Before we start - would you like to take part?\n\n"
            "It takes about 3 minutes. Taking part is voluntary, you can stop "
            "at any time by not replying, and your answers are confidential."
        ),
        # Commas are fine in a label now that the splits use regex; under
        # matches_any_of these had to read "Yes I will take part".
        "button_yes": "Yes, I'll take part",
        "button_no": "No, thanks",
        "typed_yes": "1|yes|y",
        "typed_no": "2|no|n",
    },
    # Recognised at every question, in either arm. Twilio's own opt-out handling
    # covers the carrier keywords for SMS; inside a WhatsApp session a "STOP"
    # arrives as an ordinary reply, and without this it is stored as the answer
    # to whatever was asked - then the next question is sent anyway.
    "stop_words": ["stop", "quit", "unsubscribe", "cancel", "end"],
    "stop_ack": (
        "Understood - I have stopped here and will not send anything else "
        "about this survey.\n\n"
        "The answers you already gave are kept; nothing further is asked. "
        "Thank you for your time."
    ),
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
    # For questions whose options are themselves numbers. Inviting "reply with
    # the number" there asks for exactly the reply the split has to refuse: on
    # a 0/1/2-3 projects scale a "1" could be the position or the label, and
    # they are different options.
    "error_option_labels": (
        "No problem - I could not read that one.\n\n"
        "Tap *{button}* on the message above and pick from the list, or type "
        "the option exactly as it appears.\n\n"
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
    # Sent to anyone who writes to the number without being launched into the
    # survey - which in practice is mostly people saying thank you after
    # finishing it.
    "unsolicited": (
        "👋 Thanks for your message.\n\n"
        "This number only runs a short survey during the session, so there is "
        "nothing further needed from you here.\n\n"
        "If you have a question, the IPA team at the training can help."
    ),
}

ES: dict[str, Any] = {
    "name": "Spanish",
    "flow_suffix": "es",
    "language": "es",
    "intro_template": "data_use_demo_intro_es",
    "close_template": "data_use_demo_close_es",
    "description": "Data use demo (ARM1/ARM2 experiment) - Spanish",
    "consent": {
        "body": (
            "👋 Antes de empezar - ¿quieres participar?\n\n"
            "Toma unos 3 minutos. La participación es voluntaria, puedes "
            "dejar de responder en cualquier momento y tus respuestas son "
            "confidenciales."
        ),
        "button_yes": "Sí, participo",
        "button_no": "No, gracias",
        "typed_yes": "1|si|sí|s",
        "typed_no": "2|no|n",
    },
    # Los términos en inglés también se reconocen: mucha gente escribe "stop"
    # sin importar el idioma de la encuesta.
    "stop_words": [
        "stop",
        "parar",
        "cancelar",
        "salir",
        "baja",
        "no molestar",
        "quit",
    ],
    "stop_ack": (
        "Entendido - me detengo aquí y no te enviaré nada más sobre esta "
        "encuesta.\n\n"
        "Las respuestas que ya diste se conservan; no se pregunta nada más. "
        "Gracias por tu tiempo."
    ),
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
    # Para preguntas cuyas opciones ya son números. Invitar "responde con el
    # número" ahí pide justo la respuesta que el split tiene que rechazar: en
    # una escala de 0/1/2-3 proyectos, un "1" puede ser la posición o la
    # etiqueta, y son opciones distintas.
    "error_option_labels": (
        "Sin problema - no pude leer esa respuesta.\n\n"
        "Toca *{button}* en el mensaje anterior y selecciona de la lista, o "
        "escribe la opción tal como aparece.\n\n"
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
    "unsolicited": (
        "👋 Gracias por tu mensaje.\n\n"
        "Este número solo se usa para una encuesta corta durante la "
        "sesión, así que no necesitas hacer nada más por aquí.\n\n"
        "Si tienes una pregunta, el equipo de IPA en la sesión puede "
        "ayudarte."
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
    checks four things that have to hold: every label routes to the store
    widget, every position typed as a digit routes there too *where a digit is
    unambiguous*, a digit that is ambiguous is refused rather than guessed at,
    and something nobody would ever tap does not match.

    The last two matter as much as the first two. A pattern loose enough to
    match anything accepts junk as a real answer, which is worse than rejecting
    a real one - the respondent is never asked again and the row looks complete.
    And a pattern that accepts an ambiguous digit stores the wrong answer, which
    is worse still, because it is indistinguishable from a right one.
    """
    problems = []
    for key in QUESTION_KEYS:
        options = LANGS[lang]["arm2"][key]["options"]
        pattern = answer_pattern(options)
        ambiguous = positions_are_ambiguous(options)

        for index, option in enumerate(options, start=1):
            option_id, item = option[0], option[1]
            # option_id first: that is what a tapped list row actually sends.
            # Discovered the hard way - the first live test answered `p1_0`
            # where this expected "0 times", so every tap fell to the retry.
            must_accept = [option_id, item, f" {item} ", item.upper()]
            if not ambiguous:
                must_accept += [str(index), f"{index}."]
            for reply in must_accept:
                if not evaluate_condition("regex", pattern, reply):
                    problems.append(
                        f"{lang}: ARM2 {key} would not accept {reply!r}, so the "
                        f"option {item!r} is unreachable"
                    )

        # The collision itself. On a scale whose labels are numbers, position N
        # and label N mean different options, and accepting the bare digit picks
        # the wrong one silently.
        if ambiguous:
            for index in range(1, len(options) + 1):
                if evaluate_condition("regex", pattern, str(index)):
                    problems.append(
                        f"{lang}: ARM2 {key} has numeric option labels and still "
                        f"accepts the bare digit {index!r}. A respondent typing "
                        f"it means the label, not the position, and would be "
                        f"coded as the wrong option"
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


def send_content(name, content_sid, *, x=0, y=0, variables=None):
    """Build a terminal one-way message that sends an approved template.

    Used for the closing message to somebody who never replied. They never
    opened the 24-hour window, so this send is business-initiated and a
    free-form body would fail with 63016 - a template is the only way to reach
    them at all.
    """
    properties = {
        "offset": {"x": x, "y": y},
        "from": FROM,
        "message_type": "content_template",
        "content_sid": content_sid,
    }
    if variables:
        properties["content_variables"] = variables
    return {
        "name": name,
        "type": "send-message",
        "properties": properties,
        "transitions": [{"event": "sent"}, {"event": "failed"}],
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


def ask_content(
    name, content_sid, on_reply, *, x=0, y=0, variables=None, on_timeout="mark_no_reply"
):
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
            {"event": "timeout", "next": on_timeout},
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


#: A label that opens with a number, e.g. "0 projects", "1-2 times", "5 - Very
#: satisfied". The captured digits are what a respondent typing that number
#: would mean.
_LEADING_NUMBER = re.compile(r"^\s*(\d+)")


def positions_are_ambiguous(options) -> bool:
    """Whether accepting a bare typed digit would miscode an answer.

    Options are normally matched three ways: the row id, the label, and the
    option's position typed as a digit. That last one is only safe while the
    two readings of a number cannot disagree.

    Take a frequency scale reading ``0 projects / 1 project / 2-3 projects``.
    A respondent who means *one project* types ``1``. As a position, ``1`` is
    the first option - ``0 projects``. The answer is recorded, the status says
    answered, and it is off by one for exactly the respondents who typed rather
    than tapped. Nothing anywhere reports a problem.

    The test is a *mismatch*, not merely the presence of a number: a Likert
    running ``1 - Very dissatisfied`` … ``5 - Very satisfied`` puts label 5 at
    position 5, so both readings agree and the digit stays useful. Only when
    some label's own number differs from where it sits does the position
    alternative get dropped - from the split regex, from the Liquid that codes
    the reply, and from the Python twin that tests them. A bare digit then
    matches nothing and the respondent is asked again, which is the right
    outcome for an input that genuinely has two readings.
    """
    for index, option in enumerate(options, start=1):
        found = _LEADING_NUMBER.match(normalise_reply(option[1]))
        if found and int(found.group(1)) != index:
            return True
    return False


def error_body_for(table, options) -> str:
    """Pick the retry nudge that matches what this question can accept.

    The nudge is the one place the instrument tells a respondent how to answer,
    so it has to agree with the split. Inviting "just reply with the number"
    on a question whose labels are numbers asks for precisely the reply that
    :func:`positions_are_ambiguous` requires the split to refuse - the
    respondent does as they are told, is not understood, and is nudged again
    with the same instruction.
    """
    key = "error_option_labels" if positions_are_ambiguous(options) else "error_option"
    return table[key].format(button=table["arm2"]["button"])


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
    accept_positions = not positions_are_ambiguous(options)
    alternatives: list[str] = []
    for index, option in enumerate(options, start=1):
        # The id first, because that is what a tapped list row actually
        # returns. The label second, because a tapped quick-reply button
        # returns *its* title instead - the two interactive types disagree.
        # The position last, for anyone who ignores the menu and types - unless
        # the labels are numeric, in which case a bare digit has two readings
        # and is refused rather than guessed at.
        alternatives.append(escape_literal(option[0]))
        alternatives.append(escape_literal(option[1]))
        if accept_positions:
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
    accept_positions = not positions_are_ambiguous(options)
    for index, option in enumerate(options, start=1):
        accepted = [normalise_reply(option[0]), normalise_reply(option[1])]
        if accept_positions:
            accepted.append(str(index))
        if normalised in accepted:
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
    accept_positions = not positions_are_ambiguous(options)
    clauses = []
    for index, option in enumerate(options, start=1):
        alternatives = [
            f'"{normalise_reply(option[0])}"',
            f'"{normalise_reply(option[1])}"',
        ]
        if accept_positions:
            alternatives.append(f'"{index}"')
        clauses.append(
            f"{{% when {' or '.join(alternatives)} %}}{option_code(option, index)}"
        )
    return (
        f"{{% assign reply = widgets.{widget}.inbound.Body "
        f"| strip | downcase{removals} | strip %}}"
        f"{{% case reply %}}{''.join(clauses)}"
        "{% else %}other{% endcase %}"
    )


def stop_split(name, lang, on_continue, *, x, y):
    """Route a request to stop, before the reply is treated as an answer.

    Sits between every question and its store widget, in both arms. Without it
    a mid-survey "STOP" is stored as the answer to whatever was asked - in ARM 1
    verbatim, and in ARM 2 as a failed match that nudges twice and then asks the
    next question anyway. Being asked three more questions after saying stop is
    a research-ethics problem before it is a bug.

    Twilio's own opt-out handling covers the carrier keywords for SMS. Inside a
    WhatsApp session there is no such handling: it is an ordinary inbound
    message and nothing looks at it unless the flow does.
    """
    return split(
        f"stopcheck_{name}",
        f"{{{{widgets.{name}.inbound.Body}}}}",
        [(word_pattern(LANGS[lang]["stop_words"]), "mark_optout", "asked to stop")],
        on_continue,
        x=x,
        y=y,
        condition="regex",
    )


def open_question(arm, key, body, lang, *, y, next_state):
    """ARM 1: ask, accept whatever arrives, move on.

    No validation, deliberately. Validating an open answer would turn ARM 1
    into ARM 2 and destroy the comparison the demo exists to make. The one
    exception is a request to stop, which is not an answer to anything.
    """
    name = f"{arm}_{key}"
    x = arm_x(arm)
    return [
        ask(name, body, f"stopcheck_{name}", x=x, y=y),
        stop_split(name, lang, f"store_{name}", x=x, y=y + 60),
        set_vars(
            f"store_{name}",
            [(f"{name}_status", "answered")],
            next_state,
            x=x,
            y=y + 140,
        ),
    ]


def list_question(arm, key, content_sid, options, error_body, lang, *, y, next_state):
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
        ask_content(name, content_sid, f"stopcheck_{name}", x=x, y=y),
        # Ahead of validation: a "STOP" is not a badly-formatted answer, and
        # letting it fall through would nudge them twice and then ask the next
        # question.
        stop_split(name, lang, validate, x=x, y=y + 40),
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


def build(
    lang: str, content_sids: dict[str, str], functions: dict[str, str]
) -> dict[str, Any]:
    """Assemble one language's flow definition.

    Args:
        lang: Key into :data:`LANGS`.
        content_sids: Friendly name to HX SID, for every template the flow
            references. Taking these as an argument rather than reading them
            from Twilio keeps this function pure and testable offline.
        functions: The deployed Functions coordinates, as
            :func:`resolve_functions` returns them. Also an argument, and for
            the same reason - plus a second one: hard-coding them is what made
            the built flow work on exactly one Twilio account.

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
    needed = [
        table["intro_template"],
        table["close_template"],
        consent_template_name(lang),
    ]
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
                # Someone messaging the number cold does NOT get the survey.
                # In practice this path is almost entirely people being polite
                # after they finish - "thanks", "ok" - and re-sending the whole
                # opener to somebody who has just completed the survey is worse
                # than saying nothing. They also arrive with no preloaded data,
                # so they would produce a row with no caseid to join back to
                # the sampling frame, and the opener's name variable would be
                # empty, which fails the send outright (error 21656).
                #
                # They get one short acknowledgement and the execution ends. No
                # survey, no row: a dataset row should mean a sampled
                # respondent was asked something.
                {"event": "incomingMessage", "next": "unsolicited_reply"},
                {"event": "incomingCall"},
                {"event": "incomingConversationMessage"},
                {"event": "incomingParent"},
            ],
        },
        # The opening must be an approved template and must WAIT for a reply.
        # It is the only business-initiated message in the flow, so it is the
        # only one Meta reviews. Waiting means the respondent's reply opens the
        # 24-hour window, after which the buttons and lists below are free.
        send(
            "unsolicited_reply",
            table["unsolicited"],
            "unsolicited_reply",
            x=-600,
            y=-950,
        ),
        ask_content(
            "intro",
            content_sids[table["intro_template"]],
            "consent",
            x=0,
            y=-950,
            # A template variable that resolves to an empty string is rejected
            # outright - error 21656, "one or more variables resolve to null or an
            # empty string at send time" - and the entire opener fails to send. Two
            # ways that happens: somebody messages the number cold, so there is no
            # preloaded name at all, or a sample file has a blank cell. Both leave a
            # respondent who was contacted and then heard nothing. Seen live: a cold
            # inbound published a row reading `undeliverable` for exactly this.
            variables=[{"key": "1", "value": "{{flow.data.name | default: 'there'}}"}],
            # Someone who never answers the opener has never opened the 24-hour
            # window, so every later message to them is business-initiated and
            # fails with 63016. They get a published row and no closing message
            # - which is also the right thing to do rather than merely the only
            # possible one: there is nothing to thank a non-participant for.
            on_timeout="mark_never_started",
        ),
        ask_content(
            "consent",
            content_sids[consent_template_name(lang)],
            "split_consent",
            x=0,
            y=-820,
        ),
        # Tap or type, in either language's words. Nobody is enrolled by
        # ambiguity - but an unreadable reply is not a refusal either, and
        # routing it to `record_declined` made it one. "what is this?", a voice
        # note or an emoji would all have been published as an explicit
        # decline, and refusal rate is a headline number in a consent-based
        # study. Note the asymmetry that hid it: every ARM2 question gets two
        # retries, and the single most consequential question got none.
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
            "consent_unclear",
            x=0,
            y=-700,
            condition="regex",
        ),
        # One re-ask, then treat silence or a second unreadable reply as a
        # break-off rather than a decision. `consent_unclear` is its own value
        # so the two can be counted apart in the data.
        set_vars(
            "consent_unclear",
            [("set_consent", "unclear")],
            "consent_retry",
            x=-500,
            y=-700,
        ),
        ask_content(
            "consent_retry",
            content_sids[consent_template_name(lang)],
            "split_consent_retry",
            x=-500,
            y=-620,
            on_timeout="mark_no_reply",
        ),
        split(
            "split_consent_retry",
            "{{widgets.consent_retry.inbound.Body}}",
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
            # Still unreadable. Not enrolled, and not recorded as a refusal
            # either - `set_consent` stays `unclear` and the outcome is a
            # break-off, which is what it actually was.
            "mark_no_reply",
            x=-500,
            y=-540,
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
        #
        # noMatch goes to a marker widget rather than straight to ARM1. A
        # missing or misspelled `arm` column would otherwise send *everyone*
        # down ARM1 - the flow runs, every row publishes, `arm` is blank, and
        # the experiment has quietly become a single-condition survey. The
        # marker makes it one visible value in the data instead.
        split(
            "split_arm",
            "{{flow.data.arm}}",
            [("1", "ARM1_P1"), ("2", "ARM2_P1")],
            "mark_arm_missing",
            x=0,
            y=-460,
        ),
        set_vars(
            "mark_arm_missing",
            [("set_arm_missing", "1")],
            "ARM1_P1",
            x=-400,
            y=-400,
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
                lang,
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
                # there. And on a question whose labels are numbers, the
                # variant that does *not* invite a bare digit - inviting one
                # there asks for exactly the reply the split has to refuse.
                error_body_for(table, table["arm2"][key]["options"]),
                lang,
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
            # Asked to stop. Their answers so far are kept - they were given
            # freely, and discarding them would be its own kind of disrespect -
            # but nothing further is asked, and the outcome says why the rest
            # is blank so it is not read as a break-off.
            set_vars(
                "mark_optout",
                [("set_optout", "1"), ("outcome", "optout")],
                "finish",
                x=1300,
                y=1080,
            ),
            # Never answered the opener: the window never opened, so no closing
            # message is possible. The row is still published.
            set_vars(
                "mark_never_started",
                [("set_no_reply", "1"), ("outcome", "unreachable")],
                "finish",
                x=1700,
                y=1200,
            ),
            # The message did not arrive. Sending another one would fail the
            # same way, so this path gets no closing message either.
            set_vars(
                "mark_delivery_failed",
                [("set_fail", "1"), ("outcome", "undeliverable")],
                "finish",
                x=1300,
                y=1200,
            ),
            # Single convergence point. Every terminal path arrives here, so a
            # row is published whatever happened - complete, declined, timed
            # out or undeliverable.
            # Pure convergence point now that times come from UTC sources.
            # Kept as its own widget because every terminal path routes through
            # it, which is what guarantees a row exists whatever happened.
            set_vars(
                "finish",
                # `enc_status` is set optimistically here and overridden on the
                # encryption widget's failure branch. Setting it at the
                # convergence point means every published row carries the
                # column, so a blank identifier can be read as "this respondent
                # had no name" rather than "encryption failed and nobody knew".
                [("set_reached_finish", "1"), ("enc_status", "ok")],
                "function_encrypt",
                x=0,
                y=1320,
            ),
            {
                "name": "function_encrypt",
                "type": "run-function",
                "properties": {
                    "offset": {"x": 0, "y": 1440},
                    "service_sid": functions["service_sid"],
                    "environment_sid": functions["environment_sid"],
                    "function_sid": functions["encrypt_sid"],
                    "url": functions["encrypt_url"],
                    "parameters": [
                        {"key": "enc_name", "value": "{{flow.data.name}}"},
                        {
                            "key": "enc_p_number_original",
                            "value": "{{contact.channel.address}}",
                        },
                    ],
                },
                # A failed encryption still publishes - a row with no identifiers
                # is far better than no row - but it goes via a widget that says
                # so. Publishing straight from the failure branch writes
                # `enc_name=""` under `outcome=complete`, which is
                # indistinguishable from a respondent who had no name in the
                # sample file, and nothing anywhere records that the identifiers
                # were lost.
                "transitions": [
                    {"event": "success", "next": "publish_motherduck"},
                    {"event": "fail", "next": "mark_encrypt_failed"},
                ],
            },
            set_vars(
                "mark_encrypt_failed",
                [("enc_status", "encrypt_failed")],
                "publish_motherduck",
                x=-500,
                y=1500,
            ),
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
        # Whether the two columns above mean anything. `ok` or `encrypt_failed`.
        ("enc_status", "{{flow.variables.enc_status}}"),
        ("set_consent", "{{flow.variables.set_consent}}"),
        # Set only when `arm` was missing from the sample file. Without it, a
        # misspelled column silently routes everyone to ARM1 and the two-arm
        # comparison becomes a one-arm survey that nothing reports.
        ("set_arm_missing", "{{flow.variables.set_arm_missing}}"),
        ("set_complete", "{{flow.variables.set_complete}}"),
        ("set_no_reply", "{{flow.variables.set_no_reply}}"),
        ("set_fail", "{{flow.variables.set_fail}}"),
        ("outcome", "{{flow.variables.outcome}}"),
        # The join key back to the execution log. Without it a warehouse row
        # cannot be traced to the execution that produced it.
        ("execution_sid", "{{flow.sid}}"),
        # The two timestamps that bracket a respondent's participation, both
        # UTC. `sent_at` is supplied by the launcher at the moment of contact,
        # because Studio cannot produce a UTC time itself; `submitted_at` is
        # stamped server-side by the publish Function when the row is written,
        # which is the last step of the flow. Their difference is the time the
        # respondent took, and both mean what they say.
        ("sent_at", "{{flow.data.sent_at}}"),
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
                "service_sid": functions["service_sid"],
                "environment_sid": functions["environment_sid"],
                "function_sid": functions["publish_sid"],
                "url": functions["publish_url"],
                "parameters": [{"key": k, "value": v} for k, v in published],
            },
            # The publish step reports its own failure correctly, so the flow
            # must not throw that away. Routing `fail` to the closing message
            # thanks a respondent whose row does not exist and never will -
            # success from every angle at the moment of failure, and invisible
            # until someone counts the rows months later.
            #
            # There is nowhere to persist the flag except the execution context,
            # because the thing that persists rows is what just failed. That is
            # enough: `rtt fetch` reads the context, and the respondent is not
            # told the survey landed when it did not.
            "transitions": [
                {"event": "success", "next": "split_closing"},
                {"event": "fail", "next": "record_publish_failure"},
            ],
        }
    )

    states.append(
        {
            "name": "record_publish_failure",
            "type": "set-variables",
            "properties": {
                "offset": {"x": -1000, "y": 1680},
                # The flag only. Overwriting `outcome` would destroy the one
                # thing still worth having: no row was written, so the execution
                # context is the only surviving record of whether this
                # respondent completed, declined or timed out - and `rtt fetch`
                # reads exactly that. `set_publish_failed` already says what
                # went wrong without erasing what happened.
                "variables": [{"key": "set_publish_failed", "value": "1"}],
            },
            "transitions": [{"event": "next"}],
        }
    )

    states.extend(
        [
            # Four of the five outcomes get a closing message; they differ only
            # in which mechanism can reach the respondent.
            #
            #   complete / declined / incomplete   replied within the last hour,
            #                                      so the window is open and the
            #                                      body can be free-form and as
            #                                      long as the outcome needs
            #   unreachable                        never replied, so the window
            #                                      never opened - only an
            #                                      approved template gets there
            #   undeliverable                      the first message never
            #                                      arrived, so neither will this
            #                                      one. Nothing is sent
            split(
                "split_closing",
                "{{flow.variables.outcome}}",
                [
                    ("complete", "close_complete"),
                    ("declined", "close_declined"),
                    ("incomplete", "close_incomplete"),
                    ("unreachable", "close_never_started"),
                    ("undeliverable", "end_without_message"),
                    # One acknowledgement, so they know it worked. Saying stop
                    # and then hearing nothing is indistinguishable from saying
                    # stop and not being heard.
                    ("optout", "close_optout"),
                ],
                "end_without_message",
                x=0,
                y=1680,
            ),
            send_content(
                "close_never_started",
                content_sids[table["close_template"]],
                x=900,
                y=1800,
                variables=[
                    {"key": "1", "value": "{{flow.data.name | default: 'there'}}"}
                ],
            ),
            {
                "name": "end_without_message",
                "type": "set-variables",
                "properties": {
                    "offset": {"x": 1400, "y": 1800},
                    "variables": [{"key": "set_closed_silently", "value": "1"}],
                },
                "transitions": [{"event": "next"}],
            },
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
            send(
                "close_optout",
                table["stop_ack"],
                "close_optout",
                x=-900,
                y=1800,
            ),
        ]
    )

    # The closing messages and the unsolicited acknowledgement are terminal:
    # drop their outgoing transitions so the flow ends rather than looping back
    # into itself.
    for state in states:
        if state["name"].startswith("close_") or state["name"] == "unsolicited_reply":
            state["transitions"] = [{"event": "sent"}, {"event": "failed"}]

    return {
        "description": table["description"],
        "states": states,
        "initial_state": "Trigger",
        "flags": {"allow_concurrent_calls": True},
    }


def codebook_rows(lang: str) -> list[dict[str, str]]:
    """Return the value labels for this language's coded answers.

    The published row carries a code, not a label. That is deliberate - a label
    in every row is redundant, and it would be in whichever language that
    respondent happened to receive, which makes pooling the two impossible.

    What the data needs instead is value labels, the same thing a SurveyCTO
    choice list or a Stata `label define` provides. Generating them from the
    option tables that also build the flow means the labels cannot drift from
    the codes they describe: change an option and the codebook changes with it,
    in the same commit.

    `option_id` is included because it is what a tapped list row actually sends
    and therefore what lands in the raw answer column, so it is the join key
    between a raw reply and its meaning.
    """
    table = LANGS[lang]
    rows: list[dict[str, str]] = []
    for key in QUESTION_KEYS:
        question = table["arm2"][key]
        for index, option in enumerate(question["options"], start=1):
            rows.append(
                {
                    "lang": lang,
                    "arm": "2",
                    "question": f"ARM2_{key}",
                    "variable": f"ARM2_{key}_code",
                    "code": option_code(option, index),
                    "option_id": option[0],
                    "label": option[1],
                    "description": option[2],
                }
            )
    return rows


def write_codebook(lang: str) -> Path:
    """Write the value labels for a language as CSV."""
    path = CODEBOOK_DIR / f"data_use_demo_{LANGS[lang]['flow_suffix']}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = codebook_rows(lang)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


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


def resolve_functions(client) -> dict[str, str]:
    """Look up the deployed Functions service, environment, and both functions.

    Args:
        client: An authenticated Twilio client.

    Returns:
        The keys a ``run-function`` widget needs: ``service_sid``,
        ``environment_sid``, ``encrypt_sid``, ``publish_sid``, ``encrypt_url``
        and ``publish_url``.

    Raises:
        BuildError: If the service, its environment, or either function is
            missing - which means `just deploy-functions` has not been run on
            this account.

    Studio needs the deployed **url** as well as the three SIDs; validation
    rejects a run-function widget without it, and the domain carries a random
    per-account suffix, so it has to be read from the environment rather than
    constructed.

    """
    # No `limit=`: the SDK turns it into a page_size, Serverless caps that at
    # 100, and asking for more is a 400 rather than a truncation. Bare .list()
    # walks the pages, so an account with many services still resolves.
    service = next(
        (
            s
            for s in client.serverless.v1.services.list()
            if s.unique_name == FUNCTIONS_SERVICE_NAME
        ),
        None,
    )
    if service is None:
        raise BuildError(
            f"No Functions service named {FUNCTIONS_SERVICE_NAME!r} on this "
            f"account. Run `just deploy-functions` first - it creates the "
            f"service and deploys encrypt_fields and publish_motherduck."
        )

    environments = client.serverless.v1.services(service.sid).environments.list()
    environment = next(iter(environments), None)
    if environment is None:
        raise BuildError(
            f"The {FUNCTIONS_SERVICE_NAME!r} service has no environment. "
            f"Re-run `just deploy-functions`."
        )

    functions = {
        f.friendly_name: f.sid
        for f in client.serverless.v1.services(service.sid).functions.list()
    }
    missing = [
        name
        for name in (ENCRYPT_FUNCTION_NAME, PUBLISH_FUNCTION_NAME)
        if name not in functions
    ]
    if missing:
        raise BuildError(
            f"Deployed but incomplete: {FUNCTIONS_SERVICE_NAME!r} is missing "
            f"{', '.join(missing)}. Re-run `just deploy-functions`."
        )

    host = environment.domain_name
    return {
        "service_sid": service.sid,
        "environment_sid": environment.sid,
        "encrypt_sid": functions[ENCRYPT_FUNCTION_NAME],
        "publish_sid": functions[PUBLISH_FUNCTION_NAME],
        "encrypt_url": f"https://{host}{ENCRYPT_PATH}",
        "publish_url": f"https://{host}{PUBLISH_PATH}",
    }


def resolve_sids(lang: str) -> tuple[dict[str, str], list[str]]:
    """Look up the flow's content templates on the account by friendly name.

    Returns:
        The SIDs found, and the friendly names that do not exist yet.

    Resolving by name rather than pasting HX SIDs into this file means a
    recreated template is picked up on the next build instead of leaving a flow
    pointing at something that no longer exists.

    """
    cfg.load_env()
    conf = cfg.TwilioConfig.from_env()
    client = Client(conf.account_sid, conf.auth_token)

    table = LANGS[lang]
    wanted = [
        table["intro_template"],
        table["close_template"],
        consent_template_name(lang),
    ]
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

    book = write_codebook(lang)
    print(f"  codebook  {book.relative_to(REPO_ROOT).as_posix()}")

    try:
        cfg.load_env()
        conf = cfg.TwilioConfig.from_env()
        functions = resolve_functions(Client(conf.account_sid, conf.auth_token))
    except BuildError as exc:
        print(f"\n  {exc}")
        return False
    print(
        f"  functions {functions['service_sid']} "
        f"({functions['encrypt_url'].split('//')[1].split('/')[0]})"
    )

    found, missing = resolve_sids(lang)
    if missing:
        print("\n  Cannot build the flow yet - these content templates do not")
        print("  exist on this account. Create them, then re-run this build:")
        for name in missing:
            if name in (LANGS[lang]["intro_template"], LANGS[lang]["close_template"]):
                where = f"templates/{name}.json"
                note = "  # bookend: needs Meta approval before a real round"
            else:
                where = f"templates/generated/{name}.json"
                note = "  # in-session only: never submit this one"
            print(f"    just template-create {where}{note}")
        return False

    definition = build(lang, found, functions)
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
