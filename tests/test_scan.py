import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.scan import pick_exe, scan_card


def _write(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_pick_exe_prefers_biggest_and_skips_installers(tmp_path):
    _write(tmp_path / "unins000.exe", 900)
    _write(tmp_path / "bin" / "vcredist_x64.exe", 800)
    _write(tmp_path / "bin" / "CrashHandler.exe", 700)
    _write(tmp_path / "bin" / "Game-Win64-Shipping.exe", 500)
    _write(tmp_path / "launcher.exe", 100)
    assert pick_exe(str(tmp_path)).endswith("Game-Win64-Shipping.exe")


def test_pick_exe_case_insensitive_extension(tmp_path):
    _write(tmp_path / "Game.EXE", 10)
    assert pick_exe(str(tmp_path)).endswith("Game.EXE")


def test_pick_exe_returns_none_when_nothing_usable(tmp_path):
    _write(tmp_path / "setup.exe", 10)
    (tmp_path / "data").mkdir()
    assert pick_exe(str(tmp_path)) is None


def test_pick_exe_searches_three_levels_deep(tmp_path):
    _write(tmp_path / "a" / "b" / "c" / "Deep.exe", 50)
    assert pick_exe(str(tmp_path)).endswith("Deep.exe")


def test_scan_card_reports_games_and_steam_appid_file(tmp_path):
    games = tmp_path / "Games"
    _write(games / "ANIMAL WELL" / "Animal Well.exe", 500)
    (games / "ANIMAL WELL" / "steam_appid.txt").write_text("813230")
    _write(games / "Hades" / "Hades.exe", 400)
    (games / "PustyFolder").mkdir()

    card = {"label": "SD256", "mount": str(tmp_path), "games_dir": str(games)}
    result = scan_card(card)

    assert [r["folder"] for r in result] == ["ANIMAL WELL", "Hades"]
    animal = result[0]
    assert animal["exe_rel"] == os.path.join("Games", "ANIMAL WELL", "Animal Well.exe")
    assert animal["steam_appid_file"].endswith("steam_appid.txt")
    assert result[1]["steam_appid_file"] is None


def test_nie_wybiera_instalatora_z_katalogu_redist(tmp_path):
    """ZGŁOSZONE i ZMIERZONE na karcie: dla „Invincible VS" automat wybrał
    `_Redist/oalinst.exe` — instalator OpenAL, który wygrał ROZMIAREM, bo filtr
    patrzył tylko na nazwę pliku. Prawdziwym sygnałem jest KATALOG: wydania trzymają
    biblioteki towarzyszące w `_Redist`, `redist`, `CommonRedist` itd.

    Lista plików niżej to dokładnie ta z karty użytkownika."""
    gra = tmp_path / "Invincible VS - SteamGG.NET" / "Invincible VS - SteamGG.NET"
    pliki = {
        "TagFighter/Binaries/Win64/InvincibleVS-Win64-Shipping.exe": 120_000_000,
        "TagFighter/Plugins/Sentry/Binaries/Win64/crashpad_handler.exe": 2_000_000,
        "InvincibleVS.exe": 500_000,
        "_Redist/dotNetFx40_Full_setup.exe": 900_000,
        "_Redist/oalinst.exe": 140_000_000,
        # NAZWA tego pliku nie jest na żadnej czarnej liście — jedyne, co go
        # odrzuca, to katalog. Bez tego warunku wygrałby rozmiarem.
        "_Redist/audio.exe": 150_000_000,
        "_Redist/dxwebsetup.exe": 300_000,
    }
    for wzgledna, rozmiar in pliki.items():
        p = gra / wzgledna
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * min(rozmiar, 4096))
        os.truncate(p, rozmiar)

    wybrany = pick_exe(str(gra))
    assert wybrany is not None
    assert "_Redist" not in wybrany, "wybrano instalator z katalogu bibliotek"
    assert wybrany.endswith("InvincibleVS-Win64-Shipping.exe"), wybrany


def test_katalog_z_bibliotekami_nie_blokuje_gry_bez_innych_plikow(tmp_path):
    """Gdyby gra miała WYŁĄCZNIE pliki w takim katalogu, lepiej wskazać cokolwiek
    niż nie dodać gry — użytkownik poprawi wybór przyciskiem."""
    gra = tmp_path / "Gra"
    (gra / "_Redist").mkdir(parents=True)
    (gra / "_Redist" / "cokolwiek.exe").write_bytes(b"\0" * 1000)
    assert pick_exe(str(gra)) is None, "instalator nie udaje gry"
