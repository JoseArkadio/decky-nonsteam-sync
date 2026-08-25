"""Czas przejścia gry z HowLongToBeat.

HLTB nie ma publicznego API. ZMIERZONE 2026-08-25 (i tak samo robi to hltb-for-deck):
adres wyszukiwarki i token są w PACZCE JS strony i rotują, więc dojście do danych ma
cztery kroki — strona główna, skrypt z paczki, `/init` po token, dopiero potem POST.
Każdy z nich potrafi się zmienić bez zapowiedzi, więc każdy musi umieć powiedzieć
„nie wiem" — i to jest sedno tych testów, nie same liczby.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.hltb import Hltb, parse_times, pick

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def wyniki():
    with open(os.path.join(FIXTURES, "hltb_search_GOTHIC.json")) as f:
        return json.load(f)


# ---------- wybór właściwej gry ----------

def test_pick_prefers_the_steam_appid_over_the_name():
    """Nazwa bywa dwuznaczna („Gothic" wobec „Gothic 1 Remake"), a appid nie jest.
    Dla naszych gier appid znamy z metadanych, więc to on ma rozstrzygać."""
    assert pick(wyniki()["data"], "Gothic 1 Remake", 65540)["game_id"] == 3721


def test_pick_falls_back_to_an_exact_name_when_there_is_no_appid():
    assert pick(wyniki()["data"], "Gothic 1 Remake", None)["game_id"] == 130855


def test_pick_ignores_case_and_punctuation_in_the_name():
    # ta sama składanka co przy szukaniu tytułów: bez tego „Marvel Tōkon" nie trafi
    assert pick(wyniki()["data"], "gothic 1 remake!", None)["game_id"] == 130855


def test_pick_takes_a_lone_hit_even_when_the_name_differs():
    """Jedno trafienie na wpisaną nazwę to mocny sygnał — HLTB nazywa gry inaczej niż
    Steam („Gothic® 3" wobec „Gothic 3"). Przy kilku trafieniach zgadywanie odpada."""
    jeden = [wyniki()["data"][1]]
    assert pick(jeden, "Zupelnie inna nazwa", None)["game_id"] == 3721


def test_pick_refuses_to_guess_between_several_unmatched_hits():
    # cisza tu znaczyłaby pokazanie czasu CUDZEJ gry i nikt by się nie zorientował
    assert pick(wyniki()["data"], "Zupelnie inna nazwa", None) is None
    assert pick([], "Gothic", None) is None


# ---------- przeliczenie na godziny ----------

def test_parse_times_turns_seconds_into_hours():
    # ZMIERZONE na żywym HLTB: pola comp_* są w SEKUNDACH (128160 = 35,6 h) — te same
    # liczby pokazuje hltb-for-deck na ekranie gry.
    assert parse_times(wyniki()["data"][0]) == {
        "hltb_id": 130855, "main": 35.6, "plus": 51.4, "full": 73.5}


def test_parse_times_omits_what_hltb_does_not_know():
    """Zero w HLTB znaczy „nikt tego nie zgłosił", a nie „gra trwa zero godzin".
    Pokazanie „0 h" byłoby liczbą wziętą znikąd."""
    assert parse_times({"game_id": 7, "comp_main": 3600, "comp_plus": 0, "comp_100": 0}) == {
        "hltb_id": 7, "main": 1.0}
    assert parse_times({"game_id": 7}) == {"hltb_id": 7}


# ---------- pamięć podręczna i trzy stany ----------

def _hltb(tmp_path, fetcher):
    return Hltb(str(tmp_path), fetcher=fetcher, clock=lambda: 1000)


def _udany_fetcher(dane=None):
    def fetcher(url, headers=None, body=None):
        if url.endswith("/") or url.count("/") == 2:            # strona główna
            return b'<script src="/_next/paczka.js"></script>'
        if url.endswith("paczka.js"):
            return b'searchTerms searchOptions fetch("/api/search/site",{method:"POST"})'
        if "/init" in url:
            return json.dumps({"token": "T", "hpKey": "kk", "hpVal": "vv"}).encode()
        return json.dumps(dane if dane is not None else wyniki()).encode()
    return fetcher


def test_times_are_fetched_and_remembered(tmp_path):
    wywolania = []
    fetcher = _udany_fetcher()

    def liczacy(url, headers=None, body=None):
        wywolania.append(url)
        return fetcher(url, headers, body)

    h = _hltb(tmp_path, liczacy)
    assert h.times("gothic-1-remake", "Gothic 1 Remake", None)["main"] == 35.6
    ile = len(wywolania)
    assert h.times("gothic-1-remake", "Gothic 1 Remake", None)["main"] == 35.6
    assert len(wywolania) == ile, "drugie wejście na grę nie może iść do sieci"


