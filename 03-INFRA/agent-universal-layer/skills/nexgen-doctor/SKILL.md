---
name: nexgen-doctor
description: Run the NeXgen alignment doctor and explain the result in plain language. Use when the user asks whether the agent layer is healthy or aligned, after a sync or engine cutover, or when something feels misconfigured.
---

# Vault doctor

Any text after the command is an optional specific concern to focus on.

1. Run the read-only doctor for this platform: `nexgen doctor` (the
   historical `agent-doctor` name still works; if neither is on PATH, fall
   back to `python3 03-INFRA/scripts/agent_sync.py doctor` from the engine
   root on Linux/macOS, or `py -3 03-INFRA\scripts\agent_sync.py doctor` on
   Windows). It never writes anything, so running it is always safe.
2. Open with one plain-language sentence: aligned or not, plus the counts
   of checks that passed, checks that failed, and checks that could not be
   determined (what `--summary` reports as OK/FAIL/UNDETERMINED).
3. For every failing check: quote the failing line, explain what it means
   in simple terms, and propose the smallest safe fix as a ready-to-paste
   command. Do not apply fixes without explicit confirmation (`--fix` exists
   for the ones with an automatic remedy, but only run it on request).
4. Summarize undetermined checks briefly (things the doctor couldn't verify
   from this machine, e.g. an unreachable server); expand only the ones
   that need a user decision.
5. If the user gave a specific concern, address it explicitly against the
   doctor output.
