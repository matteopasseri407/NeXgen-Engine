---
name: nexgen-discipline
description: Use for ANY code change, before writing code. A search-first, smallest-diff, evidence-based workflow with an independent review for complex changes. The owner is not a programmer, so do not rely on them to catch mistakes.
---

# NeXgen discipline

The rules are in `03-INFRA/engineering-rules.md`. This is the order to apply them in.

0. **New project?** Run `python 03-INFRA/scripts/discipline.py init --smoke "<command>"` once, then `report` shows the owner the state.
1. **Restate** in two lines what you will do and what you will not touch.
2. **Search** (`rg`) for existing code that does it; list what you will reuse.
3. **Propose** the minimal diff. If it needs a dependency, script language, data-format change or CI/hook
   change, say so in plain words and wait for the owner's yes.
4. **Test first** for a bug; a behavior test for a feature.
5. **Implement** small. Logic in Python, shell only as a thin launcher.
6. **Check:** `python 03-INFRA/scripts/discipline.py check` and the project's tests. Show the output.
7. **Score:** `python 03-INFRA/scripts/discipline.py score`. A paid review is the exception: only if it says the change to a critical file is big, and only after the owner authorizes it (`review`, then `receipt`); or the owner settles it with `approve --reason`, which you never run on your own. Never for small or non-critical work.
8. **Self-critique:** what could break, what could you delete, what did you not verify.
9. **Report** in the owner's language, plain words: what changed, files, commands run, risks, next step.
