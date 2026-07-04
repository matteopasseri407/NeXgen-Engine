#!/usr/bin/env python3
"""Sincronizzatore SKILL — agent-layer (specchio di mcp/render.py).

Legge skills.manifest.yaml e fa sì che, su QUESTA macchina, l'hub
~/.agents/skills e i runtime (Claude, Codex) contengano esattamente le skill
scelte nel manifest. Un solo script per Fedora e Windows.

  - default (--diff): READ-ONLY. Mostra cosa farebbe, NON tocca nulla.
  - --apply:          esegue le azioni (crea/ripara link, segnala installazioni
                      mancanti). Idempotente: se è già allineato non fa niente.

Modello dei byte (come da manifest):
  - origin vault  -> l'hub punta (symlink, o copia su Windows) alla cartella
                     vendorizzata nel vault. Git ha già portato i byte ovunque.
  - origin github -> third-party non vendorizzata: i byte si reinstallano da
                     upstream con `skills add <repo>` (npx). Se manca, --apply
                     prova a installarla; se manca node/npx, lo segnala.

Runtime:
  - Codex: symlink (o copia su Windows) per-skill in ~/.codex/skills/<name>.
  - Claude: ~/.claude/skills di norma è un symlink dell'INTERA cartella verso
            l'hub, quindi vede tutto in automatico; lo script lo verifica. Se
            invece è una cartella reale (modello a copie, tipico Windows),
            rispecchia la singola skill.

NON è autoritativo per la cancellazione: non rimuove skill assenti dal manifest.
"""
from __future__ import annotations
import argparse, platform, shutil, subprocess, sys, tempfile
from pathlib import Path
import yaml

# Console Windows in cp1252: i glifi unicode (checkmark) crasherebbero la print.
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = Path.home()
HERE = Path(__file__).resolve().parent
VAULT = HERE.parent.parent                      # 03-INFRA/scripts -> vault root
UL = VAULT / "03-INFRA" / "agent-universal-layer"
MANIFEST = UL / "skills" / "skills.manifest.yaml"

HUB = HOME / ".agents" / "skills"
RUNTIME = {
    "claude": HOME / ".claude" / "skills",
    "codex": HOME / ".codex" / "skills",
}
IS_WINDOWS = platform.system() == "Windows"

PASS = WARN = ACT = FAILN = 0


def ok(m):   global PASS; PASS += 1; print(f"  \033[32m✓\033[0m {m}")
def warn(m): global WARN; WARN += 1; print(f"  \033[33m⚠\033[0m {m}")
def act(m):  global ACT;  ACT += 1;  print(f"  \033[36m+\033[0m {m}")
def fail(m): global FAILN; FAILN += 1; print(f"  \033[31m✗\033[0m {m}")
def sec(m):  print(f"\n\033[1m{m}\033[0m")


def resolves_to(link: Path, target: Path) -> bool:
    """True se `link` è un symlink che risolve a `target`."""
    try:
        return link.is_symlink() and link.resolve() == target.resolve()
    except OSError:
        return False


def ensure_link(src: Path, dst: Path, apply: bool, label: str) -> None:
    """Fa in modo che `dst` punti a / rispecchi `src`. Non distrugge cartelle
    reali inattese: in quel caso segnala e si ferma (no clobber)."""
    if resolves_to(dst, src):
        ok(f"{label}: già allineato")
        return
    if dst.exists() and not dst.is_symlink():
        # cartella reale: su Windows (modello a copie) è accettabile se ha il
        # contenuto; non la cancelliamo mai.
        if (dst / "SKILL.md").exists():
            ok(f"{label}: presente come copia reale (lascio com'è)")
        else:
            warn(f"{label}: esiste come cartella reale senza SKILL.md, non tocco (controlla a mano)")
        return
    # qui dst manca o è un symlink rotto/sbagliato: lo (ri)creiamo.
    if not apply:
        act(f"{label}: creerei link -> {src}")
        return
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=True)
        act(f"{label}: symlink creato -> {src}")
    except OSError:
        # Windows senza privilegio symlink: fallback a copia.
        shutil.copytree(src, dst)
        act(f"{label}: copiato (symlink non disponibile) <- {src}")


