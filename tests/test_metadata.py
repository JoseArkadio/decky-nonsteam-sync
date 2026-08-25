import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.metadata import (Metadata, parse_appdetails, parse_compat,
                             parse_details, parse_players, parse_search, steam_ids)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
MANIFEST = os.path.join(FIXTURES, "ludusavi_manifest_WYCINEK.yaml")


def appdetails_fixture():
    with open(os.path.join(FIXTURES, "steam_appdetails_ANIMAL_WELL.json")) as f:
        return json.load(f)


# ---------- identyfikator sklepowy z manifestu ----------

def test_steam_ids_reads_ids_from_real_manifest():
    assert steam_ids(["Animal Well", "Blue Protocol"], MANIFEST) == {
        "Animal Well": 813230, "Blue Protocol": 2139230}


def test_steam_ids_ignores_steam_extra_list():
    # ZMIERZONE: Blue Prince ma w bloku "id" listę steamExtra (2984250, 3711820),
    # a jego prawdziwy appid to 1569580. Wzięcie pierwszej liczby z bloku znaczyłoby
    # pokazanie opisu i oceny CUDZEJ gry.
    assert steam_ids(["Blue Prince"], MANIFEST) == {"Blue Prince": 1569580}


def test_steam_ids_ignores_ids_of_other_stores():
    # ZMIERZONE: "Alien Breed" ma w manifeście "  gog:" / "    id: 1207663733" i żadnego
    # appidu Steama. Sama linia "    id:" nie znaczy więc „identyfikator Steama" —
    # bez bramki na bloku "  steam:" poszlibyśmy po opis zupełnie innej gry.
    assert steam_ids(["Alien Breed", "Amazon: Guardians of Eden"], MANIFEST) == {}


def test_steam_ids_handles_quoted_title_with_colon():
    assert steam_ids(["The Binding of Isaac: Rebirth"], MANIFEST) == {
        "The Binding of Isaac: Rebirth": 250900}


def test_steam_ids_omits_titles_without_id_and_titles_absent_from_manifest():
    # "-KLAUS-" jest w manifeście, ale ma tylko alias; "GTA V Enhanced" nie ma go wcale
    # (prawdziwy przypadek z biblioteki użytkownika).
    assert steam_ids(["-KLAUS-", "GTA V Enhanced"], MANIFEST) == {}


def test_steam_ids_reads_the_file_once_for_many_titles(tmp_path):
    # Manifest ma 17 MB — pętla po tytułach z osobnym odczytem to koszt liniowy
    # w wielkości biblioteki. Pilnuje tego licznik otwarć, nie zegar.
    licznik = {"n": 0}
    prawdziwy_open = open

    def zliczajacy(path, *a, **kw):
        if path == MANIFEST:
            licznik["n"] += 1
        return prawdziwy_open(path, *a, **kw)

    import builtins
    builtins.open = zliczajacy
    try:
        steam_ids(["Animal Well", "Blue Prince", "Blue Protocol"], MANIFEST)
    finally:
        builtins.open = prawdziwy_open
    assert licznik["n"] == 1


def test_steam_ids_survives_missing_manifest():
    assert steam_ids(["Animal Well"], "/nie/ma/takiego/manifestu.yaml") == {}


# ---------- odpowiedź sklepu ----------

def test_parse_appdetails_takes_the_fields_we_show():
    dane = parse_appdetails(appdetails_fixture())
    assert dane["name"] == "ANIMAL WELL"
    assert dane["release_date"] == "9 maja 2024"
    assert dane["genres"] == ["Akcja", "Przygodowe", "Niezależne"]
    assert dane["developers"] == ["Billy Basso"]
    assert dane["publishers"] == ["Bigmode"]
    assert dane["metacritic"] == 90
    assert dane["description"].startswith("Explore a dense")


def test_parse_appdetails_drops_the_heavy_fields():
    # Zapisujemy plik na urządzeniu użytkownika: 21 KB odpowiedzi na grę to głównie
    # zrzuty ekranu, filmy i wymagania sprzętowe, których nie pokazujemy.
    dane = parse_appdetails(appdetails_fixture())
    for pole in ("screenshots", "movies", "pc_requirements", "price_overview", "packages"):
        assert pole not in dane


