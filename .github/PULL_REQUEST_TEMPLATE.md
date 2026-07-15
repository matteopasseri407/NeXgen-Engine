## What this changes and why

<!-- The "why" matters more than the "what" -- link the issue if there is one. -->

## How it was tested

<!-- e.g. "python3 -m pytest from 03-INFRA/agent-universal-layer/tests/, all green"
     or "ran INIT.md end to end on a fresh Windows VM" -->

## Checklist

- [ ] Ran the regression suite locally (`python3 -m pytest` from
      `03-INFRA/agent-universal-layer/tests/`) and it's green
- [ ] If this touches a shell script or a PowerShell script: both OS
      dialects are covered, or there's an explicit reason one doesn't apply
- [ ] If this touches generated CLI config: I edited the generator
      (`render.py` / `agent_sync.py` / `skills-sync.py`), not a generated
      output file by hand
- [ ] If this changes the architecture (a component, a flow, a structural
      rule): `03-INFRA/agentic-layer-concept-map.md` is updated in the same
      PR
- [ ] No secrets, tokens, real IPs, or personal paths in the diff (CI's
      leak-scan will catch most of this, but it's not a substitute for
      checking)
