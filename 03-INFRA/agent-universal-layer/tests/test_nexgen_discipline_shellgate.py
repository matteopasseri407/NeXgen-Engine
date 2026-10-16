from discipline_repo import repo  # noqa: F401  (pytest fixture)
from nexgen_core.discipline import gates
from nexgen_core.discipline.config import Config
from nexgen_core.discipline.gitutil import changes

THIN = "#!/bin/sh\nexec python3 -m tool \"$@\"\n"
HACK = "#!/bin/sh\n" + "".join(f"if [ -n \"$A{i}\" ]; then echo x | sed s/a/b/; fi\n" for i in range(30))


def run(repo, base):
    return gates.static_checks(changes(repo.path, base), Config())


def test_thin_launcher_is_fine(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("run.sh", THIN)
    assert run(repo, base) == []


def test_logic_in_shell_fails_without_reason(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("hack.sh", HACK)
    assert any("thin launcher" in str(v) for v in run(repo, base))


def test_powershell_is_covered_too(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("hack.ps1", "".join("if ($x) { $y | ConvertFrom-Json }\n" for _ in range(12)))
    assert run(repo, base)


def test_reason_marker_allows_it_within_the_hard_cap(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("ok.sh", '# nexgen: allow reason="bootstraps python itself"\n' + HACK[:1500])
    assert run(repo, base) == []
    repo.write("huge.sh", '# nexgen: allow reason="because"\n' + HACK * 6)
    assert any("hard cap" in str(v) for v in run(repo, base))


def test_marker_without_a_real_reason_does_not_count(repo):
    base = repo.git("rev-parse", "HEAD")
    repo.write("hack.sh", "# nexgen: allow\n" + HACK)
    assert run(repo, base)