def test_parse_appdetails_returns_none_when_steam_does_not_know_the_game():
    # ZMIERZONE: nieznany appid to HTTP 200 z {"999999999": {"success": false}},
    # więc kod odpowiedzi nie mówi tu nic.
    assert parse_appdetails({"999999999": {"success": False}}) is None


def test_parse_appdetails_trusts_the_success_flag_over_the_payload():
    # Ładunek ZŁOŻONY na potrzeby testu, nie zmierzony: Steam przy "success": false nie
    # przysłał nam "data" ani razu. Sprawdzamy tu INTENCJĘ — flaga rozstrzyga, a nie
    # „obecność jakichkolwiek danych" — żeby dołożone kiedyś pole nie przeszło za dane.
    assert parse_appdetails({"813230": {"success": False, "data": {"name": "cokolwiek"}}}) is None


def test_parse_appdetails_returns_none_for_garbage():
    assert parse_appdetails({}) is None
    assert parse_appdetails({"813230": {"success": True}}) is None


# ---------- pamięć podręczna i trzy stany ----------

class FakeFetcher:
    def __init__(self, reply=None, blad=None):
        self.reply = reply if reply is not None else appdetails_fixture()
        self.blad = blad
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.blad:
            raise self.blad
        return json.dumps(self.reply).encode()


def meta(tmp_path, fetcher):
    return Metadata(str(tmp_path), manifest=MANIFEST, fetcher=fetcher)


def test_fetch_stores_data_and_get_serves_it_without_network(tmp_path):
    f = FakeFetcher()
    m = meta(tmp_path, f)
    wynik = m.fetch("animal well", "Animal Well", lang="pl")
    assert wynik["name"] == "ANIMAL WELL"
    assert wynik["steam_appid"] == 813230
    assert "l=polish" in f.calls[0], "kod języka musi zamienić się na nazwę dla Steama"
    assert "appids=813230" in f.calls[0]

    drugi = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=FakeFetcher(blad=AssertionError("sieć!")))
    assert drugi.get("animal well")["name"] == "ANIMAL WELL"


def test_unknown_language_falls_back_to_english(tmp_path):
    f = FakeFetcher()
    meta(tmp_path, f).fetch("animal well", "Animal Well", lang="xx")
    assert "l=english" in f.calls[0]


def test_game_absent_from_steam_is_remembered_so_we_stop_asking(tmp_path):
    f = FakeFetcher(reply={"813230": {"success": False}})
    m = meta(tmp_path, f)
    wynik = m.fetch("animal well", "Animal Well")
    assert wynik["missing"] is True
    assert m.get("animal well")["missing"] is True


def test_title_without_appid_is_remembered_without_touching_the_network(tmp_path):
    f = FakeFetcher()
    m = meta(tmp_path, f)
    assert m.fetch("gta v enhanced", "GTA V Enhanced")["missing"] is True
    assert f.calls == [], "bez appidu nie ma o co pytać"


def test_network_failure_is_NOT_remembered_as_absent_from_steam(tmp_path):
    # To jest jedyny błąd w tym module, który boli: gra z prawdziwym opisem dostałaby
    # na stałe „Steam nie zna tej gry", bo raz zapytaliśmy bez sieci.
    f = FakeFetcher(blad=OSError("brak sieci"))
    m = meta(tmp_path, f)
    wynik = m.fetch("animal well", "Animal Well")
    # Kod, nie treść zdania: rozpoznawanie stanu po napisie jest w tym projekcie
    # zakazane, a od wielojęzyczności zdanie zależy od języka.
    assert wynik["error"]["code"] == "metadata_store_unreachable"
    assert "brak sieci" in wynik["error"]["message"], "szczegół nie może zginąć"
    assert "missing" not in wynik
    assert m.get("animal well") is None, "awaria nie zostawia śladu w pamięci"

    udany = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=FakeFetcher())
    assert udany.fetch("animal well", "Animal Well")["name"] == "ANIMAL WELL"


