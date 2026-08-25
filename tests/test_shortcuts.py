"""Odczyt shortcuts.vdf i dopasowanie ISTNIEJĄCEGO kafelka do gry z karty.

Po co: użytkownik ma na Decku kafelki dodane ręcznie, zanim ta wtyczka istniała,
i w ICH prefiksach siedzi jego postęp. Skan karty, który zrobi drugi kafelek,
zakłada nowy prefiks — postęp zostaje na boku i nikt go już nie wozi.

Format binarnego VDF sprawdzony na Decku: parser przeczytał żywy plik z 32
skrótami, z appid i pełnymi ścieżkami .exe (patrz test niżej na próbce zbudowanej
dokładnie tak samo).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync import shortcuts


def _vdf(entries) -> bytes:
    """Buduje binarny VDF tak, jak robi to Steam: \\x00 mapa, \\x01 tekst,
    \\x02 int32 little-endian, \\x08 koniec mapy."""
    out = b"\x00shortcuts\x00"
    for index, (appid, name, exe) in enumerate(entries):
        out += b"\x00" + str(index).encode() + b"\x00"
        out += b"\x02appid\x00" + struct.pack("<I", appid)
        out += b"\x01AppName\x00" + name.encode("utf-8") + b"\x00"
        out += b"\x01Exe\x00" + exe.encode("utf-8") + b"\x00"
        out += b"\x01LaunchOptions\x00\x00"
        out += b"\x08"
    return out + b"\x08\x08"


_WPISY = [
    (2807005576, "Gothic 1 Remake", '"/karta/Games/Gothic/G1R.exe"'),
    (3349231731, "Baba Is You", '"/karta/Games/Baba-is-You/Baba.exe"'),
    (2314379289, "Hydra Launcher", '"/home/deck/Applications/hydra.AppImage"'),
]


def test_parser_czyta_appid_nazwe_i_sciezke(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(_vdf(_WPISY))
    wpisy = shortcuts.read(str(path))
    assert [(w["appid"], w["name"]) for w in wpisy] == [
        (2807005576, "Gothic 1 Remake"),
        (3349231731, "Baba Is You"),
        (2314379289, "Hydra Launcher"),
    ]
    # cudzysłowy Steama zdjęte — inaczej żadna ścieżka nigdy się nie dopasuje
    assert wpisy[0]["exe"] == "/karta/Games/Gothic/G1R.exe"


def test_uszkodzony_plik_nie_wywala_skanu(tmp_path):
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(b"\x00shortcuts\x00\x00" + b"\x02appid\x00\x01\x02")
    assert shortcuts.read(str(path)) == []
    assert shortcuts.read(str(tmp_path / "nie-ma-mnie.vdf")) == []


def test_dopasowanie_po_realpath_a_nie_po_tekscie(tmp_path):
    """ZMIERZONE na Decku: `/run/media/SD256` jest symlinkiem na
    `/run/media/deck/SD256`, więc ta sama gra ma kafelki z DWIEMA różnymi
    ścieżkami tekstowo (007 First Light miał ich trzy). Bez realpath dopasowanie
    ich nie łączy i wtyczka dokłada kolejny kafelek."""
    prawdziwa = tmp_path / "media" / "deck" / "SD256" / "Games" / "Gra"
    prawdziwa.mkdir(parents=True)
    exe = prawdziwa / "gra.exe"
    exe.write_text("", encoding="utf-8")
    link = tmp_path / "media" / "SD256"
    os.symlink(tmp_path / "media" / "deck" / "SD256", link)
    przez_link = str(link / "Games" / "Gra" / "gra.exe")

    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(_vdf([(111, "Gra", '"%s"' % przez_link)]))
    assert shortcuts.find_by_exe(shortcuts.read(str(path)), str(exe)) == [
        {"appid": 111, "name": "Gra"}]


def test_inna_gra_nie_jest_dopasowana(tmp_path):
    """Dopasowanie po nazwie byłoby zgadywaniem — dwie gry mogą się nazywać tak
    samo, a przejęcie CUDZEGO prefiksu znaczy kopiowanie i przywracanie zapisów
    nie tej gry."""
    a = tmp_path / "a.exe"
    b = tmp_path / "b.exe"
    a.write_text("", encoding="utf-8")
    b.write_text("", encoding="utf-8")
    path = tmp_path / "shortcuts.vdf"
    path.write_bytes(_vdf([(111, "Gra", '"%s"' % b)]))
    assert shortcuts.find_by_exe(shortcuts.read(str(path)), str(a)) == []


def test_zbiera_z_wszystkich_userdata_i_nie_dubluje(tmp_path):
    """Steam ma katalog userdata na konto, a po migracjach zostają też martwe.
    Ten sam appid w dwóch plikach to jeden kafelek, nie dwa."""
    exe = tmp_path / "gra.exe"
    exe.write_text("", encoding="utf-8")
    for konto in ("0", "67291926"):
        katalog = tmp_path / "userdata" / konto / "config"
        katalog.mkdir(parents=True)
        (katalog / "shortcuts.vdf").write_bytes(_vdf([(111, "Gra", '"%s"' % exe)]))
    assert shortcuts.matches_for(str(exe), str(tmp_path)) == [
        {"appid": 111, "name": "Gra"}]


def test_brak_steama_nie_jest_bledem(tmp_path):
    assert shortcuts.matches_for(str(tmp_path / "gra.exe"), str(tmp_path)) == []
    assert shortcuts.matches_for("", str(tmp_path)) == []
