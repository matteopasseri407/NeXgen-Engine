#!/usr/bin/env python3
"""Generatore MCP — Vault 2.0 Fase 1.
Legge manifest.yaml e, per ogni CLI, costruisce la config MCP nel dialetto
giusto.
  - default (--diff): confronta col file vivo, NON scrive. I segreti sono
    ridotti a <AUTH> su entrambi i lati: si confronta la struttura senza mai
    toccare i token.
  - --write CLI: rigenera SOLO la sezione MCP di quella CLI dal manifest, con
    una sostituzione chirurgica (il resto del file resta intatto), nello stile
    del file. Fa backup, valida, e si AUTOBLOCCA se una sezione non-MCP
    risulterebbe modificata.
  - Server FUORI MANIFEST nel file vivo: mai cancellati. Vengono CONSERVATI
    tali e quali e segnalati (regola additiva: una novità installata da un
    agente è il nuovo standard da registrare nel manifest e propagare)."""
from __future__ import annotations
import argparse, difflib, json, os, platform, re, sys, time, tomllib
from pathlib import Path
import yaml

HOME = Path.home()
HERE = Path(__file__).parent
MANIFEST = HERE / "manifest.yaml"
IS_WINDOWS = platform.system() == "Windows"

SECRET_KEY = re.compile(r"(token|secret|password|authorization|bearer|api[_-]?key|cookie)", re.I)
LONGTOK = re.compile(r"^[A-Za-z0-9_\-\.=+/]{40,}$")

def redact(obj, key=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x, key) for x in obj]
    if isinstance(obj, str):
        if key and SECRET_KEY.search(str(key)):
            return "<AUTH>"
        if "${" in obj or "{env:" in obj:
            return "<AUTH>"
        if obj.lower().startswith("authorization:") or "bearer " in obj.lower():
            return "<AUTH>"
        if LONGTOK.match(obj) and any(c.isdigit() for c in obj):
            return "<AUTH>"
    return obj

# ---- render per dialetto (valori REALI: env-ref dove serve) ------------------

def r_claude(name, s):
    if s["transport"] == "stdio":
        return {"type": "stdio", "command": s["command"], "args": s.get("args", []), "env": s.get("env", {})}
    # header via env-ref: Claude Code espande ${VAR} negli header all'avvio,
    # cosi' il token non resta in chiaro in .claude.json.
    return {"type": "http", "url": s["url"],
            "headers": {"Authorization": f"Bearer ${{{s['auth']['env']}}}"}}

def r_codex(name, s):
    if s["transport"] == "stdio":
        d = {"command": s["command"], "args": s.get("args", [])}
        if s.get("env"):
            d["env"] = s["env"]
        return d
    t = s.get("timeouts", {})
    return {"url": s["url"], "bearer_token_env_var": s["auth"]["env"],
            "startup_timeout_sec": float(t.get("startup", 120)),
            "tool_timeout_sec": float(t.get("tool", 120))}

def r_antigravity(name, s):
    if s["transport"] == "stdio":
        return {"command": s["command"], "args": s.get("args", []), "env": s.get("env", {})}
    hdr = f"Authorization: Bearer ${{{s['auth']['env']}}}"
    return {"command": "npx", "args": ["-y", "mcp-remote", s["url"], "--header", hdr], "env": {}}

def r_opencode(name, s):
    if s["transport"] == "stdio":
        d = {"type": "local", "command": [s["command"], *s.get("args", [])], "enabled": True}
        if s.get("timeouts", {}).get("tool"):
            d["timeout"] = int(float(s["timeouts"]["tool"]) * 1000)
        if s.get("env"):
            d["environment"] = s["env"]
        return d
    url = "{env:%s}" % s["url_env"] if s.get("url_env") else s["url"]
    auth = "Bearer {env:%s}" % s["auth"]["env"]
    return {"type": "remote", "url": url, "headers": {"Authorization": auth}, "enabled": True, "oauth": False}

CLI = {
    "claude":      dict(render=r_claude,      name=lambda n: n),
    "codex":       dict(render=r_codex,       name=lambda n: n.replace("-", "_")),
    "antigravity": dict(render=r_antigravity, name=lambda n: n),
    "opencode":    dict(render=r_opencode,    name=lambda n: n),
}