def test_refresh_overwrites_a_remembered_absence(tmp_path):
    m = meta(tmp_path, FakeFetcher(reply={"813230": {"success": False}}))
    m.fetch("animal well", "Animal Well")
    poprawiony = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=FakeFetcher())
    assert poprawiony.fetch("animal well", "Animal Well")["name"] == "ANIMAL WELL"
    assert "missing" not in poprawiony.get("animal well")


def test_broken_store_file_does_not_kill_the_screen(tmp_path):
    (tmp_path / "metadata.json").write_text("{ to nie jest json")
    assert meta(tmp_path, FakeFetcher()).get("animal well") is None


def test_missing_ludusavi_database_is_an_error_not_an_absence(tmp_path):
    # Bez bazy Ludusaviego nie wiemy NIC o żadnej grze. Zapisanie tego jako „nie ma jej
    # na Steamie" odebrałoby opis całej bibliotece, i to na stałe.
    m = Metadata(str(tmp_path), manifest="/nie/ma/bazy.yaml", fetcher=FakeFetcher())
    wynik = m.fetch("animal well", "Animal Well")
    assert wynik["error"]["code"] == "metadata_no_ludusavi_db"
    assert m.get("animal well") is None


# ---------- język: opis jest TREŚCIĄ w języku, więc pamięć musi go znać ----------

def test_fetch_remembers_which_language_it_asked_for(tmp_path):
    m = meta(tmp_path, FakeFetcher())
    assert m.fetch("animal well", "Animal Well", lang="pl")["lang"] == "pl"


def test_cached_description_in_another_language_counts_as_absent(tmp_path):
    # Opis, data premiery i gatunki przychodzą PRZETŁUMACZONE. Po przełączeniu języka
    # interfejsu zapamiętany polski opis jest tak samo bezużyteczny jak brak opisu —
    # inaczej angielski użytkownik zostałby z polskim tekstem na zawsze.
    m = meta(tmp_path, FakeFetcher())
    m.fetch("animal well", "Animal Well", lang="pl")
    assert m.get("animal well", "en") is None
    assert m.get("animal well", "pl")["name"] == "ANIMAL WELL"


def test_get_without_language_serves_any_cached_entry(tmp_path):
    # Zgodność w tył: starszy frontend nie podaje języka i ma dostać to, co jest,
    # zamiast pustki (czyli zamiast pytać sieć przy każdym wejściu na grę).
    m = meta(tmp_path, FakeFetcher())
    m.fetch("animal well", "Animal Well", lang="pl")
    assert m.get("animal well")["name"] == "ANIMAL WELL"


def test_entry_written_before_languages_were_tracked_is_stale(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"animal well": {"name": "STARE"}}))
    m = meta(tmp_path, FakeFetcher())
    assert m.get("animal well", "pl") is None, "bez zapisanego języka nie wiemy, co to za tekst"
    assert m.get("animal well")["name"] == "STARE"


def test_absence_on_steam_does_not_depend_on_language(tmp_path):
    # „Steam nie zna tej gry" to fakt o grze, nie o języku — pytanie ponownie po każdym
    # przełączeniu języka byłoby siecią bez żadnej możliwej nowej odpowiedzi.
    m = meta(tmp_path, FakeFetcher(reply={"813230": {"success": False}}))
    m.fetch("animal well", "Animal Well", lang="pl")
    assert m.get("animal well", "en")["missing"] is True


# ---------- tryby gry, osiągnięcia, zgodność ze sprzętem Valve ----------

def split_fiction_fixture():
    with open(os.path.join(FIXTURES, "steam_appdetails_SPLIT_FICTION.json")) as f:
        return json.load(f)


def compat_fixture(nazwa):
    with open(os.path.join(FIXTURES, "steam_deck_compat_%s.json" % nazwa)) as f:
        return json.load(f)