def load_excludes(cli: str) -> set:
    """Skill escluse dal precarico per un runtime (lazy: restano nell'hub,
    lette on-demand). Stessa fonte usata da agent-sync §4: i due provisioner
    DEVONO leggere la stessa lista, o si riparano/rompono a vicenda."""
    f = UL / f"skills-exclude-{cli}.txt"
    if not f.exists():
        return set()
    return {ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def ensure_absent_link(dst: Path, apply: bool, label: str) -> None:
    """La skill è esclusa dal runtime: il link per-skill NON deve esserci.
    Rimuove solo symlink; una cartella reale non è nostra e non si tocca."""
    if dst.is_symlink():
        if apply:
            dst.unlink()
            act(f"{label}: link rimosso (esclusa dal precarico, lazy nell'hub)")
        else:
            act(f"{label}: rimuoverei il link (esclusa, lazy)")
    elif dst.exists():
        warn(f"{label}: esclusa ma esiste come cartella reale, non tocco (controlla a mano)")
    else:
        ok(f"{label}: esclusa (lazy, on-demand dall'hub)")


def install_github(name: str, spec: dict, apply: bool) -> bool:
    """Skill third-party assente dall'hub: la reinstalla da upstream con un
    `git clone` controllato (niente npx: collide col symlink dell'intera
    cartella di Claude). `path` nel manifest = sottocartella col SKILL.md
    (default: radice del repo). Ritorna True se al termine è presente."""
    repo = spec.get("repo", "")
    sub = spec.get("path", ".")
    dst = HUB / name
    # difensiva: nell'hub una skill github dev'essere una cartella reale (copia).
    # se qui trovo un symlink (self-loop, rotto o residuo), non è mai uno stato
    # valido e manderebbe il `.exists()` qui sotto in ELOOP, bloccando il sync.
    # lo rimuovo subito così il sync si auto-ripara invece di piantarsi.
    if dst.is_symlink():
        if not apply:
            act(f"hub/{name}: symlink anomalo nell'hub (self-loop/rotto), in --apply lo rimuoverei e reinstallerei da {repo}")
            return False
        warn(f"hub/{name}: symlink anomalo nell'hub, lo rimuovo e reinstallo da {repo}")
        dst.unlink()
    if (dst / "SKILL.md").exists():
        ok(f"hub/{name}: presente (third-party {repo})")
        return True
    if not apply:
        extra = f" [{sub}]" if sub != "." else ""
        act(f"hub/{name}: MANCANTE, installerei da {repo}{extra}  (git clone)")
        return False
    if shutil.which("git") is None:
        fail(f"hub/{name}: manca e git non c'è. Copia a mano la skill da https://github.com/{repo}")
        return False
    # dst inesistente o rotta/vuota (qui non ha SKILL.md): ripuliamo prima.
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        shutil.rmtree(dst)
    with tempfile.TemporaryDirectory() as tmp:
        url = f"https://github.com/{repo}.git"
        print(f"    … git clone --depth 1 {url}")
        repo_dir = Path(tmp) / "repo"
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"hub/{name}: clone fallito. {r.stderr.strip()[:200]}")
            return False
        src = repo_dir / sub
        if not (src / "SKILL.md").exists():
            fail(f"hub/{name}: SKILL.md non trovato in '{sub}' del repo {repo}")
            return False
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", ".claude-plugin"))
        (dst / ".source").write_text(
            f"source: https://github.com/{repo}\nupstream: {repo}\npath: {sub}\n"
            f"model: vendored-as-is (non modificata)\n", encoding="utf-8")
        act(f"hub/{name}: installata da {repo}")
        return True