def os_view(s):
    """Vista del server per l'OS corrente: se siamo su Windows e il server ha un
    blocco 'windows:' (override di command/args/env/...), lo applica; altrimenti
    scarta la chiave 'windows'. Cosi' il manifest unico serve entrambi gli OS —
    i valori Windows si popolano girando render.py sul fisso, non si indovinano."""
    if IS_WINDOWS and isinstance(s.get("windows"), dict):
        merged = {**s, **s["windows"]}
        merged.pop("windows", None)
        return merged
    return {k: v for k, v in s.items() if k != "windows"}

def _env_present(var):
    """True se la env var e' definita e non vuota."""
    v = os.environ.get(var, "").strip()
    return bool(v)

def _required_ok(s):
    """Respect manifest `require_env`: skip a server if its gating env var
    is unset. Lets a Local-Only install omit Cloud-Only MCP (firecrawl, n8n,
    vault-library, vault-ocr) instead of pointing them at dead ports."""
    req = s.get("require_env")
    if not req:
        return True
    return _env_present(req)

def load_manifest():
    raw = yaml.safe_load(MANIFEST.read_text("utf-8"))["servers"]
    out = {}
    for n, s in raw.items():
        s = os_view(s)
        if not _required_ok(s):
            print(f">>> skip [{n}]: require_env non soddisfatto (Local-Only?)")
            continue
        out[n] = s
    return out

def keep_extras(gen, live, label):
    """Un server nel file vivo ma NON nel manifest non è drift da cancellare:
    è una novità installata da un agente (regola del vault: 'non è drift, è il
    nuovo standard che va per tutti'). Si CONSERVA tale e quale e si segnala
    finché non viene registrato nel manifest. Codex fa già
    così by-design (patch per-sezione); qui si allineano i writer JSON."""
    out = dict(gen)
    for k in live:
        if k not in out:
            out[k] = live[k]
            print(f">>> FUORI MANIFEST [{label}]: server '{k}' CONSERVATO. Registralo in manifest.yaml per propagarlo ovunque.")
    return out

# ---- caricamento config viventi (solo sezione MCP) ---------------------------

def load_current(cli):
    try:
        if cli == "claude":
            return json.loads((HOME / ".claude.json").read_text("utf-8")).get("mcpServers", {})
        if cli == "codex":
            d = tomllib.loads((HOME / ".codex/config.toml").read_text("utf-8"))
            return {k: {kk: vv for kk, vv in v.items() if kk != "tools"} for k, v in d.get("mcp_servers", {}).items()}
        if cli == "antigravity":
            d = json.loads((HOME / ".gemini/antigravity/mcp_config.json").read_text("utf-8"))
            return {k: {kk: vv for kk, vv in v.items() if kk != "$typeName"} for k, v in d.get("mcpServers", {}).items()}
        if cli == "opencode":
            return json.loads((HOME / ".config/opencode/opencode.json").read_text("utf-8")).get("mcp", {})
    except FileNotFoundError:
        return None     # CLI non installata su questa macchina
    return {}

# ---- diff strutturale (modalita' --diff) -------------------------------------

def diff_struct(path, cur, exp, out):
    if isinstance(exp, dict) and isinstance(cur, dict):
        for k in sorted(set(exp) | set(cur)):
            if k not in cur:
                out.append(f"    - {path}{k}: MANCA nel file vivo (atteso: {json.dumps(exp[k], ensure_ascii=False)})")
            elif k not in exp:
                out.append(f"    + {path}{k}: in piu' nel file vivo (valore: {json.dumps(cur[k], ensure_ascii=False)})")
            else:
                diff_struct(f"{path}{k}.", cur[k], exp[k], out)
    elif cur != exp:
        out.append(f"    ~ {path[:-1]}: vivo={json.dumps(cur, ensure_ascii=False)}  atteso={json.dumps(exp, ensure_ascii=False)}")

def cmd_diff():
    man = load_manifest()
    ok = bad = extra = 0
    for cli, spec in CLI.items():
        current = load_current(cli)
        print(f"\n========== {cli.upper()} ==========")
        if current is None:
            print("  (config non presente: CLI non installata su questa macchina, saltata)"); continue
        wanted = {n: s for n, s in man.items() if cli in s["targets"]}
        seen = set()
        for name, s in wanted.items():
            key = spec["name"](name); seen.add(key)
            exp = redact(spec["render"](name, s))
            if key not in current:
                print(f"  [MANCA]  {name} -> il file vivo non ha '{key}'"); bad += 1; continue
            out = []
            diff_struct("", redact(current[key]), exp, out)
            if out:
                print(f"  [DIFF]   {name}"); print("\n".join(out)); bad += 1
            else:
                print(f"  [OK]     {name}"); ok += 1
        for k in sorted(set(current) - seen):
            print(f"  [EXTRA]  '{k}' nel file vivo ma non nel manifest (conservato dai --write: registralo per propagarlo)"); extra += 1
    print(f"\n---- riepilogo: {ok} server combaciano, {bad} con differenze, {extra} fuori manifest ----")