def test_parse_appdetails_reads_game_modes_in_a_meaningful_order():
    # ZMIERZONE na Split Fiction: kategorie przychodzą jako pary (id, opis w języku
    # zapytania). Bierzemy TYLKO tryby gry — reszta z tej listy to szum w rodzaju
    # „Dostępne napisy" i „Udostępnianie gier".
    dane = parse_appdetails(split_fiction_fixture())
    assert dane["modes"][:3] == ["Wieloosobowa", "Kooperacja", "Kooperacja przez internet"]
    assert "Dostępne napisy" not in dane["modes"]
    assert "Udostępnianie gier" not in dane["modes"]


def test_parse_appdetails_drops_duplicate_controller_categories():
    # ZMIERZONE: Animal Well ma DWA razy „Obsługa kontrolerów DUALSHOCK" (id 55 i 56)
    # i dwa razy DualSense (57, 58). To nie tryby gry, więc w ogóle tu nie wchodzą.
    dane = parse_appdetails(appdetails_fixture())
    assert dane["modes"] == ["Jednoosobowa"]


def test_parse_appdetails_reads_achievements_cloud_and_controller():
    dane = parse_appdetails(split_fiction_fixture())
    assert dane["achievements"] == 20
    assert dane["cloud"] is True
    assert dane["controller"] == "full"


def test_parse_appdetails_without_achievements_says_none_not_zero():
    # „Nie wiem, ile osiągnięć" to nie „gra nie ma osiągnięć". Zero byłoby zdaniem
    # twierdzącym o grze, którego dane nie zawierają.
    dane = parse_appdetails(appdetails_fixture())
    assert dane["achievements"] is None


def test_parse_compat_reads_all_four_valve_devices():
    # ZMIERZONE: jedno zapytanie zwraca kategorie dla Decka, SteamOS, Steam Machine
    # i Steam Frame. 0 nietestowana, 1 niewspierana, 2 grywalna, 3 zweryfikowana.
    assert parse_compat(compat_fixture("BLUE_PRINCE")) == {
        "deck": 3, "steamos": 2, "machine": 3, "frame": None}


def test_parse_compat_reports_unsupported_without_guessing():
    assert parse_compat(compat_fixture("GTA_V")) == {
        "deck": 1, "steamos": 1, "machine": 1, "frame": None}


def test_parse_compat_on_an_unknown_game_says_nothing_rather_than_unsupported():
    # ZMIERZONE: dla appidu 999999999 ten adres zwraca `{"success":1,"results":[]}` —
    # jedynką odpowiada TAKŻE na brak danych, więc rozstrzyga kształt `results`.
    # Pomylenie tego z „niewspierana" powiedziałoby graczowi, że gra nie pójdzie.
    assert parse_compat({"success": 1, "results": []}) == {}
    assert parse_compat({}) == {}
    assert parse_compat(None) == {}


def test_parse_appdetails_says_no_cloud_when_the_category_is_absent():
    # Ładunek ZMIERZONY, z którego usunięto jedną kategorię (23 = Steam Cloud) —
    # sprawdzamy odwzorowanie, nie wymyślony kształt.
    payload = appdetails_fixture()
    dane = payload["813230"]["data"]
    dane["categories"] = [c for c in dane["categories"] if c["id"] != 23]
    assert parse_appdetails(payload)["cloud"] is False


class TwoCallFetcher:
    """Sklep i raport zgodności to DWA różne adresy — atrapa musi je rozróżniać,
    inaczej test nie zauważy, że pytamy tylko o jedno."""

    def __init__(self, compat_blad=None):
        self.compat_blad = compat_blad
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if "appcompatibilityreport" in url:
            if self.compat_blad:
                raise self.compat_blad
            return json.dumps(compat_fixture("BLUE_PRINCE")).encode()
        return json.dumps(appdetails_fixture()).encode()


def test_fetch_stores_compatibility_alongside_the_description(tmp_path):
    f = TwoCallFetcher()
    wynik = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=f).fetch(
        "animal well", "Animal Well", lang="pl")
    assert wynik["compat"] == {"deck": 3, "steamos": 2, "machine": 3, "frame": None}
    assert wynik["name"] == "ANIMAL WELL"
    assert len(f.calls) == 2, "opis i zgodność to dwa różne zapytania"


