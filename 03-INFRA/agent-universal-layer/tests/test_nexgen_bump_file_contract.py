"""Pin updates obey the shared file writer's preservation contract."""
import os
import stat

import pytest

from nexgen_core.thirdparty_bump import _atomic_write


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows uses inherited ACLs")
def test_pin_update_preserves_restrictive_manifest_permissions(tmp_path):
    target = tmp_path / "manifest.yaml"
    target.write_text("old\n")
    target.chmod(0o600)
    previous = os.umask(0o022)
    try:
        _atomic_write(target, "new\n")
    finally:
        os.umask(previous)
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_pin_update_ignores_a_temp_file_left_by_another_call(tmp_path):
    target = tmp_path / "manifest.yaml"
    target.write_text("old\n")
    foreign = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    foreign.write_text("another writer's bytes\n")
    _atomic_write(target, "new\n")
    assert target.read_text() == "new\n"
    assert foreign.read_text() == "another writer's bytes\n"