# ---- serializzatori per-stile ------------------------------------------------

def s_inline(obj, ind=0):
    """OpenCode: array di scalari inline, oggetti espansi."""
    pad, pad2 = "  " * ind, "  " * (ind + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        body = ",\n".join(f'{pad2}{json.dumps(k, ensure_ascii=False)}: {s_inline(v, ind + 1)}' for k, v in obj.items())
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in obj) + "]"
        body = ",\n".join(f'{pad2}{s_inline(x, ind + 1)}' for x in obj)
        return "[\n" + body + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)

def s_standard(obj, ind=0):
    """Antigravity: json.dump standard indent=2 (array espansi)."""
    return json.dumps(obj, indent=2, ensure_ascii=False)

def reorder(gen, live):
    """Allinea l'ordine delle chiavi di gen a quello di live (valori da gen)."""
    if isinstance(gen, dict) and isinstance(live, dict):
        out = {k: reorder(gen[k], live[k]) for k in live if k in gen}
        for k in gen:
            out.setdefault(k, gen[k])
        return out
    return gen

def _value_span(text, brace_idx):
    """Indice subito dopo la '}' che chiude l'oggetto iniziato a brace_idx,
    saltando le stringhe (e le graffe dentro le stringhe, es. {env:...})."""
    depth = 0; i = brace_idx; in_str = esc = False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("graffa di chiusura non trovata")

# ---- scrittura chirurgica generica per file JSON -----------------------------

def _prune_backups(path, keep=3):
    """Tiene solo gli ultimi `keep` backup .bak-* di un file, elimina i piu' vecchi."""
    backs = sorted(path.parent.glob(path.name + ".bak-*"))
    for old in backs[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass

def write_json_section(path, key, new_section, live_section, serialize, indent_exact=None):
    if not path.exists():
        print(f">>> {path.name} non presente: CLI non configurata, salto."); return 0
    raw = path.read_text("utf-8")
    live = json.loads(raw)
    # indent_exact: se dato, aggancia SOLO la chiave a quell'indentazione esatta
    # (per .claude.json, dove "mcpServers" compare anche annidato nei projects).
    ind_pat = re.escape(indent_exact) if indent_exact is not None else r'[ \t]*'
    m = re.search(rf'(?m)^({ind_pat}){re.escape(json.dumps(key))}[ \t]*:[ \t]*', raw)
    if not m or raw[m.end():m.end() + 1] != "{":
        print(f">>> STOP: non trovo la sezione {json.dumps(key)} all'indent atteso."); return 2
    indent = m.group(1)
    end = _value_span(raw, m.end())
    inner = serialize(new_section)
    lines = inner.split("\n")
    block_val = lines[0] + "\n" + "\n".join(indent + l for l in lines[1:])
    block = f'{indent}{json.dumps(key)}: ' + block_val
    new_text = raw[:m.start()] + block + raw[end:]

    if new_text[:m.start()] != raw[:m.start()] or not new_text.endswith(raw[end:]):
        print(">>> STOP: la sostituzione tocca testo fuori dalla sezione."); return 2
    try:
        new_parsed = json.loads(new_text)
    except json.JSONDecodeError as e:
        print(f">>> STOP: risultato non JSON valido ({e})."); return 2
    for k in live:
        if k != key and new_parsed.get(k) != live[k]:
            print(f">>> STOP: la sezione non-MCP '{k}' risulterebbe modificata."); return 2

    def _mask(line):   # mai un token in chiaro in NESSUN output del generatore
        return re.sub(r'(Bearer )[^"\s]+', r'\1<MASK>', line)
    d = list(difflib.unified_diff(raw.splitlines(), new_text.splitlines(),
                                  f"{path.name} (vivo)", f"{path.name} (generato)", lineterm=""))
    print("\n".join(_mask(l) for l in d) if d else "(nessuna differenza testuale: gia' conforme)")
    sem = []
    diff_struct("", live_section, new_section, sem)
    print("\nDifferenze SEMANTICHE nella sezione MCP (ordine-indipendenti):")
    print("\n".join(_mask(l) for l in sem) if sem else "    (nessuna: gia' conforme al manifest)")
    if not d:
        print("\n>>> Niente da scrivere."); return 0

    bak = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    bak.write_text(raw, "utf-8")
    _prune_backups(path)
    path.write_text(new_text, "utf-8")
    json.loads(path.read_text("utf-8"))
    print(f"\n>>> SCRITTO e validato (JSON ok). Backup: {bak}")
    return 0

def write_opencode():
    path = HOME / ".config/opencode/opencode.json"
    if not path.exists():
        print(">>> opencode.json non presente: OpenCode non configurato, salto."); return 0
    live = json.loads(path.read_text("utf-8"))
    man = load_manifest()
    gen = {n: r_opencode(n, s) for n, s in man.items() if "opencode" in s["targets"]}
    gen = keep_extras(gen, live.get("mcp", {}), "opencode")
    new_mcp = reorder(gen, live.get("mcp", {}))
    return write_json_section(path, "mcp", new_mcp, live.get("mcp", {}), s_inline)

def write_antigravity():
    path = HOME / ".gemini/antigravity/mcp_config.json"
    if not path.exists():
        print(">>> mcp_config.json non presente: Antigravity non configurato, salto."); return 0
    live = json.loads(path.read_text("utf-8"))
    live_servers = live.get("mcpServers", {})
    man = load_manifest()
    gen = {}
    for n, s in man.items():
        if "antigravity" not in s["targets"]:
            continue
        d = r_antigravity(n, s)
        for k, v in live_servers.get(n, {}).items():   # preserva extra interni (es. $typeName)
            d.setdefault(k, v)
        gen[n] = d
    gen = keep_extras(gen, live_servers, "antigravity")
    new_servers = reorder(gen, live_servers)
    return write_json_section(path, "mcpServers", new_servers, live_servers, s_standard)

# ---- scrittura chirurgica per Codex (TOML, patch mirato per-sezione) ---------

def _toml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)            # 120.0, 600.0
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)