def test_failed_compatibility_call_does_not_lose_the_description(tmp_path):
    # Dwa zapytania znaczą, że jedno może paść osobno. Utrata całego wpisu z powodu
    # nieudanego DODATKU byłaby zamianą częściowego sukcesu na porażkę.
    f = TwoCallFetcher(compat_blad=OSError("brak sieci"))
    m = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=f)
    wynik = m.fetch("animal well", "Animal Well", lang="pl")
    assert wynik["name"] == "ANIMAL WELL"
    assert "error" not in wynik
    assert wynik.get("compat") in (None, {}), "brak zgodności to brak, nie awaria"
    assert m.get("animal well", "pl")["name"] == "ANIMAL WELL"


# ---------- gry ZE STEAMA: appid znamy wprost, manifest nie jest potrzebny ----------

def test_appid_key_is_separate_from_the_title_key():
    # Gry ze Steama nie ma w naszym rejestrze, więc nie ma dla niej `title_key`.
    # Klucz musi być z appidu i NIE MOŻE zderzyć się z kluczem tytułowym.
    assert Metadata.appid_key(813230) == "steam:813230"
    assert Metadata.appid_key("813230") == "steam:813230"


def test_fetch_by_appid_works_without_the_ludusavi_database(tmp_path):
    # To jest cały sens tej trasy: appid gry ze Steama znamy wprost od Steama,
    # więc baza Ludusaviego (od której zależy tłumaczenie TYTUŁU na appid) nie jest
    # tu do niczego potrzebna — i jej brak nie może tej trasy zablokować.
    f = TwoCallFetcher()
    m = Metadata(str(tmp_path), manifest="/nie/ma/bazy.yaml", fetcher=f)
    wynik = m.fetch_by_appid(813230, lang="pl")
    assert wynik["name"] == "ANIMAL WELL"
    assert wynik["steam_appid"] == 813230
    assert wynik["compat"]["deck"] == 3
    assert m.get(Metadata.appid_key(813230), "pl")["name"] == "ANIMAL WELL"


def test_fetch_by_appid_does_not_overwrite_a_managed_game(tmp_path):
    # Ta sama gra może być JEDNOCZEŚNIE naszą (z karty) i cudzym kafelkiem ze Steama.
    # Wpisy muszą żyć osobno, inaczej jeden przebieg nadpisywałby drugi.
    m = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=TwoCallFetcher())
    m.fetch("animal well", "Animal Well", lang="pl")
    m.fetch_by_appid(813230, lang="pl")
    zapamietane = m.all()
    assert "animal well" in zapamietane and "steam:813230" in zapamietane


def test_fetch_by_appid_remembers_a_game_the_store_does_not_know(tmp_path):
    # ZMIERZONE: sklep na nieznany appid odpowiada 200 z `success: false`.
    class Brak(TwoCallFetcher):
        def __call__(self, url):
            self.calls.append(url)
            return json.dumps({"999999999": {"success": False}}).encode()

    m = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=Brak())
    assert m.fetch_by_appid(999999999)["missing"] is True


def test_fetch_by_appid_failure_leaves_no_trace(tmp_path):
    class Padnie(TwoCallFetcher):
        def __call__(self, url):
            raise OSError("brak sieci")

    m = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=Padnie())
    wynik = m.fetch_by_appid(813230)
    assert wynik["error"]["code"] == "metadata_store_unreachable"
    assert m.get(Metadata.appid_key(813230)) is None


# ---------- wyszukiwarka sklepu (ręczne wskazanie gry) ----------

def search_fixture():
    with open(os.path.join(FIXTURES, "steam_storesearch_GOTHIC.json")) as f:
        return json.load(f)


def test_parse_search_reads_appid_from_the_id_field():
    # ZMIERZONE 2026-08-24: pole nazywa się `id`, nie `appid` — sięgnięcie po „appid"
    # dałoby pustą listę przy poprawnej odpowiedzi, czyli „sklep nic nie zna".
    found = parse_search(search_fixture())
    assert found[0] == {"appid": 1297900, "name": "Gothic 1 Remake"}
    assert len(found) == 10


