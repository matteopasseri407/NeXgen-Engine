"""Standalone OCR path gates and bounded reads, without an HTTP request."""
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def ocr():
    target = Path(__file__).resolve().parents[2] / "deploy/ocr/mcp/vault_ocr_mcp.py"
    spec = importlib.util.spec_from_file_location("ocr_test", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ordinary_image_names_are_not_credentials(ocr, tmp_path):
    image = tmp_path / "author.png"
    image.write_bytes(b"synthetic")
    assert ocr.confined_image(str(image)) == image


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_parent_alias_cannot_hide_excluded_directory(ocr, tmp_path):
    excluded = tmp_path / "99-SECRETS"
    excluded.mkdir()
    image = excluded / "image.png"
    image.write_bytes(b"synthetic")
    alias = tmp_path / "images"
    alias.symlink_to(excluded, target_is_directory=True)
    with pytest.raises(ValueError, match="sensitive"):
        ocr.confined_image(str(alias / "image.png"))


def test_growing_image_never_reads_more_than_limit_plus_one(ocr, tmp_path, monkeypatch):
    from types import SimpleNamespace
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(ocr, "MAX_LOCAL_BYTES", 4)
    monkeypatch.setattr(Path, "stat", lambda self: SimpleNamespace(st_size=1))
    with pytest.raises(ValueError, match="grew"):
        ocr.read_local_image(image)


@pytest.mark.parametrize("name", ["auth.png", "token-backup.png", "credentials.png"])
def test_known_sensitive_image_names_are_refused(ocr, tmp_path, name):
    image = tmp_path / name
    image.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="sensitive"):
        ocr.confined_image(str(image))
