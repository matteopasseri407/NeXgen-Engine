---
name: nexgen-council
description: Convene the AI Council (other installed agentic CLIs as advisors) on a question, plan, or piece of code. Use when the user wants a second opinion, a cross-vendor review, or to stress-test a decision.
---

# Convene the council

The text after the command is the question or plan to put to the council.

1. Check the council is configured: without a `seats.yaml` in the data
   root (`03-INFRA/agent-universal-layer/council/seats.yaml`) the council
   is deliberately inert. If it is missing, explain the one-time setup
   from `docs/council.md` and stop.
2. Pick the mode that fits the request: `brainstorm` (generate options),
   `challenge` (find the flaws in a plan), `code-review` (cross-vendor
   review of a diff), `consult` (the same brief to every seat in parallel,
   plus one aimed rebuttal round), `relay` (staged pipeline of seats).
   `council propose` invokes no model and prices nothing: it lists the
   seats verified on this machine for each role, primary and fallbacks.
3. The seat is the user's choice, never yours. The single-seat modes take
   one `--seat NAME`, `consult` takes one to five, `relay` takes
   `--sequence`. Without it the command prints the choices and stops
   instead of starting a model; pass `--auto` only when the user tells you
   to choose. A paid channel asks for its own confirmation on the terminal,
   and refuses the call when stdin is unavailable.
4. Tell the user which mode and seat(s) you are about to use and that the
   run spends those CLIs' own subscription quota; wait for their yes.
5. Run it, e.g. `council challenge "<the question>" --seat <seat>`, and
   wait for the transcript.
6. Synthesize the answers: what the advisors agree on, where they
   disagree (attribute positions to seats), and your own recommendation
   as one plain-language paragraph. Do not paste raw transcripts.