def test_parse_search_of_an_unknown_phrase_is_empty():
    # ZMIERZONE: nieznana fraza to HTTP 200 i {"total": 0, "items": []} — o wyniku
    # rozstrzyga treść, nie kod odpowiedzi.
    assert parse_search({"total": 0, "items": []}) == []


def test_parse_search_survives_junk():
    assert parse_search(None) == []
    assert parse_search({"items": "nie lista"}) == []
    assert parse_search({"items": [{"name": "bez id"}, {"id": "nie liczba", "name": "x"},
                                   {"id": 7, "name": "dobra"}]}) == [{"appid": 7, "name": "dobra"}]


def test_manual_appid_beats_the_ludusavi_database(tmp_path):
    """ZMIERZONE, dlaczego to musi istnieć: baza Ludusaviego wiąże „Grand Theft Auto V"
    z appidem 271590 (wydanie Legacy), a na karcie użytkownika leży Enhanced. Wskazany
    ręcznie appid ma pominąć bazę CAŁKIEM, nie tylko wygrać remis."""
    pytane = []

    def fetcher(url):
        pytane.append(url)
        return json.dumps({"3240220": {"success": True, "data": {
            "name": "Grand Theft Auto V Enhanced", "short_description": "opis"}}}).encode()

    meta = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=fetcher, clock=lambda: 1)
    wpis = meta.fetch("grand-theft-auto-v", "Blue Prince", lang="en", appid=3240220)

    assert wpis["steam_appid"] == 3240220, wpis
    assert wpis["name"] == "Grand Theft Auto V Enhanced"
    assert all("1569580" not in url for url in pytane), \
        "appid z manifestu (Blue Prince = 1569580) nie moze przebic wskazania czlowieka"
    # zapamiętane pod kluczem GRY, więc ekran gry znajdzie je bez pytania sieci
    assert meta.get("grand-theft-auto-v")["steam_appid"] == 3240220


def test_forget_drops_the_entry_of_the_previous_title(tmp_path):
    meta = Metadata(str(tmp_path), manifest=MANIFEST,
                    fetcher=lambda url: json.dumps(
                        {"813230": {"success": True, "data": {"name": "Animal Well"}}}).encode(),
                    clock=lambda: 1)
    meta.fetch("animal-well", "Animal Well")
    assert meta.get("animal-well")
    assert meta.forget("animal-well") is True
    assert meta.get("animal-well") in (None, {}, "")
    assert meta.forget("animal-well") is False, "drugie zapomnienie nie jest zmianą"


def test_parse_details_reads_a_translated_date_and_a_bare_year():
    # ZMIERZONE 2026-08-24: data przychodzi PRZETŁUMACZONA („5 czerwca 2026"), a gra
    # niewydana daje sam rok. Parser dat musiałby znać każdy język sklepu — rok nie.
    assert parse_details(
        {"1297900": {"success": True,
                     "data": {"type": "game",
                              "release_date": {"coming_soon": False, "date": "5 czerwca 2026"}}}},
        1297900) == {"year": "2026", "kind": "game"}
    assert parse_details(
        {"3754920": {"success": True,
                     "data": {"type": "game",
                              "release_date": {"coming_soon": True, "date": "2026"}}}},
        3754920) == {"year": "2026", "kind": "game"}


def test_parse_details_reads_the_kind_of_the_entry():
    # ZMIERZONE na „007 First Light": jedna gra i pięć pozycji `dlc` (skórki, ulepszenie
    # edycji), plus „Gothic 1 Remake Soundtrack" jako `music`. Wyszukiwarka sklepu daje
    # wszystkim `type: "app"`, więc rodzaj widać dopiero tutaj.
    assert parse_details({"2671240": {"success": True, "data": {"type": "music"}}},
                         2671240)["kind"] == "music"
    assert parse_details({"3986520": {"success": True, "data": {"type": "dlc"}}},
                         3986520)["kind"] == "dlc"


