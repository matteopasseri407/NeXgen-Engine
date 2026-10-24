"""The evidence job: a local model's answer must stand on the receipts of its own turn.

Two halves, both executed rather than read from the source. The pure half loads `evidence.mjs`
and calls it with literal text and receipts, so every verdict below is what the module answered.
The hooked half loads the real plugin, registers it against a fake `api.on`, and fires
`after_tool_call`, `before_agent_finalize` and `message_sending` with the event fields OpenClaw
2026.10.6 documents. No gateway, no model, no network.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from conftest import REAL_UL

PLUGIN_SOURCE = REAL_UL / "hooks" / "openclaw-guardrail-plugin"
NODE = shutil.which("node") or "node"

PREAMBLE = """
import plugin from {uri};
import {{ dirname, join }} from "node:path";
import {{ fileURLToPath }} from "node:url";
const DIR = dirname(fileURLToPath(import.meta.url));
const handlers = new Map();
const commands = new Map();
plugin.register({{ on: (name, fn) => {{
  if (!handlers.has(name)) handlers.set(name, []);
  handlers.get(name).push(fn);
}}, registerCommand: (spec) => commands.set(spec.name, spec) }});
async function call(name, event, ctx) {{
  const list = handlers.get(name) || [];
  if (!list.length) return "MISSING";
  let answer = null;
  for (const handler of list) {{
    const result = await handler(event, ctx);
    if (result !== undefined && answer === null) answer = result;
  }}
  return answer;
}}
async function command(name) {{
  const spec = commands.get(name);
  if (!spec) return "MISSING";
  return spec.handler({{}});
}}
"""

PURE = """
import * as evidence from {uri};
const show = (value) => console.log(JSON.stringify(value));
"""


def deployed(tmp_path: Path, config: dict | None = None) -> Path:
    """The plugin as the deposit leaves it, with the engine's switch file beside it."""
    target = tmp_path / "deployed"
    target.mkdir(parents=True)
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    if config is not None:
        (target / "nexgen.config.json").write_text(json.dumps(config), encoding="utf-8")
    return target


def _run(driver: Path, body: str, env: dict | None = None):
    driver.write_text(body, encoding="utf-8")
    done = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False, env=env,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def drive(target: Path, script: str):
    """Runs `script` against the registered plugin in a fresh node process."""
    return _run(target / "driver.mjs", PREAMBLE.format(uri=json.dumps((target / "index.mjs").as_uri())) + script)


def pure(target: Path, script: str):
    """Runs `script` against the module's exported functions, with no plugin around them."""
    return _run(target / "pure.mjs", PURE.format(uri=json.dumps((target / "evidence.mjs").as_uri())) + script)


def read_of(path: str, text: str = "") -> dict:
    return {"tool": "read", "ok": True, "args": {"path": path}, "text": text}


# ------------------------------------------------------------------- the pure verdict


def test_a_grounded_answer_passes(tmp_path):
    verdict = pure(deployed(tmp_path), """
const receipts = [{tool: "read", ok: true, args: {path: "01-NOTE/progetto.md"}, text: ""}];
show(evidence.verifyAnswer("Il progetto è descritto [01-NOTE/progetto.md].", receipts));
""")

    assert verdict == []


def test_an_invented_read_is_flagged_without_any_receipt(tmp_path):
    verdict = pure(deployed(tmp_path), """
show(evidence.verifyAnswer("Ho letto la nota e l'ho riassunta.", []));
""")

    assert verdict == ["afferma lavoro svolto senza una ricevuta riuscita"]


def test_a_cited_source_that_was_never_read_is_named(tmp_path):
    verdict = pure(deployed(tmp_path), """
const receipts = [{tool: "read", ok: true, args: {path: "01-NOTE/buona.md"}, text: ""}];
show(evidence.verifyAnswer(
  "Riassunto da [01-NOTE/buona.md] con dettagli da [99-ALTRE/inventata.md].", receipts));
""")

    assert verdict == ["cita una fonte non letta: 99-ALTRE/inventata.md"]


def test_a_negated_claim_is_not_a_claim(tmp_path):
    verdict = pure(deployed(tmp_path), """
show([
  evidence.verifyAnswer("Non ho letto la nota.", []),
  evidence.verifyAnswer("Non ho trovato nessuna nota su Progetto.", [
    {tool: "memory_search", ok: false, args: {query: "falco"}, text: ""},
  ]),
]);
""")

    assert verdict == [[], []]


