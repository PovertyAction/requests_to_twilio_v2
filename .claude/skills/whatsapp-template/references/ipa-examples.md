# A library of templates that shipped, and the categories Meta gave them

Real copy from IPA WhatsApp studies, all of it `approved` and all of it sent to
respondents. The categories shown are what Meta actually assigned, not what was
requested - which is the whole reason this file exists. House style is already
established, and the category pattern is visible in copy that went out rather
than theoretical.

**Read this before drafting.** The use cases below are the ones IPA has actually
run - recruitment openings, consent-bearing invitations, incentive offers,
reminders, availability windows, and closings - so the nearest match is usually
already here. Lift the shape, then write your own words.

Pull the current set any time with:

```bash
just template-list                    # everything
just template-list --filter intro     # openings
just template-list --filter end       # closings
```

## The category pattern

The clearest signal in the account. Same organisation, same kind of study, and
the categories split cleanly on **how the message frames the ask**.

### Openings that Meta approved as UTILITY

`beat_intro2_recontacto` (es_MX) - the strongest example, because it names the
consent basis explicitly:

> Hola, {{1}}. Somos IPA Colombia. Esta es una encuesta de actualización de
> datos de 5 minutos, **para la cual nos autorizaste un nuevo contacto**. Te
> recordamos que estará disponible durante 24 horas. Para continuar con la
> encuesta, por favor oprime SÍ

`intro_capacitacion` (es) - identifies, states purpose, asks permission:

> Hola, {{1}} 👋. Somos IPA Colombia 🇨🇴. Te escribimos para que participes en
> una encuesta de prueba para la capacitación de Twilio. *¿Te gustaría
> participar?*

`rst_feedbackintro` (en_GB) - states what the interaction is, offers a choice:

> Hi, 👋 Welcome to the WhatsApp RST interactive Survey! In this interaction,
> you can provide *feedback* on the sessions or register for the *Innovation
> Fair*. Please select what you would like to do:

### Openings that Meta pushed to MARKETING

`edu_baseline_welcome2` (es) - the incentive is the ask, and the tone is
persuasive:

> Esta encuesta le tomará alrededor de 15 minutos ⏳. Luego de completar esta
> encuesta, **le daremos un bono de regalo por COP 10.000** para supermercados
> D1 🛒. **¿Se anima?** ¡Haga clic aquí y continúe en el estudio!

`intro_fim2_1` (es) - incentive bolded and prominent:

> ¡Hola! *Somos IPA Colombia*. El {{1}} respondió una encuesta sobre temas
> financieros... ***Si usted completa esta encuesta, recibirá un incentivo de
> $15,000 COP en bonos de supermercado***.

`edu_int_welcome1_v3` (es) - excitement framing:

> ¡Hola de nuevo! 👋 Somos IPA Colombia. **Tenemos contenido increíble preparado
> para usted** como parte del estudio... 🤩

### What the split shows

| Reads as | Lands in |
| --- | --- |
| "you authorised us to contact you again" | UTILITY |
| "you attended X, here is the follow-up" | UTILITY |
| "we have amazing content for you" | MARKETING |
| "complete this and receive $15,000 COP" | MARKETING |
| "¿Se anima?" / "Get X now" | MARKETING |

**The incentive trap is real and visible.** Every opening that leads with
compensation is MARKETING; every one that leads with the consent basis is
UTILITY. Incentives can still be mentioned - state them as a factual
consequence, late in the message, not as the reason to act.

`intro_fim2_1` is instructive: it *does* reference the prior survey, which is
the UTILITY lever, but the bolded incentive dominates and it landed in
MARKETING anyway. Position and emphasis matter, not just presence.

## The requested category matters too

Two near-identical closings, different categories:

| Template | Copy | Category |
| --- | --- | --- |
| `end1_msj` | "Muchas gracias por su atención. Que tenga un feliz resto de día. _Equipo IPA_" | **UTILITY** |
| `end_capacitacion` | "¡Muchas gracias por tu tiempo!" | **MARKETING** |

There is nothing promotional in either. The difference is almost certainly what
was requested at submission. Meta does not always override, so **ask for
UTILITY** - it is not merely a hint.

## House style for closings

A consistent formula across projects, all UTILITY:

> Muchas gracias por su atención. Que tenga un feliz resto de día.
> _Equipo IPA_

> ¡Muchísimas gracias por su participación! Que tenga un feliz resto de día 😎.
> _Equipo IPA_

> Muchísimas gracias por participar en esta corta encuesta. Que tenga un feliz
> resto de día 😎. _Equipo IPA y Fundación Juanfe_

Conventions worth keeping:

- **Sign off as `_Equipo IPA_`** in italics, adding the partner organisation
  where there is one (`_Equipo IPA y Fundación Juanfe_`).
- **Thank for participation, then close warmly.** Short. Nothing is being asked.
- **Name the partner** when a study runs with one - participants often know the
  partner better than they know IPA.

### Telling people the channel is closed

`rifa_wa_cierre` (es_AR, UTILITY) does something the others do not, and it is
worth copying:

> Hemos finalizado la comunicación por este canal. Esta encuesta es automática
> por lo que te pedimos por favor no responder a este chat.

An automated number that silently ignores replies is a bad last impression, and
replies to a dead flow are invisible. If nobody is monitoring the number after
the round, say so in the closing template.

## Opening formula, distilled

The UTILITY-approved openings share a shape:

1. **Greeting with name** - `Hola, {{1}} 👋` (put words around the variable;
   never open on a bare `{{1}}`)
2. **Identify** - `Somos IPA Colombia` / `Somos IPA`, plus the partner
3. **Consent basis** - "para la cual nos autorizaste un nuevo contacto",
   "usted fue formado por NRC... y autorizó ser contactado(a) a través de este
   medio", "El {{1}} respondió una encuesta sobre temas financieros"
4. **What and how long** - "una encuesta de actualización de datos de 5 minutos"
5. **The ask, as a choice** - "*¿Te gustaría participar?*" with SÍ / NO buttons

Step 3 is the one people skip, and it is the one that earns UTILITY.

## Further use cases

**Availability windows are stated up front.** `beat_intro2_recontacto` says
"estará disponible durante 24 horas". That is honest about the WhatsApp session
window and sets expectations - a participant who returns two days later would
otherwise hit a dead flow.

**Deadlines are stated in reminders.** `lego_reminder_aecs_c2b_endline`:
"ampliamos el plazo para contestar la encuesta hasta el mediodía del jueves, 19
de mayo", plus "*Si ya contestaste la encuesta, has caso omiso a este mensaje*"
so people who already responded are not nagged.

**Quick-reply and text variants are paired.** Several templates define both
`twilio/quick-reply` and `twilio/text` in one definition, with the text version
saying `¡Escriba "Quiero saber más"` where the button version says click. That
keeps the template usable where buttons are not rendered.

**Duration is disclosed.** "alrededor de 15 minutos", "5 minutos", "10 minutos".
Standard research practice and it helps completion rates.

## One caution about "anónima"

`lego_intro_aecs_c2b_endline` invites participation in "una encuesta
*anónima*". Responses arrive tied to a WhatsApp phone number, and in that flow
they were published to Google Sheets. Unless the pipeline genuinely separates
identifiers from responses, **anonymous is the wrong word** - "confidential" is
the accurate one, and even that has to be backed by encryption before publishing
to Sheets. Do not copy this phrasing without checking what the flow does.