def test_parse_details_of_an_unknown_app_is_empty():
    # ZMIERZONE: nieznany appid to {"success": false} z kodem HTTP 200.
    assert parse_details({"999999999": {"success": False}}, 999999999) == {"year": "", "kind": ""}
    assert parse_details({}, 1) == {"year": "", "kind": ""}
    assert parse_details(None, 1) == {"year": "", "kind": ""}


def _store_fetcher(items, kinds, years):
    def fetcher(url):
        if "storesearch" in url:
            return json.dumps({"items": items}).encode()
        appid = url.split("appids=")[1].split("&")[0]
        return json.dumps({appid: {"success": True, "data": {
            "type": kinds.get(appid, "game"),
            "release_date": {"date": years.get(appid, "2001")}}}}).encode()
    return fetcher


def test_search_attaches_the_release_year_to_every_hit(tmp_path):
    fetcher = _store_fetcher(
        [{"id": 1297900, "name": "Gothic 1 Remake"}, {"id": 65540, "name": "Gothic 1"}],
        {}, {"1297900": "5 czerwca 2026", "65540": "2001"})
    meta = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=fetcher, clock=lambda: 1)
    assert meta.search("gothic", "pl") == [
        {"appid": 1297900, "name": "Gothic 1 Remake", "year": "2026"},
        {"appid": 65540, "name": "Gothic 1", "year": "2001"}]


def test_search_drops_soundtracks_and_dlc(tmp_path):
    """ZGŁOSZONE: „lista wyszukanych gier pokazuje też soundtracki i dodatki — możemy to
    wyciąć bo może być mylące". ZMIERZONE na „007 First Light": jedna gra i pięć skórek
    oraz ulepszeń edycji, wszystkie z `type: "app"` w odpowiedzi wyszukiwarki."""
    fetcher = _store_fetcher(
        [{"id": 3768760, "name": "007 First Light"},
         {"id": 3986520, "name": "007 First Light - Deluxe Edition Upgrade"},
         {"id": 2671240, "name": "Gothic 1 Remake Soundtrack"}],
        {"3986520": "dlc", "2671240": "music"}, {})
    meta = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=fetcher, clock=lambda: 1)
    assert meta.search("007", "pl") == [
        {"appid": 3768760, "name": "007 First Light", "year": "2001"}]


def test_a_hit_the_store_would_not_describe_stays_on_the_list(tmp_path):
    """„Nie wiem, co to jest" nie może znaczyć „to na pewno dodatek". Rok jest ozdobą,
    lista wyboru nie jest — timeout na jednej pozycji nie może zabrać człowiekowi gry,
    której szuka."""
    def fetcher(url):
        if "storesearch" in url:
            return json.dumps({"items": [{"id": 7, "name": "Gra bez daty"}]}).encode()
        raise OSError("sieć padła")

    meta = Metadata(str(tmp_path), manifest=MANIFEST, fetcher=fetcher, clock=lambda: 1)
    assert meta.search("gra", "pl") == [{"appid": 7, "name": "Gra bez daty", "year": ""}]


def test_two_writers_do_not_take_each_others_temp_file(tmp_path):
    """ZMIERZONE na Decku (log zdarzeń, dwa wpisy 2026-08-24 i jeden 2026-08-19):
    `fetch_metadata failed with FileNotFoundError: … metadata.json.tmp -> metadata.json`.
    Stała nazwa pliku tymczasowego znaczy, że dwóch piszących otwiera TEN SAM plik,
    a pierwszy `os.replace` zabiera go drugiemu spod ręki. Rejestr ma na to unikatową
    nazwę od początku (registry._write) — metadane nie miały."""
    import threading

    meta = Metadata(str(tmp_path), manifest=MANIFEST, clock=lambda: 1)
    meta._store("pierwsza", {"name": "A"})   # plik musi już istnieć
    bledy = []
    start = threading.Barrier(6)

    def pisz(numer):
        start.wait()
        try:
            for _ in range(20):
                meta._store("gra-%d" % numer, {"name": str(numer)})
        except Exception as blad:
            bledy.append(blad)

    watki = [threading.Thread(target=pisz, args=(i,)) for i in range(6)]
    for w in watki:
        w.start()
    for w in watki:
        w.join()

    assert not bledy, bledy
    assert meta.get("pierwsza"), "plik przeżył równoległe zapisy"