def test_a_write_claim_needs_a_write_receipt(tmp_path):
    verdict = pure(deployed(tmp_path), """
const written = [{tool: "write", ok: true, args: {path: "01-NOTE/x.md"}, text: ""}];
show([
  evidence.verifyAnswer("Ho salvato la nota.", []),
  evidence.verifyAnswer("Ho salvato la nota.", written),
]);
""")

    assert verdict == [["afferma una scrittura senza una ricevuta di scrittura"], []]


def test_a_test_claim_needs_an_execution_receipt(tmp_path):
    verdict = pure(deployed(tmp_path), """
const ran = [{tool: "exec", ok: true, args: {command: "pytest -q"}, text: "12 passed"}];
show([
  evidence.verifyAnswer("Ho eseguito i test.", []),
  evidence.verifyAnswer("Ho eseguito i test.", ran),
]);
""")

    assert verdict == [["afferma test o verifiche senza una ricevuta di esecuzione"], []]


def test_a_result_pattern_is_grounded_only_by_what_was_read(tmp_path):
    """Proximity to a citation is not grounding; the words of the claim must be in the receipt."""
    verdict = pure(deployed(tmp_path), """
const read = (text) => [{tool: "read", ok: true, args: {path: "01-NOTE/x.md"}, text}];
show([
  evidence.verifyAnswer("Il file è stato aggiornato.", read("")),
  evidence.verifyAnswer("Il file è stato aggiornato.", read("Il file è stato aggiornato ieri.")),
  evidence.verifyAnswer("Il file non è stato aggiornato.", read("Il file non è stato aggiornato.")),
]);
""")

    assert verdict == [
        ["afferma una scrittura senza una ricevuta di scrittura"],
        [],
        [],
    ]


def test_an_empty_retrieval_with_facts_is_an_invention(tmp_path):
    verdict = pure(deployed(tmp_path), """
const failed = [{tool: "memory_search", ok: false, args: {query: "falco"}, text: ""}];
show([
  evidence.verifyAnswer("Il budget di Progetto è 900 euro.", failed),
  evidence.verifyAnswer("Non ho trovato informazioni: la ricerca non ha prodotto risultati.", failed),
]);
""")

    assert verdict == [
        ["afferma fatti senza contenuto recuperato dal motore"],
        [],
    ]


def test_receipts_are_scoped_to_the_current_turn(tmp_path):
    """A read in the previous turn is not evidence for this answer."""
    verdict = pure(deployed(tmp_path), """
const earlier = [
  {role: "user", content: "leggi la nota"},
  {role: "assistant", content: [
    {type: "toolCall", id: "c1", name: "read", arguments: {path: "01-NOTE/x.md"}},
  ]},
  {role: "toolResult", toolCallId: "c1", toolName: "read", isError: false,
   content: [{type: "text", text: "il progetto si chiama Progetto"}]},
  {role: "assistant", content: [{type: "text", text: "Fatto."}]},
];
const again = [...earlier, {role: "user", content: "e adesso?"}];
show([evidence.receiptsOf(earlier), evidence.receiptsOf(again)]);
""")

    first, third = verdict
    assert [receipt["tool"] for receipt in first] == ["read"]
    assert first[0]["args"] == {"path": "01-NOTE/x.md"}
    assert "Progetto" in first[0]["text"]
    assert third == []


def test_the_tool_family_reads_an_mcp_name(tmp_path):
    verdict = pure(deployed(tmp_path), """
show([
  evidence.familyOf("vault-library__search_notes"),
  evidence.familyOf("vault-library__update_section"),
  evidence.familyOf("vault-library__read_note"),
  evidence.familyOf("read"),
  evidence.familyOf("exec"),
  evidence.familyOf("tts"),
]);
""")

    assert verdict == ["search", "write", "read", "read", "exec", "other"]


# --------------------------------------------------------------- the hooks, as OpenClaw fires

LOCAL = {"evidence": {"enabled": True, "providers": ["ollama", "llama-cpp"]}}


