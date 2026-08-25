import json
import os
import re
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.registry import Registry, title_key


def test_title_key_normalizes():
    assert title_key("The Witcher 3: Wild Hunt") == "the-witcher-3-wild-hunt"
    assert title_key("ANIMAL WELL") == "animal-well"
    assert title_key("Assassin's Creed  IV") == "assassins-creed-iv"


def test_upsert_creates_and_updates(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    rec = reg.upsert({"title": "Animal Well", "folder": "ANIMAL WELL", "exe_rel": "Animal Well.exe"})

    assert rec["title_key"] == "animal-well"
    assert rec["appid"] is None
    assert rec["excluded"] is False
    # rejestr nie trzyma pól, których nikt nie czyta: ukrycia pamięta Steam
    # (collectionStore), a karty rozróżniamy po etykiecie
    assert "hidden_appids" not in rec
    assert "card_uuid" not in rec

    reg.upsert({"title": "Animal Well", "appid": 3208618198})
    again = reg.get("animal-well")
    assert again["appid"] == 3208618198
    assert again["folder"] == "ANIMAL WELL", "upsert nie może gubić istniejących pól"


def test_persists_between_instances(tmp_path):
    path = str(tmp_path / "games.json")
    Registry(path).upsert({"title": "Hades"})
    assert Registry(path).get("hades")["title"] == "Hades"


def test_remove_and_set_fields(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Hades"})
    reg.set_fields("hades", excluded=True, last_push_ts=123)
    assert reg.get("hades")["excluded"] is True
    assert reg.get("hades")["last_push_ts"] == 123
    assert reg.remove("hades") is True
    assert reg.remove("hades") is False
    assert reg.all() == []


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "games.json"
    path.write_text("{ to nie jest json")
    reg = Registry(str(path))
    assert reg.all() == []
    reg.upsert({"title": "Hades"})
    assert json.loads(path.read_text())["hades"]["title"] == "Hades"
    # uszkodzonego rejestru nie wolno po cichu skasować — odkładamy go obok do odzysku
    broken = path.with_name(path.name + ".broken")
    assert broken.read_text() == "{ to nie jest json"


def test_title_key_keeps_diacritics_as_latin():
    assert title_key("Wiedźmin 3") == "wiedzmin-3"
    assert title_key("Pokémon Snap") == "pokemon-snap"
    assert title_key("Miłość") == "milosc"


def test_title_key_non_latin_title_is_stable_and_not_empty():
    key = title_key("原神")
    assert key, "tytuł nie-łaciński nie może dać pustego klucza"
    assert key == title_key("原神 "), "klucz musi być stabilny"
    assert key != title_key("崩壞"), "różne tytuły — różne klucze"
    assert re.fullmatch(r"[a-z0-9-]+", key)


def test_title_key_empty_title_gives_empty_key():
    assert title_key("") == ""
    assert title_key("   ") == ""


def test_upsert_non_latin_title_does_not_raise(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    rec = reg.upsert({"title": "原神"})
    assert reg.get(rec["title_key"])["title"] == "原神"


def test_upsert_rejects_empty_title(tmp_path):
    path = tmp_path / "games.json"
    reg = Registry(str(path))
    for bad in ({"title": ""}, {"title": "   "}, {"title_key": "hades"},
                {"title": "", "title_key": "hades"}):
        with pytest.raises(ValueError):
            reg.upsert(bad)
    assert not path.exists(), "odrzucony rekord nie może trafić na dysk"


def test_upsert_empty_title_does_not_wipe_existing(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Hades"})
    with pytest.raises(ValueError):
        reg.upsert({"title": "", "title_key": "hades"})
    assert reg.get("hades")["title"] == "Hades"


def test_concurrent_writes_never_lose_an_update_or_corrupt_the_file(tmp_path):
    """Regresja: sync_all, push_after_game i add_playtime idą przez asyncio.to_thread,
    czyli przez WIELE wątków. Bez zamka read-modify-write gubi cudzą zmianę, a stała
    nazwa pliku tymczasowego pozwala dwóm piszącym wyprodukować niepoprawny JSON —
    a wtedy _read zwraca {} i najbliższy upsert utrwala pusty rejestr."""
    path = tmp_path / "games.json"
    reg = Registry(str(path))
    reg.upsert({"title": "Hades"})
    problems = []
    seen = {}

    def worker(field, value):
        try:
            for _ in range(200):
                reg.set_fields("hades", **{field: value})
                with open(path, encoding="utf-8") as fh:
                    record = json.load(fh)["hades"]  # plik MUSI być poprawny na każdym etapie
                # Żadne z tych pól nigdy nie jest kasowane, więc raz ustawione musi
                # zostać. Powrót do wartości sprzed = ktoś zapisał starszą kopię CAŁEGO
                # pliku, czyli cudza zmiana (np. flaga konfliktu) przepadła.
                for other, target in (("conflict", True), ("playtime_seconds", 4242)):
                    if record[other] == target:
                        seen[other] = True
                    elif seen.get(other):
                        problems.append("cofnięte pole %s" % other)
                        return
        except Exception as exc:  # noqa: BLE001 - dowolna awaria jest wynikiem testu
            problems.append("%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=worker, args=("conflict", True)),
               threading.Thread(target=worker, args=("playtime_seconds", 4242))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert problems == [], problems
    record = reg.get("hades")
    assert record["conflict"] is True, "zgubiona flaga konfliktu = nadpisany zapis"
    assert record["playtime_seconds"] == 4242, "zgubiony czas gry"


def test_a_second_process_writing_the_registry_cannot_corrupt_it(tmp_path):
    """Zamek działa tylko w obrębie procesu, a plik tymczasowy o STAŁEJ nazwie jest
    wspólny dla wszystkich piszących. Drugi proces (diagnostyka z konsoli obok
    działającej wtyczki) obcina cudzy plik tymczasowy w locie — a ten po os.replace
    jest już games.json. Wynik: niepoprawny JSON, który _read łyka jako pusty rejestr.
    Unikatowa nazwa z tempfile.mkstemp to wyklucza."""
    path = tmp_path / "games.json"
    reg = Registry(str(path))
    # duże pola: zapis nie mieści się w jednym syscallu, więc okno na przeplot jest realne
    reg.upsert({"title": "Hades", "folder": "F" * 40000})

    pid = os.fork()
    if pid == 0:  # potomek pisze ten sam plik, nie widząc zamka rodzica
        try:
            child = Registry(str(path))
            for _ in range(200):
                child.set_fields("hades", card_label="B" * 40000)
        finally:
            os._exit(0)

    problems = []
    try:
        for _ in range(200):
            reg.set_fields("hades", folder="A" * 40000)
            with open(path, encoding="utf-8") as fh:
                json.load(fh)  # plik MUSI być poprawny na każdym etapie
    except Exception as exc:  # noqa: BLE001 - dowolna awaria jest wynikiem testu
        problems.append("%s: %s" % (type(exc).__name__, exc))
    finally:
        os.waitpid(pid, 0)

    assert problems == [], problems


def test_title_is_always_stored_trimmed(tmp_path):
    """_filter przycina tytuly przed wywolaniem Ludusavi, wiec odpowiedz wraca pod
    kluczem PRZYCIETYM. Rekord z przypadkowa spacja dostawalby wieczny konflikt."""
    path = tmp_path / "games.json"
    reg = Registry(str(path))
    reg.upsert({"title": "  Hades  "})
    assert reg.get("hades")["title"] == "Hades"
    # Rejestr zapisany przez starsza wersje trzyma tytul ze spacja, a aktualizacja
    # bez pola "title" (tak wolamy przy nadaniu appid albo znacznika kopii) nigdy go
    # nie prostowala - i taki rekord nie dopasowal sie do odpowiedzi Ludusavi.
    path.write_text(json.dumps({"hades": {"title_key": "hades", "title": "  Hades  "}}),
                    encoding="utf-8")
    reg.upsert({"title_key": "hades", "appid": 4242})
    assert reg.get("hades")["title"] == "Hades"
def test_martwe_pole_check_cloud_before_launch_nie_wraca(tmp_path):
    from sdsync.registry import FIELDS
    assert "check_cloud_before_launch" not in FIELDS, \
        "pole nigdy nie było czytane — nie wskrzeszamy go bez wywołującego"


def test_card_seen_jest_osobnym_slownikiem_dla_kazdej_gry(tmp_path):
    """FIELDS trzyma wartości domyślne, a `_blank` je przepisuje — wspólny słownik
    znaczyłby, że znacznik kopii jednej gry pojawia się przy wszystkich innych.
    Skutek: wtyczka uznałaby, że karta ma kopię gry, której nigdy nie widziała."""
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Hades"})
    reg.upsert({"title": "Animal Well"})
    reg.set_fields("hades", card_seen={"SD256": "2026-08-20T10:00:00Z"})
    assert reg.get("animal-well")["card_seen"] == {}


# ---------- zmiana tytułu = zmiana klucza ----------

def test_rename_moves_the_record_and_clears_the_unknown_flag(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "GTA V Enhanced", "appid": 7, "folder": "GTA"})
    reg.set_fields("gta-v-enhanced", playtime_seconds=600, ludusavi_unknown=True)

    record, problem = reg.rename("gta-v-enhanced", "Grand Theft Auto V")

    assert problem is None
    assert record["title_key"] == "grand-theft-auto-v"
    assert record["playtime_seconds"] == 600, "czas gry ma jechać z rekordem"
    assert record["ludusavi_unknown"] is False
    assert reg.get("gta-v-enhanced") is None
    assert reg.get("grand-theft-auto-v")["appid"] == 7


def test_rename_refuses_a_key_taken_by_another_game(tmp_path):
    # Zlanie dwóch wpisów w jeden znaczy jeden katalog kopii dla dwóch gier — czyli
    # przywracanie cudzych zapisów do cudzego prefiksu.
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "GTA V Enhanced", "appid": 7})
    reg.upsert({"title": "Grand Theft Auto V", "appid": 9})

    record, problem = reg.rename("gta-v-enhanced", "Grand Theft Auto V")

    assert (record, problem) == (None, "taken")
    assert reg.get("grand-theft-auto-v")["appid"] == 9
    assert reg.get("gta-v-enhanced")["appid"] == 7


def test_rename_to_the_same_title_is_not_a_conflict(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Animal Well", "appid": 7})
    record, problem = reg.rename("animal-well", "Animal Well")
    assert problem is None and record["appid"] == 7


def test_rename_reports_missing_and_empty_separately(tmp_path):
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Animal Well", "appid": 7})
    assert reg.rename("nie-ma-takiej", "Cokolwiek") == (None, "missing")
    assert reg.rename("animal-well", "   ") == (None, "empty")
    assert reg.get("animal-well") is not None
