import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.steamworks import neutralize, restore, state

BAK = "steam_appid.txt.sdsync-bak"


def test_neutralize_renames_and_reports_appid(tmp_path):
    (tmp_path / "steam_appid.txt").write_text("813230\n")
    result = neutralize(str(tmp_path))
    assert result["changed"] is True
    assert result["appid"] == "813230"
    assert not (tmp_path / "steam_appid.txt").exists()
    assert (tmp_path / BAK).read_text().strip() == "813230"
    assert state(str(tmp_path)) == "neutralized"


def test_neutralize_is_idempotent(tmp_path):
    (tmp_path / "steam_appid.txt").write_text("813230")
    neutralize(str(tmp_path))
    second = neutralize(str(tmp_path))
    assert second["changed"] is False
    assert (tmp_path / BAK).exists()


def test_neutralize_without_file_reports_absent(tmp_path):
    result = neutralize(str(tmp_path))
    assert result["changed"] is False
    assert result["appid"] is None
    assert state(str(tmp_path)) == "absent"


def test_restore_brings_the_file_back(tmp_path):
    (tmp_path / "steam_appid.txt").write_text("813230")
    neutralize(str(tmp_path))
    assert restore(str(tmp_path)) is True
    assert (tmp_path / "steam_appid.txt").read_text() == "813230"
    assert not (tmp_path / BAK).exists()
    assert state(str(tmp_path)) == "active"
    assert restore(str(tmp_path)) is False