def write_index(apply: bool) -> None:
    """Genera ~/.agents/skills/INDEX.md: catalogo one-line-per-skill (nome +
    descrizione dal frontmatter di SKILL.md). È il lazy-loading UNIVERSALE:
    ogni CLI/modello (anche senza formato skill: Antigravity, OpenCode, worker
    locale) legge il catalogo e apre la SKILL.md giusta solo quando il task la
    richiede. Idempotente: riscrive solo se il contenuto cambia."""
    rows = []
    for d in sorted(HUB.iterdir()):
        md = d / "SKILL.md"
        try:
            if not md.is_file():
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # symlink rotto/self-loop: non deve uccidere l'indice
        desc = ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                try:
                    fm = yaml.safe_load(text[3:end]) or {}
                    desc = " ".join(str(fm.get("description") or "").split())
                except yaml.YAMLError:
                    pass
        if len(desc) > 240:
            desc = desc[:237].rstrip() + "..."
        rows.append(f"- **{d.name}**: {desc or '(senza descrizione)'}")
    body = (
        "# Skill catalog (GENERATO da skills-sync.py --index, non editare)\n\n"
        "Catalogo per TUTTI gli agenti e le CLI, lazy by design.\n"
        "Uso: quando il task matcha una voce, leggi `~/.agents/skills/<skill>/SKILL.md` e seguila.\n"
        "Non precaricare mai l'intero set.\n\n"
        + "\n".join(rows) + "\n")
    dst = HUB / "INDEX.md"
    old = dst.read_text(encoding="utf-8") if dst.exists() else ""
    if old == body:
        ok(f"INDEX.md: già aggiornato ({len(rows)} skill)")
        return
    if not apply:
        act(f"INDEX.md: rigenererei il catalogo ({len(rows)} skill)")
        return
    dst.write_text(body, encoding="utf-8")
    act(f"INDEX.md: catalogo rigenerato ({len(rows)} skill)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sincronizza le skill dell'agent-layer dal manifest.")
    ap.add_argument("--apply", action="store_true", help="esegue le azioni (default: solo diff read-only)")
    ap.add_argument("--index", action="store_true", help="rigenera SOLO il catalogo INDEX.md ed esce")
    args = ap.parse_args()
    apply = args.apply

    if args.index:
        print(f"\033[1m=== skills-sync [INDEX] · {platform.system()} ===\033[0m")
        HUB.mkdir(parents=True, exist_ok=True)
        write_index(apply=True)
        return 1 if FAILN else 0

    if not MANIFEST.exists():
        print(f"manifest non trovato: {MANIFEST}", file=sys.stderr)
        return 2
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    skills = data.get("skills") or {}

    mode = "APPLY" if apply else "DIFF (read-only)"
    print(f"\033[1m=== skills-sync [{mode}] · {platform.system()} ===\033[0m")
    HUB.mkdir(parents=True, exist_ok=True)

    # stato del runtime Claude: cartella-symlink verso l'hub (vede tutto)?
    claude_is_hub_link = resolves_to(RUNTIME["claude"], HUB)
    excludes = {cli: load_excludes(cli) for cli in RUNTIME}

    for name, spec in skills.items():
        sec(f"skill: {name}")
        origin = spec.get("origin")
        targets = spec.get("targets", [])

        # 1) materializza nell'hub
        if origin == "vault":
            ensure_link(UL / "skills" / name, HUB / name, apply, f"hub/{name}")
            present = (HUB / name / "SKILL.md").exists() or resolves_to(HUB / name, UL / "skills" / name)
        elif origin == "github":
            present = install_github(name, spec, apply)
        else:
            fail(f"origin sconosciuto '{origin}' per {name}")
            continue

        # 2) aggancia i runtime (rispettando le exclude-list: lazy > precarico)
        for t in targets:
            if t in RUNTIME and name in excludes[t]:
                if t == "claude" and claude_is_hub_link:
                    warn(f"claude/{name}: esclusione IMPOSSIBILE finché ~/.claude/skills è un symlink all'hub")
                else:
                    ensure_absent_link(RUNTIME[t] / name, apply, f"{t}/{name}")
                continue
            if t == "claude":
                if claude_is_hub_link:
                    ok("claude: coperta (symlink dell'intera cartella verso l'hub)")
                else:
                    ensure_link(HUB / name, RUNTIME["claude"] / name, apply, f"claude/{name}")
            elif t == "codex":
                ensure_link(HUB / name, RUNTIME["codex"] / name, apply, f"codex/{name}")
            else:
                warn(f"target sconosciuto '{t}'")

    sec("catalogo universale")
    write_index(apply)

    print(f"\n\033[1mTotale:\033[0m {PASS} ok · {ACT} azioni · {WARN} warn · {FAILN} fail")
    if not apply and ACT:
        print("  (esegui di nuovo con --apply per applicarle)")
    return 1 if FAILN else 0


if __name__ == "__main__":
    sys.exit(main())
