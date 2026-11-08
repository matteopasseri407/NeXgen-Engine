# Routing tiers and model management: `nexgen models`

🇮🇹 [Italiano](it/models.md)

The supervisor ("sup") picks models based on explicit tiers and availability rather than glob patterns.
Model tiers live in `03-INFRA/agent-universal-layer/routing-tiers.yaml` in the KnowledgeVault, with a reference template in `03-INFRA/agent-universal-layer/templates/routing-tiers.yaml.example`.

## Commands

```bash
nexgen models list                   # tiers, candidates, and exhaustion status
nexgen models pick <tier> [--limited] # pick first available candidate
nexgen models trial <model> --harness <harness> # run deterministic benchmark trial
nexgen models promote <model>        # promote candidate to the head of worker tier
nexgen models rollback               # restore previous worker order
nexgen models discover               # compare inventory with worker tier and benchmark eligible Flash models
```

## Tiers structure

- `tiers.orchestrator`: frontier candidates for task planning and coordination.
- `tiers.worker`: ordered flat candidates for execution tasks, each specifying `model`, `harness`, and `requests_5h`.
- `tiers.limited`: free and contributor models intended strictly for non-sensitive tasks.

## Security boundary for limited tier

Unmarked tasks are treated as sensitive: the limited tier is never used unless explicitly requested with `--limited`.
The limited tier must never receive financial data, emails, identity information, or credentials. Code alone is not treated as sensitive.

## Automatic relaunch in `sup status`

When `sup status` finds an open unit whose session ran out of quota or stalled, it relaunches the same brief in a new session on the next candidate, once per blocked session. A flat model never falls back to the limited tier.

## Deterministic trial

`nexgen models trial` benchmarks a model against a reproducible Python bug in a temporary Git repository. A trial only passes if the real test passes within the timeout without modifying unrelated files.

## Automated discovery

`nexgen models discover` is manual. The first run only records the current inventory in `known_models` in the local state: it runs no trial and sends no alert. Later runs look only at models never seen before, on flat channels (`flat_prefixes`, default `opencode-go/`; names containing "free" or "contributor" are excluded, and Zen `opencode/` models are never considered). Each new Flash model with sufficient volume in `channels_volume` is benchmarked with `trial`. Each outcome (passed or failed, with its reason) is sent as a Megaphone alert: discover never promotes. To adopt a model that passed, run `nexgen models promote <modello>`; to undo, `nexgen models rollback`. A new model with unknown volume gets one alert asking for its number in `channels_volume`. On a server-role machine the beat runs discovery at most once every 24 hours. A model is trialled once, so a rollback is not undone by the next run.
