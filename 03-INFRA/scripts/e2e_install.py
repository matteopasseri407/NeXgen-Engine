#!/usr/bin/env python3
"""Install the engine from scratch on a clean Ubuntu, the way a client machine gets it.

One job: build a throwaway Ubuntu 24.04 container with a real systemd, create a
plain account without sudo, enter it with `sudo -iu` as the documented server
install does, and run this checkout's `install.sh` there. Then check what a
person would check: nothing written by `--check`, the engine runtime has pip,
the guard units name an engine root that exists, OpenClaw is the pinned
release, and the doctor reports no failure.

Why it exists: on 2026-10-30 seven defects of the install path (a `--check`
that wrote, sudo rules for the wrong account, an unreachable user bus, a venv
without pip, a unit naming a missing engine root, a config OpenClaw refused,
agents without connectors) were found only by installing on the live server.
Every one of them is visible here, on a machine nobody has touched.

Usage: python3 03-INFRA/scripts/e2e_install.py [--keep]
Needs Docker able to run a privileged container (systemd as PID 1).
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIN = REPO / "03-INFRA" / "agent-universal-layer" / "openclaw" / "pin.json"
IMAGE = "nexgen-e2e-install:24.04"
ACCOUNT = "nxclient"
#: Where the README tells a person to clone: the clone is the vault.
CHECKOUT = f"/home/{ACCOUNT}/KnowledgeVault"
NODE_VERSION = "24.22.1"

#: The admin's part, done once before the client account exists: systemd, sudo,
#: Python with its venv package, git, and an official Node in /usr/local.
DOCKERFILE = f"""\
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
      systemd systemd-sysv dbus dbus-user-session sudo ca-certificates curl xz-utils \\
      git python3 python3-venv python3-pip \\
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-linux-x64.tar.xz \\
      | tar -xJ -C /usr/local --strip-components=1
RUN useradd -m -s /bin/bash {ACCOUNT}
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
"""


def run(argv: list[str], *, check: bool = True, timeout: int = 1800, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """One host command, echoed so the CI log reads as the steps a person would take."""
    print("$ " + shlex.join(argv), flush=True)
    return subprocess.run(argv, check=check, timeout=timeout, text=True, encoding="utf-8", errors="replace",
                          capture_output=capture)


def as_client(container: str, script: str, *, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    """A login shell of the client account, entered the documented way: `sudo -iu`."""
    return run(["docker", "exec", container, "sudo", "-iu", ACCOUNT, "bash", "-lc", script],
               check=check, timeout=timeout, capture=True)


def start(keep: bool) -> str:
    with tempfile.TemporaryDirectory() as context:
        (Path(context) / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
        run(["docker", "build", "-q", "-t", IMAGE, context])
    container = run(
        ["docker", "run", "-d", "--privileged", "--cgroupns=host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
         *([] if keep else ["--rm"]), IMAGE],
        capture=True,
    ).stdout.strip()
    for _ in range(60):
        state = run(["docker", "exec", container, "systemctl", "is-system-running"], check=False, capture=True)
        if state.stdout.strip() in {"running", "degraded"}:
            break
        time.sleep(1)
    run(["docker", "exec", container, "loginctl", "enable-linger", ACCOUNT])
    copy_checkout(container)
    run(["docker", "exec", container, "chown", "-R", f"{ACCOUNT}:{ACCOUNT}", CHECKOUT])
    # A client clones `main`: whatever this checkout's branch is, it lands there as `main`.
    as_client(container, "cd ~/KnowledgeVault && git checkout -q -B main")
    return container


def copy_checkout(container: str) -> None:
    """What a clone holds and nothing else: tracked and new files, plus `.git`.

    A plain copy of the folder carried a local `.venv` and every ignored file,
    which no client machine ever has. `.git` goes along because the install
    commits its first files into the vault.
    """
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard", "-z"],
                            check=True, capture_output=True).stdout.split(b"\0")
    names = [name for name in listed if name and (REPO / name.decode()).exists()] + [b".git"]
    target = CHECKOUT
    run(["docker", "exec", container, "mkdir", "-p", target])
    tar = subprocess.Popen(["tar", "-C", str(REPO), "--null", "-T", "-", "-cf", "-"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    unpack = subprocess.Popen(["docker", "exec", "-i", container, "tar", "-xf", "-", "-C", target], stdin=tar.stdout)
    assert tar.stdin is not None and tar.stdout is not None
    tar.stdout.close()
    tar.stdin.write(b"\0".join(names))
    tar.stdin.close()
    if unpack.wait(timeout=600) != 0 or tar.wait(timeout=600) != 0:
        raise RuntimeError("copying the checkout into the container failed")


def home_listing(container: str) -> str:
    """Every path under the client's home except the checkout itself."""
    return as_client(container, "find ~ -path ~/KnowledgeVault -prune -o -print | sort").stdout


def check_writes_nothing(container: str) -> list[str]:
    before = home_listing(container)
    result = as_client(container, "cd ~/KnowledgeVault && sh install.sh --role server --check", check=False)
    after = home_listing(container)
    problems = []
    if result.returncode != 0:
        problems.append(f"--role server --check exited {result.returncode}: {result.stdout[-400:]}{result.stderr[-400:]}")
    if before != after:
        added = sorted(set(after.splitlines()) - set(before.splitlines()))
        problems.append("--role server --check wrote: " + ", ".join(added[:10]))
    return problems


def check_standalone(container: str) -> list[str]:
    problems = []
    result = as_client(container, "cd ~/KnowledgeVault && sh install.sh --role standalone", check=False, timeout=2400)
    print(result.stdout[-3000:], result.stderr[-2000:], sep="\n")
    if result.returncode != 0:
        problems.append(f"--role standalone exited {result.returncode}")
    pip = as_client(container, "~/.local/share/nexgen-engine/runtime/bin/python -m pip --version", check=False)
    if pip.returncode != 0:
        problems.append("the engine runtime has no pip")
    roots = as_client(container, "grep -ho 'AGENT_ENGINE_ROOT=[^\"]*' ~/.config/systemd/user/agent-*.service", check=False)
    for line in roots.stdout.split():
        path = line.split("=", 1)[1]
        if as_client(container, f"test -d {shlex.quote(path)}", check=False).returncode != 0:
            problems.append(f"a unit names an engine root that does not exist: {path}")
    if not roots.stdout.strip():
        problems.append("no guard unit declares AGENT_ENGINE_ROOT")
    pinned = json.loads(PIN.read_text(encoding="utf-8"))["version"]
    version = as_client(container, "openclaw --version", check=False).stdout
    if pinned not in version:
        problems.append(f"OpenClaw is not the pinned {pinned}: {version.strip()}")
    doctor = as_client(container, "nexgen openclaw doctor", check=False)
    print(doctor.stdout[-2500:])
    failures = [line for line in doctor.stdout.splitlines() if line.startswith("FAIL")]
    if failures:
        problems.append("doctor: " + " | ".join(failures))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="leave the container running for inspection")
    args = parser.parse_args()
    container = start(args.keep)
    try:
        problems = check_writes_nothing(container) + check_standalone(container)
    finally:
        if not args.keep:
            run(["docker", "stop", container], check=False)
        else:
            print(f"container kept: {container}")
    for problem in problems:
        print("PROBLEM: " + problem)
    print("OK: a clean machine installs and passes the doctor" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