def test_the_honeypot_pair_goes_into_the_body_not_only_the_headers(tmp_path):
    """ZMIERZONE i to był JEDYNY powód, dla którego pierwsze podejście dostawało 404:
    para `hpKey`/`hpVal` musi być POLEM W TREŚCI żądania, nie samym nagłówkiem."""
    tresci = []

    def fetcher(url, headers=None, body=None):
        if body is not None:
            tresci.append(json.loads(body))
        return _udany_fetcher()(url, headers, body)

    _hltb(tmp_path, fetcher).times("g", "Gothic 1 Remake", None)
    assert tresci and tresci[-1].get("kk") == "vv", tresci


def test_a_game_hltb_does_not_know_is_remembered(tmp_path):
    wywolania = []

    def fetcher(url, headers=None, body=None):
        wywolania.append(url)
        return _udany_fetcher({"count": 0, "data": []})(url, headers, body)

    h = _hltb(tmp_path, fetcher)
    assert h.times("gra", "Gra Nieznana", None) == {"missing": True, "fetched": 1000}
    ile = len(wywolania)
    h.times("gra", "Gra Nieznana", None)
    assert len(wywolania) == ile, "HLTB-nie-zna-tej-gry to fakt o grze: pytamy raz"


def test_a_failure_to_ask_is_NOT_remembered(tmp_path):
    """Trzeci stan i najważniejszy: zapamiętanie awarii sieci jako „brak danych"
    odebrałoby grze czas przejścia na zawsze po jednym wejściu bez internetu."""
    def padajacy(url, headers=None, body=None):
        raise OSError("sieć padła")

    h = _hltb(tmp_path, padajacy)
    wynik = h.times("gra", "Gothic 1 Remake", None)
    assert "error" in wynik and "missing" not in wynik, wynik
    assert h.get("gra") in (None, {}), "awaria nie ma prawa trafić do pamięci"


def test_a_rotated_endpoint_is_retried_once(tmp_path):
    """Adres i token rotują — pierwsza odpowiedź po zmianie jest błędem, a druga,
    po ponownym rozpoznaniu paczki, już nie. Bez tej próby wtyczka mówiłaby
    „nie udało się zapytać" do następnego przeładowania."""
    stan = {"padnij": True}

    def kapryśny(url, headers=None, body=None):
        if body is not None and stan["padnij"]:
            stan["padnij"] = False
            raise OSError("HTTP Error 404")
        return _udany_fetcher()(url, headers, body)

    assert _hltb(tmp_path, kapryśny).times("g", "Gothic 1 Remake", None)["main"] == 35.6


def test_an_old_not_in_the_database_answer_is_asked_again(tmp_path):
    """„HLTB nie zna tej gry" jest faktem o grze, ale nie na zawsze: gra niewydana
    trafia do bazy po premierze. Bez wygaszania ten wpis odbierałby jej czas przejścia
    do końca świata, a odpytywanie przy każdym wejściu byłoby siecią bez powodu."""
    from sdsync.hltb import MISSING_TTL
    zegar = {"teraz": 1000}
    wywolania = []

    def fetcher(url, headers=None, body=None):
        wywolania.append(url)
        return _udany_fetcher()(url, headers, body)

    h = Hltb(str(tmp_path), fetcher=fetcher, clock=lambda: zegar["teraz"])
    # pierwsze pytanie: baza nie zna
    h._load  # noqa: B018 - czytelność: niżej podmieniamy odpowiedź wyszukiwarki
    def pusty(url, headers=None, body=None):
        wywolania.append(url)
        return _udany_fetcher({"count": 0, "data": []})(url, headers, body)
    h.fetcher = pusty
    assert h.times("gra", "Gra Przed Premiera", None)["missing"] is True

    h.fetcher = fetcher
    zegar["teraz"] += MISSING_TTL // 2
    ile = len(wywolania)
    h.times("gra", "Gra Przed Premiera", None)
    assert len(wywolania) == ile, "w oknie ważności nie pytamy ponownie"

    zegar["teraz"] += MISSING_TTL
    assert h.times("gra", "Gothic 1 Remake", None).get("main") == 35.6, \
        "po wygaśnięciu wpisu trzeba zapytać jeszcze raz"
