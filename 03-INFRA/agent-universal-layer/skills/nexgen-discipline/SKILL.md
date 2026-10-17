---
name: nexgen-discipline
description: Use for ANY code change, before writing code. A search-first, smallest-diff, evidence-based workflow. The owner is not a programmer, so do not rely on them to catch mistakes.
---

# NeXgen discipline

The rules are in `03-INFRA/engineering-rules.md`. This is the order to apply them in.

0. **New project?** Run `python 03-INFRA/scripts/discipline.py init --smoke "<command>"` once, then `report` shows the owner the state.
1. **Interview if unclear.** When the request is vague or a design choice is open, grill the owner before building: numbered
   rounds, every question with your recommended answer worded so that "yes" accepts it, facts looked up by you and never asked, and
   no work until the owner confirms. Otherwise **restate** in two lines what you will do and what you will not touch.
2. **Search** (`rg`) for existing code that does it; list what you will reuse.
3. **Propose** the minimal diff. If it needs a dependency, script language, data-format change or CI/hook
   change, say so in plain words and wait for the owner's yes (unattended: write the proposal down and do not do it).
4. **Test first** for a bug: build a command that goes red on exactly this bug before touching the code, and commit it with the fix as `fix: ...`
   (the gate checks that the test fails on the old code). A behavior test for a feature. Critical code never ships without a test.
5. **Implement** small. Logic in Python, shell only as a thin launcher.
6. **Check:** `python 03-INFRA/scripts/discipline.py check` and the project's tests. Show the output.
7. **Score:** `python 03-INFRA/scripts/discipline.py score` is switched off and says so: no change needs a review and no gate checks a receipt. A review is the owner's call, not a step: if they ask for one, `review` writes the brief, `council code-review` runs it, and `approve --reason` records the owner's own decision, which you never run on your own.
8. **Self-critique:** what could break, what could you delete, what did you not verify.
9. **Report** in the owner's language, plain words: what changed, files, commands run, risks, next step.
