# Team use today

🇮🇹 [Italiano](it/team.md)

This page is for anyone evaluating NeXgen Engine for more than one person: two colleagues, or a small company.
Read it before adopting the engine as shared infrastructure. The security and identity model is mono-user by design today, and that has concrete consequences for a team.

The licence question is separate and still applies. NeXgen Engine is PolyForm Noncommercial 1.0.0: evaluating it internally is fine, while running it inside a company as part of paid or revenue-generating work is a commercial use the default licence does not cover.
No commercial licence is offered (see [COMMERCIAL.md](../COMMERCIAL.md)), so such use is not licensed, whatever you conclude about the mono-user constraints below.

## What "mono-user" means, concretely

- **One profile, not one per person.** `99-INDEX/USER-PROFILE.md` holds a single hardware and host description and a single model-team configuration for the whole vault.
  The framework has no concept of "which teammate is driving right now": there is one file, and whoever edited it last wins.
- **No shared secret store ships.** `99-SECRETS/` is a git-ignored local space; only `README.md` and `secrets-registry.md` are tracked, and the registry never holds values.
  Real credentials stay where each machine keeps them: OS keyring, per-user environment variables, per-CLI stores.
  There is no per-person credential scoping. Sharing a clone shares configuration and notes, never credentials, and there is no way to give one teammate read access to the n8n token without also handing them the OCR service key.
- **No per-file access control beyond your OS.** As [SECURITY.md](../SECURITY.md) states: "Any agent CLI with filesystem access to [the vault] can read and write everything inside."
  That is true for a solo user and just as true when three people share a clone: nothing in the framework stops one teammate's agent from editing another's notes, profile, or the shared secrets registry.
- **No role separation and no per-person audit trail.** There is no admin versus regular member, and no built-in log of who made a change, as opposed to which machine, beyond whatever authorship your own `git commit` setup produces.

## What this means if a small team adopts it today

- **Sharing one vault clone**, the shortest path to "everyone sees the same notes", puts everyone in the same trust domain: one shared profile, one shared trust boundary, no per-person credential scoping.
  Fine for two people who already trust each other with everything. Not a fit for a team that wants to compartmentalise who sees what.
- **Giving everyone a separate clone** avoids sharing credentials, but then nobody's notes, profile or model routing are shared automatically. You lose the team knowledge base and end up with N independent single-user installs that happen to use the same framework.
- The `MULTI` installation profile (see [INIT.md](../INIT.md), step 1) does not solve this: it keeps one person's CLIs and machines in sync with each other, not several people's identities in sync with one shared vault.
  The name suggests otherwise, so it is worth saying plainly.

Most small teams end up somewhere between these two options and accept the trade-off explicitly, rather than getting a clean answer from the framework.

## What is not here

Real multi-user support is not implemented in this release: per-person identity, scoped secrets, a role and permission model, per-person audit.
It is a separate, larger piece of work already planned, and not something to bolt on as a documentation patch.
This page exists so you can judge whether NeXgen Engine fits a team today, with today's constraints, instead of finding them out after the fact.

## A declarative routing aid is not multi-user support

`99-INDEX/USER-PROFILE.md` has an optional "Team members" section that declares who besides the vault owner uses this framework, so the Council `seats.yaml` does not have to be one file everyone edits at once, and a skill can be marked `scope: personal` so `nexgen skill sync` (historical name `skills-sync.py`) only puts it on its owner's machine.
That is all it does: it routes seats and skills, saying who owns which host, which seat file, which skills.
It is not a security boundary, and every constraint above still holds.
There is still one profile file where the last edit wins, one secrets archive with one key per enrolled machine, no OS-independent access control, and no per-person audit trail.
Declaring team members makes seat and skill routing more convenient; it adds no identity, isolation or permissions.