def test_a_cloud_model_is_never_checked(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
console.log(JSON.stringify(await call("before_agent_finalize", {
  provider: "anthropic", sessionKey: "s1", runId: "r1",
  lastAssistantMessage: "Ho letto la nota e l'ho riassunta.", messages: [],
}, {sessionKey: "s1"})));
""")

    assert verdict is None


def test_an_unsupported_answer_asks_for_one_more_pass(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
console.log(JSON.stringify(await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: false,
  lastAssistantMessage: "Ho letto la nota e l'ho riassunta.", messages: [],
}, {sessionKey: "s1"})));
""")

    assert verdict["action"] == "revise"
    assert verdict["retry"]["maxAttempts"] == 1
    assert verdict["retry"]["idempotencyKey"]
    assert verdict["retry"]["instruction"]
    assert "afferma lavoro svolto senza una ricevuta riuscita" in verdict["reason"]


def test_a_stop_hook_already_running_is_not_asked_again(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
console.log(JSON.stringify(await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: true,
  lastAssistantMessage: "Ho letto la nota e l'ho riassunta.", messages: [],
}, {sessionKey: "s1"})));
""")

    assert verdict is None


def test_the_note_lands_on_the_answer_that_earned_it(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
const answer = "Ho letto la nota e l'ho riassunta.";
await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: true,
  lastAssistantMessage: answer, messages: [],
}, {sessionKey: "s1"});
const judged = await call("message_sending", {to: "me", content: answer}, {sessionKey: "s1"});
const other = await call("message_sending", {to: "me", content: "Buongiorno."}, {sessionKey: "s1"});
const again = await call("message_sending", {to: "me", content: answer}, {sessionKey: "s1"});
console.log(JSON.stringify({judged, other, again}));
""")

    assert verdict["other"] is None
    assert verdict["again"] is None
    assert verdict["judged"]["content"].startswith("Ho letto la nota")
    assert "Affermazioni non verificate:" in verdict["judged"]["content"]
    assert "afferma lavoro svolto senza una ricevuta riuscita" in verdict["judged"]["content"]


def test_an_answer_that_stands_on_its_receipts_clears_the_note(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: true,
  lastAssistantMessage: "Ho letto la nota.", messages: [],
}, {sessionKey: "s1"});
const clean = await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r2", stopHookActive: false,
  lastAssistantMessage: "Il progetto è descritto [01-NOTE/x.md].",
  messages: [
    {role: "user", content: "riassumi"},
    {role: "assistant", content: [
      {type: "toolCall", id: "c1", name: "read", arguments: {path: "01-NOTE/x.md"}},
    ]},
    {role: "toolResult", toolCallId: "c1", toolName: "read", isError: false,
     content: [{type: "text", text: "Il progetto è descritto"}]},
  ],
}, {sessionKey: "s1"});
console.log(JSON.stringify({clean, delivered: await call("message_sending",
  {to: "me", content: "Ho letto la nota."}, {sessionKey: "s1"})}));
""")

    assert verdict["clean"] is None
    assert verdict["delivered"] is None


def test_the_receipt_stream_covers_a_host_that_withholds_the_transcript(tmp_path):
    target = deployed(tmp_path, LOCAL)

    verdict = drive(target, """
await call("after_tool_call", {
  toolName: "read", toolCallId: "c1", params: {path: "01-NOTE/x.md"},
  result: {content: [{type: "text", text: "Il progetto è descritto"}]},
}, {sessionKey: "s1", runId: "r1"});
console.log(JSON.stringify(await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: false,
  lastAssistantMessage: "Ho letto la nota: il progetto si chiama Progetto.",
}, {sessionKey: "s1"})));
""")

    assert verdict is None


def test_the_switch_off_means_no_check(tmp_path):
    cases = (None, {"evidence": {"enabled": False, "providers": ["ollama"]}},
             {"evidence": {"enabled": True, "providers": []}})
    for index, config in enumerate(cases):
        target = deployed(tmp_path / f"case{index}", config)

        verdict = drive(target, """
console.log(JSON.stringify(await call("before_agent_finalize", {
  provider: "ollama", sessionKey: "s1", runId: "r1", stopHookActive: false,
  lastAssistantMessage: "Ho letto la nota e l'ho riassunta.", messages: [],
}, {sessionKey: "s1"})));
""")

        assert verdict is None, config
