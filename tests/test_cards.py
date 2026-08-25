import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.cards import find_cards


def test_finds_card_with_games_dir(tmp_path):
    card = tmp_path / "run" / "media" / "deck" / "SD256"
    (card / "Games" / "Hades").mkdir(parents=True)
    found = find_cards([str(tmp_path / "run" / "media")])
    assert len(found) == 1
    assert found[0]["label"] == "SD256"
    assert found[0]["games_dir"] == str(card / "Games")


def test_finds_card_mounted_without_user_dir(tmp_path):
    card = tmp_path / "media" / "SD256"
    (card / "Games").mkdir(parents=True)
    found = find_cards([str(tmp_path / "media")])
    assert [c["label"] for c in found] == ["SD256"]


def test_ignores_media_without_games_dir(tmp_path):
    (tmp_path / "media" / "USBSTICK" / "dokumenty").mkdir(parents=True)
    assert find_cards([str(tmp_path / "media")]) == []


def test_finds_multiple_cards_sorted(tmp_path):
    for label in ("SD512", "SD256"):
        (tmp_path / "media" / "deck" / label / "Games").mkdir(parents=True)
    found = find_cards([str(tmp_path / "media")])
    assert [c["label"] for c in found] == ["SD256", "SD512"]


def test_symlinked_card_is_reported_once(tmp_path):
    """W SteamOS /run/media/SD256 jest symlinkiem na /run/media/deck/SD256 i oba wzorce
    globa łapią tę samą kartę. Podwójny wpis = każda gra proponowana dwa razy =
    podwójne kafelki w bibliotece, czyli problem, od którego zaczął się projekt."""
    media = tmp_path / "run" / "media"
    real = media / "deck" / "SD256"
    (real / "Games" / "Hades").mkdir(parents=True)
    (media / "SD256").symlink_to(real)
    found = find_cards([str(media)])
    assert len(found) == 1
    assert found[0]["mount"] == str(real)  # zostaje punkt montowania, nie symlink
    assert found[0]["games_dir"] == str(real / "Games")


def test_two_different_cards_are_not_merged_by_dedup(tmp_path):
    media = tmp_path / "media"
    for label in ("SD256", "SD512"):
        (media / "deck" / label / "Games").mkdir(parents=True)
    (media / "SD256").symlink_to(media / "deck" / "SD256")
    found = find_cards([str(media)])
    assert [c["label"] for c in found] == ["SD256", "SD512"]
    assert [c["mount"] for c in found] == [
        str(media / "deck" / "SD256"), str(media / "deck" / "SD512")]