def _codex_body(d):
    return [f"{k} = {_toml_scalar(v)}" for k, v in d.items()]

def _section_headers(lines):
    out = {}
    for i, l in enumerate(lines):
        m = re.match(r'^\[(.+?)\]\s*$', l)
        if m:
            out[m.group(1)] = i
    return out

def _content_range(lines, header_idx):
    """Righe-contenuto subito dopo un header di sezione: fino a riga vuota,
    prossimo header, o EOF. Non tocca header ne' righe vuote di separazione."""
    s = header_idx + 1
    e = s
    while e < len(lines) and lines[e].strip() != "" and not lines[e].startswith("["):
        e += 1
    return s, e

def write_codex(path=None):
    path = path or HOME / ".codex/config.toml"
    if not path.exists():
        print(f">>> {path.name} non presente: Codex non configurato, salto."); return 0
    raw = path.read_text("utf-8")
    lines = raw.split("\n")
    live = tomllib.loads(raw)
    live_srv = live.get("mcp_servers", {})
    man = load_manifest()

    targets = {}   # cname -> (direct_fields, env_or_None)
    for n, s in man.items():
        if "codex" not in s["targets"]:
            continue
        full = dict(r_codex(n, s))
        env = full.pop("env", None)
        targets[n.replace("-", "_")] = (full, env)

    headers = _section_headers(lines)
    # punto di inserimento per server NUOVI = fine dell'ultimo blocco mcp_servers.*
    mcp_ends = [_content_range(lines, idx)[1] for h, idx in headers.items()
                if h == "mcp_servers" or h.startswith("mcp_servers.")]
    insert_pos = max(mcp_ends) if mcp_ends else len(lines)

    edits = []        # (start, end, new_lines) — patch in-place di sezioni esistenti
    add_block = []    # righe dei server NUOVI da accodare al blocco mcp_servers
    for cname, (direct, env) in targets.items():
        if f"mcp_servers.{cname}" in headers:
            s, e = _content_range(lines, headers[f"mcp_servers.{cname}"])
            edits.append((s, e, _codex_body(direct)))
            if env is not None:
                if f"mcp_servers.{cname}.env" in headers:
                    s2, e2 = _content_range(lines, headers[f"mcp_servers.{cname}.env"])
                    edits.append((s2, e2, _codex_body(env)))
                else:
                    print(f">>> STOP: [mcp_servers.{cname}] esiste ma manca [.env] — caso raro, non patcho."); return 2
        else:
            add_block += ["", f"[mcp_servers.{cname}]"] + _codex_body(direct)
            if env is not None:
                add_block += ["", f"[mcp_servers.{cname}.env]"] + _codex_body(env)
    if add_block:
        edits.append((insert_pos, insert_pos, add_block))

    new_lines = lines[:]
    for s, e, nl in sorted(edits, key=lambda x: x[0], reverse=True):
        new_lines[s:e] = nl
    new_text = "\n".join(new_lines)

    # --- guard: parsing + non-MCP intatto + tools preservati + campi == manifest
    try:
        np_ = tomllib.loads(new_text)
    except Exception as ex:
        print(f">>> STOP: risultato non TOML valido ({ex})."); return 2
    for k in live:
        if k != "mcp_servers" and np_.get(k) != live[k]:
            print(f">>> STOP: la sezione non-MCP '{k}' risulterebbe modificata."); return 2
    for cname, (direct, env) in targets.items():
        ns = np_["mcp_servers"][cname]
        ls = live_srv.get(cname)              # None se e' un server nuovo
        if ls is not None and ns.get("tools") != ls.get("tools"):
            print(f">>> STOP: l'overlay 'tools' di {cname} risulterebbe modificato."); return 2
        for kk, vv in direct.items():
            if ns.get(kk) != vv:
                print(f">>> STOP: campo {cname}.{kk} non combacia col manifest."); return 2
        if env is not None and ns.get("env") != env:
            print(f">>> STOP: env di {cname} non combacia."); return 2
    for cname in live_srv:                          # nessun altro server MCP toccato
        if cname not in targets and np_["mcp_servers"][cname] != live_srv[cname]:
            print(f">>> STOP: il server non-manifest '{cname}' risulterebbe modificato."); return 2

    d = list(difflib.unified_diff(raw.splitlines(), new_text.splitlines(),
                                  f"{path.name} (vivo)", f"{path.name} (generato)", lineterm=""))
    print("\n".join(d) if d else "(nessuna differenza testuale: Codex gia' conforme)")
    if not d:
        print("\n>>> Niente da scrivere."); return 0

    bak = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    bak.write_text(raw, "utf-8")
    _prune_backups(path)
    path.write_text(new_text, "utf-8")
    tomllib.loads(path.read_text("utf-8"))
    print(f"\n>>> SCRITTO e validato (TOML ok). Backup: {bak}")
    return 0

