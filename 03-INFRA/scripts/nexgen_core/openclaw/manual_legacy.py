"""The handover wording the previous release shipped, kept only to recognise its files.

One job: hold the exact handover section the release before this one wrote into
every manual, so an ownership test can still call that file the engine's. Why: the
org chart moved out of the manual body into its own managed block, and a workspace
prepared by the previous release must not start looking like the person's work
just because this engine words that section differently.

Nothing renders this text: no new workspace receives it. It exists once, here, and
`templates.shipped_manuals` consults it after the current wording.
"""
from __future__ import annotations

#: The previous release's handover section, by language and manual name.
PREVIOUS_HANDOVER: dict[str, dict[str, str]] = {
    "it": {
        "main": """## Passare il lavoro

- Scrivi a un altro agente con `sessions_send`. I destinatari possibili sono
  `capo` e `automazioni`.
- Passa un compito, non un ordine vago: cosa va fatto, dove, e come si saprà che
  è fatto.
- Se il destinatario non è disponibile, dillo alla persona: non fare il suo
  lavoro al posto suo.""",
        "capo": """## Passare il lavoro

- Scrivi all'assistente con `sessions_send` quando il risultato va raccontato
  alla persona, o quando il compito non è codice.
- Passa un compito, non un ordine vago: cosa va fatto, dove, e come si saprà che
  è fatto.""",
        "automazioni": """## Passare il lavoro

- Scrivi all'assistente con `sessions_send` quando il risultato va raccontato
  alla persona.
- Passa a `capo` le modifiche al codice del motore o dei servizi.
- Passa un compito, non un ordine vago: cosa va fatto, dove, e come si saprà che
  è fatto.""",
        "sviluppo": """## Passare il lavoro

- Quando il risultato va raccontato alla persona, scrivi all'assistente con
  `sessions_send`.
- Passa un compito, non un ordine vago: cosa va fatto, dove, e come si saprà che
  è fatto.""",
    },
    "en": {
        "main": """## Handing work over

- Write to another agent with `sessions_send`. The possible recipients are
  `capo` and `automazioni`.
- Hand over a task, not a vague order: what has to be done, where, and how it
  will be known to be done.
- If the recipient is unavailable, tell the person: do not do their work for them.""",
        "capo": """## Handing work over

- Write to the assistant with `sessions_send` when the result has to be told to
  the person, or when the task is not code.
- Hand over a task, not a vague order: what has to be done, where, and how it
  will be known to be done.""",
        "automazioni": """## Handing work over

- Write to the assistant with `sessions_send` when the result has to be told to
  the person.
- Hand engine or service code changes to `capo`.
- Hand over a task, not a vague order: what has to be done, where, and how it
  will be known to be done.""",
        "sviluppo": """## Handing work over

- When the result has to be told to the person, write to the assistant with
  `sessions_send`.
- Hand over a task, not a vague order: what has to be done, where, and how it
  will be known to be done.""",
    },
}
