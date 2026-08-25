import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync import playtime  # noqa: E402


def _card(tmp_path, data):
    path = playtime.file_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return str(tmp_path)


def test_publish_never_touches_other_devices(tmp_path):
    """Sedno pomysłu: każde urządzenie pisze WYŁĄCZNIE swój klucz. Gdyby zapis
    przepisywał całą mapę, przełożenie karty kasowałoby czas drugiego Decka."""
    mount = _card(tmp_path, {"hades": {"deck-salon": 3600}})
    merged = playtime.publish(mount, "deck-podroz", {"hades": 1800})
    assert merged == {"hades": {"deck-salon": 3600, "deck-podroz": 1800}}
    assert playtime.read(mount) == merged


def test_publish_creates_the_file_on_a_fresh_card(tmp_path):
    merged = playtime.publish(str(tmp_path), "deck", {"hades": 60})
    assert merged == {"hades": {"deck": 60}}
    assert os.path.isfile(playtime.file_path(str(tmp_path)))


def test_totals_sum_across_devices(tmp_path):
    card = {"hades": {"a": 100, "b": 200}}
    assert playtime.totals(card, {}, "c")["hades"] == 300


def test_our_registry_number_beats_the_card(tmp_path):
    """Kartę mogło nie być w urządzeniu, gdy kończyliśmy grę, więc plik bywa
    starszy niż rejestr. Suma z zaniżonym własnym wkładem to cofnięty licznik."""
    card = {"hades": {"deck": 100, "inny": 50}}
    assert playtime.totals(card, {"hades": 400}, "deck")["hades"] == 450


def test_game_known_only_locally_still_counts(tmp_path):
    assert playtime.totals({}, {"nowa": 30}, "deck") == {"nowa": 30}


def test_broken_card_file_is_empty_not_an_exception(tmp_path):
    """Czas gry to statystyka, nie zapis użytkownika — uszkodzony plik nie może
    wywalić ekranu gier ani zatrzymać synchronizacji zapisów."""
    path = playtime.file_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{to nie jest json")
    assert playtime.read(str(tmp_path)) == {}


def test_missing_card_reads_as_empty(tmp_path):
    assert playtime.read(str(tmp_path / "nie-ma-takiej-karty")) == {}


def test_junk_values_do_not_poison_the_sum(tmp_path):
    mount = _card(tmp_path, {"hades": {"a": "sporo", "b": -5, "c": "120"},
                             "zepsute": "wcale nie mapa"})
    assert playtime.read(mount) == {"hades": {"a": 0, "b": 0, "c": 120}}


def test_device_id_is_never_empty():
    assert playtime.device_id().strip()


def test_publish_survives_a_read_only_card(tmp_path):
    """Karta wyjęta albo zamontowana tylko do odczytu nie może wywalić końca gry —
    licznik tego urządzenia i tak żyje w rejestrze."""
    mount = str(tmp_path / "brak")
    os.makedirs(mount)
    os.chmod(mount, 0o500)
    try:
        assert playtime.publish(mount, "deck", {"hades": 10}) == {"hades": {"deck": 10}}
    finally:
        os.chmod(mount, 0o700)


def test_per_device_shows_who_played_how_much(tmp_path):
    card = {"hades": {"salon": 100}}
    assert playtime.per_device(card, {"hades": 250}, "podroz", "hades") == {
        "salon": 100, "podroz": 250}


def test_device_id_survives_two_devices_with_the_same_hostname(monkeypatch, tmp_path):
    """ZMIERZONE: obrazy SteamOS ustawiają hostname `steamdeck`, więc Deck i Machine
    z pudełka nazywają się tak samo. Sam hostname jako klucz znaczy, że jedno
    urządzenie NADPISUJE czas drugiego przy każdej sesji."""
    monkeypatch.setattr(playtime.socket, "gethostname", lambda: "steamdeck")
    first = tmp_path / "machine-id-a"
    first.write_text("d50b6bac111111111111111111111111")
    second = tmp_path / "machine-id-b"
    second.write_text("aaaaaaaa2222222222222222222222222")

    monkeypatch.setattr(playtime, "MACHINE_ID_PATHS", (str(first),))
    a = playtime.device_id()
    monkeypatch.setattr(playtime, "MACHINE_ID_PATHS", (str(second),))
    b = playtime.device_id()

    assert a != b, "dwa urządzenia dostały ten sam klucz — czas gry będzie się nadpisywał"
    assert a.startswith("steamdeck"), "klucz ma zostać czytelny dla człowieka"


def test_device_id_without_machine_id_is_still_usable(monkeypatch):
    monkeypatch.setattr(playtime, "MACHINE_ID_PATHS", ("/nie/ma/takiego/pliku",))
    monkeypatch.setattr(playtime.socket, "gethostname", lambda: "steamdeck")
    assert playtime.device_id() == "steamdeck"


def test_katalog_zapisow_na_karcie_lezy_obok_pliku_czasu_gry(tmp_path):
    """Karta ma JEDEN katalog wtyczki. Gdyby zapisy poszły gdzie indziej niż czas gry,
    na karcie powstałyby dwa nasze katalogi i sprzątanie jednego zabrałoby drugi."""
    from sdsync import paths
    mount = str(tmp_path)
    assert (os.path.dirname(paths.card_saves_dir(mount))
            == os.path.dirname(playtime.file_path(mount)))


def test_suma_nie_maleje_gdy_karty_nie_ma_w_czytniku():
    """ZMIERZONE na Decku: po wyjęciu karty plik wymiany jest nieczytelny, więc
    totals() zna tylko NASZE sekundy — kafelek spadł z 7,1 min (322 s Deck + 106 s
    Machine) na 5,4 min. Wygląda to dokładnie jak „czas gry się nie sumuje", choć
    suma była policzona poprawnie, dopóki karta była w środku."""
    totals = {"animal-well": 322, "gothic-1-remake": 732}
    zapamietane = {"animal-well": 428, "gothic-1-remake": 0}
    assert playtime.merge_remembered(totals, zapamietane) == {
        "animal-well": 428,      # ostatnia znana suma z dwóch urządzeń
        "gothic-1-remake": 732,  # nasza liczba jest większa niż zapamiętana
    }


def test_zapamietana_suma_nie_przebija_swiezszej_z_karty():
    """Karta w czytniku = świeże dane. Zapamiętana wartość jest tylko dolną granicą."""
    assert playtime.merge_remembered({"a": 900}, {"a": 500}) == {"a": 900}


def test_zapamietana_suma_znosi_smieci():
    assert playtime.merge_remembered({"a": 10}, {"a": None}) == {"a": 10}
    assert playtime.merge_remembered({"a": 10}, {"a": "nonsens"}) == {"a": 10}