def write_claude(path=None):
    """Patch chirurgico del SOLO mcpServers top-level (indent 2) di .claude.json.
    Preserva i token letterali del file vivo (il manifest non li contiene) e
    ignora i mcpServers annidati nei projects. Fail-safe: i guard di
    write_json_section bloccano se toccherebbe altro. Da eseguire a Claude
    CHIUSO (Claude riscrive .claude.json a caldo)."""
    path = path or HOME / ".claude.json"
    if not path.exists():
        print(">>> .claude.json non presente: Claude non configurato, salto."); return 0
    live = json.loads(path.read_text("utf-8"))
    live_mcp = live.get("mcpServers", {})
    man = load_manifest()
    gen = {}
    for n, s in man.items():
        if "claude" not in s["targets"]:
            continue
        d = r_claude(n, s)   # header http gia' come ${VAR}: niente token letterale in .claude.json
        gen[n] = d
    gen = keep_extras(gen, live_mcp, "claude")
    new_mcp = reorder(gen, live_mcp)
    return write_json_section(path, "mcpServers", new_mcp, live_mcp, s_standard, indent_exact="  ")

def cmd_write(cli):
    if cli == "opencode":
        return write_opencode()
    if cli == "antigravity":
        return write_antigravity()
    if cli == "codex":
        return write_codex()
    if cli == "claude":
        return write_claude()
    print(f"--write per '{cli}' non ancora implementato.")
    return 1

def main():
    ap = argparse.ArgumentParser(description="Generatore MCP da manifest unico (Vault 2.0 Fase 1).")
    ap.add_argument("--write", metavar="CLI", choices=list(CLI), help="rigenera la config MCP di una CLI (default: solo diff).")
    args = ap.parse_args()
    if args.write:
        return cmd_write(args.write)
    cmd_diff()
    return 0

if __name__ == "__main__":
    sys.exit(main())
