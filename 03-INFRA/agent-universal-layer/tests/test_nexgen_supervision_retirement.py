"""Il permesso di portare le regole di disciplina, e cosa i commit dell'unità hanno ritirato.

Il permesso è del supervisore e vive nel confine scritto alla creazione; il
giudizio è commit per commit, con la configurazione che ogni commit eredita dal
genitore. Qui si prova il modulo da solo; il collegamento ai cancelli passa da
`test_nexgen_supervision_gates.py`.
"""
from __future__ import annotations

from nexgen_core.discipline import config
from nexgen_local import supervision_retirement as own_rules

from supervision_repo import build


def test_the_boundary_that_names_the_configuration_lets_the_unit_carry_it():
    """Solo un confine che nomina `.nexgen-discipline.toml` dà all'unità le sue autorizzazioni."""
    assert own_rules.may_carry(["**"])
    assert own_rules.may_carry([".nexgen-discipline.toml"])
    assert not own_rules.may_carry(["docs/*.md", "tests/*.py"])


def test_the_commits_are_judged_with_the_rules_of_their_parent(tmp_path):
    """Autorizzare in un commit e ritirare nel successivo passa, e i casi ritirati si contano."""
    repo = build(tmp_path / "sandbox")
    base = repo.head()
    repo.write(".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    repo.commit("chore: autorizza il ritiro del campione")
    (repo.path / "tests" / "test_sample.py").unlink()
    head = repo.commit("test: ritira il campione")

    problems, retired = own_rules.integrity_by_commit(repo.path, base, head)

    assert problems == []
    assert retired == {"tests/test_sample.py": 2}


def test_a_commit_that_authorises_and_retires_at_once_is_a_failure(tmp_path):
    """Un commit solo che scrive l'autorizzazione e toglie il test non se la concede: il guasto lo nomina."""
    repo = build(tmp_path / "sandbox")
    base = repo.head()
    repo.write(".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    (repo.path / "tests" / "test_sample.py").unlink()
    head = repo.commit("test: autorizza e ritira insieme")

    problems, retired = own_rules.integrity_by_commit(repo.path, base, head)

    assert problems, "il commit si autorizza da solo, quindi il cancello deve dire di no"
    assert head[:8] in problems[0]
    assert retired == {}


def test_the_entries_the_unit_added_are_what_the_package_lists(tmp_path):
    """Il pacchetto elenca le voci aggiunte dall'unità, cioè la differenza con la configurazione della base."""
    repo = build(tmp_path / "sandbox")
    base = repo.head()
    repo.write(".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    head = repo.commit("chore: autorizza il ritiro del campione")
    base_rules = config.load(repo.path, base)

    entries, problem = own_rules.added_entries(repo.path, head, base_rules)

    assert problem == ""
    assert entries == ["tests/test_sample.py"]
    assert own_rules.added_entries(repo.path, base, base_rules) == ([], "")
