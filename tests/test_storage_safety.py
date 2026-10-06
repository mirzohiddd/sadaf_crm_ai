"""JSON storage: buzuq fayl jimgina [] bilan almashtirilmasligi."""
from __future__ import annotations

import pytest

from app import config, storage


def test_corrupt_file_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    path = tmp_path / "leads.json"
    path.write_text('[{"id": 1, "name": "Ali"}', encoding="utf-8")  # yarim yozilgan JSON
    assert storage.read("leads") == []                                # API yiqilmaydi
    with pytest.raises(storage.StorageCorruptError):
        storage.insert("leads", {"name": "Yangi"})
    assert path.read_text(encoding="utf-8") == '[{"id": 1, "name": "Ali"}'  # ma'lumot saqlandi
    assert list(tmp_path.glob("leads.json.corrupt-*"))                 # nusxa olindi


def test_storage_id_is_stable_and_resets_with_new_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    first = storage.storage_id()
    assert first == storage.storage_id()
    (tmp_path / ".storage_id").unlink()          # papka "tozalandi"
    assert storage.storage_id() != first
    assert config.DATA_DIR  # mavjud sozlama o'zgarmagan