# ---------- ilu gra teraz ----------

def test_parse_players_reads_the_count():
    # ZMIERZONE 2026-08-25 na publicznym API (bez klucza): ELDEN RING 23240 graczy,
    # 112 Operator 35, Marvel Tokon 3389. `result: 1` znaczy „policzone".
    assert parse_players({"response": {"player_count": 23240, "result": 1}}) == 23240
    assert parse_players({"response": {"player_count": 0, "result": 1}}) == 0


def test_parse_players_of_an_unknown_app_is_none():
    """Zero graczy i „nie wiem" to DWIE rzeczy: pierwsze wolno pokazać, drugiego nie."""
    assert parse_players({"response": {"result": 42}}) is None
    # liczba PRZY złym wyniku też nie przechodzi: to pole `result` mówi, czy Steam
    # w ogóle policzył, a nie obecność liczby
    assert parse_players({"response": {"player_count": 5, "result": 42}}) is None
    assert parse_players({"response": {}}) is None
    assert parse_players({}) is None
    assert parse_players(None) is None


def test_players_are_cached_for_a_short_while(tmp_path):
    """Liczba graczy ZMIENIA SIĘ, więc pamięć podręczna jest krótka i tylko w pamięci
    procesu — zapisana na dysku przeżyłaby restart i pokazywała wczorajszy tłum jako
    dzisiejszy. Krótka wystarcza: chroni przed pytaniem przy każdym przejściu tam
    i z powrotem między dwoma ekranami gier."""
    from sdsync.metadata import PLAYERS_TTL, _players_cache
    _players_cache.clear()
    zegar = {"teraz": 1000}
    wywolania = []

    def fetcher(url):
        wywolania.append(url)
        return json.dumps({"response": {"player_count": 7, "result": 1}}).encode()

    meta = Metadata(str(tmp_path), fetcher=fetcher, clock=lambda: zegar["teraz"])
    assert meta.players(1245620) == {"players": 7}
    assert meta.players(1245620) == {"players": 7}
    assert len(wywolania) == 1, "drugie pytanie w oknie ważności ma iść z pamięci"

    zegar["teraz"] += PLAYERS_TTL + 1
    meta.players(1245620)
    assert len(wywolania) == 2, "po wygaśnięciu trzeba zapytać ponownie"


def test_players_of_a_game_without_an_appid_is_not_the_whole_of_steam(tmp_path):
    """ZMIERZONE i to pułapka, nie ostrożność: `appid=0` zwraca liczbę graczy CAŁEGO
    Steama (25 186 835) z `result: 1`. Gra bez znanego appidu pokazałaby więc cały Steam
    jako swoich graczy — liczbę fałszywą, a wyglądającą jak prawdziwa."""
    from sdsync.metadata import _players_cache
    _players_cache.clear()
    wywolania = []

    def fetcher(url):
        wywolania.append(url)
        return json.dumps({"response": {"player_count": 25186835, "result": 1}}).encode()

    meta = Metadata(str(tmp_path), fetcher=fetcher, clock=lambda: 1)
    assert meta.players(0) == {}
    assert meta.players(None) == {}
    assert wywolania == [], "bez appidu nie ma o co pytać"


def test_players_failure_is_not_a_zero(tmp_path):
    """Awaria sieci nie może wyjść jako „0 graczy" — to liczba wzięta znikąd,
    a wygląda jak informacja o grze."""
    from sdsync.metadata import _players_cache
    _players_cache.clear()

    def padajacy(url):
        raise OSError("sieć padła")

    wynik = Metadata(str(tmp_path), fetcher=padajacy, clock=lambda: 1).players(1245620)
    assert "error" in wynik and "players" not in wynik, wynik
