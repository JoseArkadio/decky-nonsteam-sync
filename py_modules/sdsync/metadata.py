"""Metadane gier ze sklepu Steama: data premiery, opis, gatunki, ocena.

Dwa źródła, oba opisane pomiarami w `docs/superpowers/specs/2026-08-22-metadane-design.md`:
identyfikator sklepowy bierzemy z manifestu Ludusaviego (lokalnie, bez sieci), a treść
z publicznego `appdetails` (bez klucza API).

Moduł nie importuje `decky` — jak cały silnik.
"""

import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Warstwa TLS jest WSPÓLNA z grafikami celowo: Python wtyczki nie ma certyfikatów
# (zmierzone, opisane w AGENTS.md), a druga kopia tej logiki znaczyłaby, że następna
# zmiana w certyfikatach naprawia połowę wtyczki.
from .artwork import _ssl_context
from .messages import msg
# Manifest Ludusaviego jest spisem TYTUŁÓW i mieszka w titles.py; tutaj czytamy
# z niego tylko identyfikator sklepowy.
from .titles import manifest_path, title_of


STORE_URL = "https://store.steampowered.com/api/appdetails?appids=%d&l=%s"
# Wyszukiwarka sklepu — ta sama, z której korzysta strona Steama. Potrzebna, bo baza
# Ludusaviego wiąże tytuł z JEDNYM appidem i potrafi trafić w inne wydanie:
# ZMIERZONE, że „Grand Theft Auto V" prowadzi do 271590 (Legacy), a na karcie
# użytkownika leży Enhanced (3240220). Bez tej trasy nie ma jak tego poprawić.
SEARCH_URL = "https://store.steampowered.com/api/storesearch/?term=%s&l=%s&cc=PL"
# Rok premiery i RODZAJ pozycji, po jednym appidzie. ZMIERZONE 2026-08-24: 2,5–20 kB
# i 0,3 s na grę, a ZBIORCZE pytanie (`appids=a,b,c`) kończy się HTTP 400 dla KAŻDEGO
# filtru poza `price_overview` — więc lecą równolegle. Wyszukiwarka sklepu nie zwraca
# ani roku, ani rodzaju: ZMIERZONE, że ścieżka dźwiękowa i skórki DLC mają tam
# `type: "app"` dokładnie jak gra, więc odsiać ich po jej odpowiedzi się NIE DA.
# `filters=basic` daje prawdziwe `type` („game" / „dlc" / „music" / „demo").
DETAILS_URL = ("https://store.steampowered.com/api/appdetails"
               "?appids=%d&filters=basic,release_date&l=%s")
# Ile pozycji opisujemy naraz. Wyszukiwarka zwraca do 10, więc jedna runda wystarcza.
DETAILS_WORKERS = 6
# Ilu ludzi gra TERAZ. Publiczne, bez klucza — tego samego adresu używa wtyczka
# playcount-decky. ZMIERZONE 2026-08-25: 0,17–0,35 s na zapytanie, odpowiedź to
# `{"response": {"player_count": N, "result": 1}}`.
PLAYERS_URL = ("https://api.steampowered.com/ISteamUserStats/"
               "GetNumberOfCurrentPlayers/v1/?appid=%d")
# Liczba graczy ZMIENIA SIĘ, więc pamięć jest krótka i tylko w pamięci procesu:
# zapisana na dysku przeżyłaby restart i pokazywała wczorajszy tłum jako dzisiejszy.
# Trzy minuty chronią przed pytaniem przy każdym przejściu tam i z powrotem między
# dwoma ekranami gier, a nie zdążą skłamać.
PLAYERS_TTL = 180
_players_cache = {}
# Zgodność ze sprzętem Valve. ZMIERZONE: jedno zapytanie zwraca cztery kategorie —
# Steam Deck, SteamOS, Steam Machine i Steam Frame. Ten adres nie jest częścią
# udokumentowanego API (używa go strona sklepu), więc jego zniknięcie musi znaczyć
# „nie wiem", nigdy „niewspierana".
COMPAT_URL = ("https://store.steampowered.com/saleaction/"
              "ajaxgetdeckappcompatibilityreport?nAppID=%d&l=%s")
COMPAT_DEVICES = (("deck", "resolved_category"),
                  ("steamos", "steamos_resolved_category"),
                  ("machine", "machine_resolved_category"),
                  ("frame", "frame_resolved_category"))

# Kategorie sklepu to jedna lista, w której tryby gry sąsiadują z szumem („Dostępne
# napisy", „Udostępnianie gier", dwa razy ten sam pad). ZMIERZONE na Split Fiction,
# Invincible VS i Animal Well — te identyfikatory to tryby, i tylko one:
MODE_CATEGORIES = frozenset((1, 2, 9, 24, 27, 36, 38, 39, 47, 48, 49))
CLOUD_CATEGORY = 23

# Steam chce NAZWY języka, nie kodu. Front podaje kod dwuliterowy — tłumaczenie jest
# tutaj, żeby front został cienki i nie wchodził w drogę wielojęzyczności.
DEFAULT_LANG = "english"
STEAM_LANG = {
    "bg": "bulgarian", "cs": "czech", "da": "danish", "de": "german", "el": "greek",
    "en": "english", "es": "spanish", "fi": "finnish", "fr": "french", "hu": "hungarian",
    "it": "italian", "ja": "japanese", "ko": "koreana", "nl": "dutch", "no": "norwegian",
    "pl": "polish", "pt": "portuguese", "ro": "romanian", "ru": "russian", "sv": "swedish",
    "th": "thai", "tr": "turkish", "uk": "ukrainian", "vi": "vietnamese", "zh": "schinese",
}


def steam_ids(titles, path=None) -> dict:
    """Identyfikatory sklepowe podanych tytułów, JEDNYM przejściem po manifeście.

    Manifest ma 17 MB i 53019 tytułów (zmierzone), więc pętla po tytułach z osobnym
    odczytem kosztowałaby liniowo w wielkości biblioteki.

    Appid leży pod `  steam:` / `    id:`. Blok `  id:` to INNE sklepy — Blue Prince
    ma tam `steamExtra` z liczbami, które nie są jego appidem, więc „pierwsza liczba
    w bloku" wzięłaby cudzą grę.
    """
    if path is None:
        path = manifest_path()
    szukane = set(titles)
    znalezione = {}
    if not path or not szukane:
        return znalezione
    try:
        with open(path, encoding="utf-8") as plik:
            tytul = None
            w_steam = False
            for linia in plik:
                if linia[:1] not in (" ", "\n", "\t", "#"):
                    tytul = title_of(linia)
                    w_steam = False
                elif tytul not in szukane:
                    continue
                elif not linia.startswith("   "):          # klucz na drugim poziomie
                    w_steam = linia.strip() == "steam:"
                elif w_steam and linia.lstrip().startswith("id:"):
                    appid = linia.split(":", 1)[1].strip()
                    if appid.isdigit():
                        znalezione[tytul] = int(appid)
                        if len(znalezione) == len(szukane):
                            break
    except OSError:
        return {}
    return znalezione


def parse_players(payload):
    """Liczba grających TERAZ, albo `None`.

    `result: 1` znaczy „policzone". Zero graczy i „nie wiem" to DWIE rzeczy: pierwsze
    wolno pokazać, drugiego nie — liczba wzięta znikąd wygląda jak informacja o grze.
    """
    odpowiedz = (payload or {}).get("response") or {}
    if odpowiedz.get("result") != 1:
        return None
    ilu = odpowiedz.get("player_count")
    return ilu if isinstance(ilu, int) else None


def parse_details(payload, appid) -> dict:
    """`{"year": "2026", "kind": "game"}` z odpowiedzi `filters=basic,release_date`.

    ZMIERZONE: data przychodzi już PRZETŁUMACZONA („5 czerwca 2026"), a gra
    niewydana daje sam rok („2026") i `coming_soon: true`. Wyciągamy więc czterocyfrową
    liczbę, a nie parsujemy daty — parser dat musiałby znać każdy język sklepu.
    Nieznany appid to `{"success": false}` z kodem 200, czyli rozstrzyga treść.
    """
    wpis = (payload or {}).get(str(appid)) or {}
    if not wpis.get("success"):
        return {"year": "", "kind": ""}
    dane = wpis.get("data") or {}
    data = (dane.get("release_date") or {}).get("date") or ""
    rok = re.search(r"\b(\d{4})\b", str(data))
    return {"year": rok.group(1) if rok else "", "kind": str(dane.get("type") or "")}


def parse_search(payload) -> list:
    """[{appid, name}] z odpowiedzi wyszukiwarki sklepu.

    ZMIERZONE 2026-08-24 (fixture `steam_storesearch_GOTHIC.json`): pole nazywa się
    `id`, nie `appid`, a obok gier bywa ścieżka dźwiękowa („Gothic 1 Remake
    Soundtrack") — też z `type: "app"`, więc odsiać jej po typie się NIE DA i nie
    udajemy, że umiemy. Nieznana fraza to `{"total": 0, "items": []}` z kodem 200,
    czyli o wyniku rozstrzyga treść, nie kod odpowiedzi.
    """
    items = (payload or {}).get("items")
    if not isinstance(items, list):
        return []
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        appid, name = item.get("id"), item.get("name")
        if isinstance(appid, int) and name:
            found.append({"appid": appid, "name": str(name)})
    return found


def parse_appdetails(payload) -> dict:
    """Pola, które pokazujemy — albo None, gdy Steam tej gry nie zna.

    ZMIERZONE: nieznany appid to HTTP 200 z `{"<id>": {"success": false}}`, więc kod
    odpowiedzi nie mówi tu nic i patrzymy na flagę.
    """
    if not isinstance(payload, dict):
        return None
    wpis = next(iter(payload.values()), None)
    if not isinstance(wpis, dict) or not wpis.get("success"):
        return None
    dane = wpis.get("data")
    if not isinstance(dane, dict) or not dane:
        return None
    kategorie = [c for c in dane.get("categories") or [] if isinstance(c, dict)]
    return {
        "name": dane.get("name") or "",
        "description": dane.get("short_description") or "",
        "release_date": (dane.get("release_date") or {}).get("date") or "",
        "genres": [g.get("description") for g in dane.get("genres") or []
                   if isinstance(g, dict) and g.get("description")],
        "developers": list(dane.get("developers") or []),
        "publishers": list(dane.get("publishers") or []),
        "metacritic": (dane.get("metacritic") or {}).get("score"),
        "modes": [c["description"] for c in kategorie
                  if c.get("id") in MODE_CATEGORIES and c.get("description")],
        # None, a nie 0: „nie wiem, ile osiągnięć" to nie „gra ich nie ma"
        "achievements": (dane.get("achievements") or {}).get("total"),
        "cloud": any(c.get("id") == CLOUD_CATEGORY for c in kategorie),
        "controller": dane.get("controller_support") or None,
    }


def parse_compat(payload) -> dict:
    """Kategorie zgodności dla urządzeń Valve: 0 nietestowana, 1 niewspierana,
    2 grywalna, 3 zweryfikowana. Pusty słownik znaczy „nie wiem"."""
    # Flagi `success` NIE sprawdzamy i to jest wybór z pomiaru: dla nieistniejącej gry
    # ten adres zwraca `{"success":1,"results":[]}` — jedynką odpowiada i na sukces,
    # i na brak, więc rozstrzyga kształt `results` (słownik = dane, pusta LISTA = brak).
    wyniki = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(wyniki, dict):
        return {}
    return {nasze: wyniki.get(ich) for nasze, ich in COMPAT_DEVICES}


def _default_fetcher(url: str) -> bytes:
    # User-Agent jak w artwork.py: domyślny "Python-urllib/3.x" bywa odrzucany.
    request = urllib.request.Request(url, headers={"User-Agent": "sd-sync decky plugin"})
    with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
        return response.read()


class Metadata:
    """Pamięć podręczna metadanych: plik `metadata.json` w katalogu ustawień.

    Rejestru NIE dotykamy — metadane to dane ozdobne, których utrata nic nie kosztuje,
    a rejestr trzyma stan zapisów.
    """

    def __init__(self, settings_dir: str, manifest=None, fetcher=None, clock=None):
        self.path = os.path.join(settings_dir, "metadata.json")
        self.manifest = manifest if manifest is not None else manifest_path()
        self.fetcher = fetcher or _default_fetcher
        self.clock = clock or time.time

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as plik:
                dane = json.load(plik)
            return dane if isinstance(dane, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store(self, key: str, wpis: dict):
        dane = self._load()
        dane[key] = wpis
        self._save(dane)

    def _save(self, dane: dict):
        """Zapis przez plik tymczasowy o UNIKATOWEJ nazwie.

        ZMIERZONE na Decku (log zdarzeń): przy stałej nazwie „metadata.json.tmp" dwóch
        piszących otwiera ten sam plik, a pierwszy `os.replace` zabiera go drugiemu spod
        ręki — `fetch_metadata failed with FileNotFoundError: … .tmp -> metadata.json`.
        Ta sama grabla, co w `registry._write`, i to samo lekarstwo. Dwóch piszących
        nie jest tu wyjątkiem: ekran gry i sekcja na ekranie Steama pytają równolegle.
        """
        katalog = os.path.dirname(self.path)
        os.makedirs(katalog, exist_ok=True)
        uchwyt, tmp = tempfile.mkstemp(dir=katalog, prefix=".metadata-", suffix=".tmp")
        try:
            with os.fdopen(uchwyt, "w", encoding="utf-8") as plik:
                json.dump(dane, plik, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def players(self, appid) -> dict:
        """`{"players": N}` albo `{"error": …}`. Nigdy zera z niewiedzy.

        Osobno od reszty metadanych i celowo NIE w `metadata.json`: tamte są opisem
        gry i starzeją się przez miesiące, a to jest pomiar chwili.
        """
        try:
            numer = int(appid)
        except (TypeError, ValueError):
            numer = 0
        # ZMIERZONE i to jest PUŁAPKA, nie ostrożność: `appid=0` zwraca liczbę graczy
        # CAŁEGO Steama (25 186 835 przy pomiarze) z `result: 1`. Gra bez znanego appidu
        # pokazałaby więc cały Steam jako swoich graczy — liczbę fałszywą, a wyglądającą
        # jak prawdziwa.
        if numer <= 0:
            return {}
        wpis = _players_cache.get(numer)
        if wpis and self.clock() - wpis[0] <= PLAYERS_TTL:
            return {"players": wpis[1]}
        try:
            dane = json.loads(self.fetcher(PLAYERS_URL % numer).decode("utf-8"))
        except Exception as blad:
            return {"error": msg("metadata_store_unreachable",
                                 detail=str(blad) or blad.__class__.__name__)}
        ilu = parse_players(dane)
        if ilu is None:
            # Steam nie liczy tej gry (albo appid jest nie ten) — to nie awaria,
            # ale i nie liczba; wiersza po prostu nie będzie.
            return {}
        _players_cache[numer] = (self.clock(), ilu)
        return {"players": ilu}

    def forget(self, key: str) -> bool:
        """Zapomnij metadane tej gry — po zmianie tytułu opisują CUDZĄ grę.

        Metadane są ozdobą, więc kasowanie ich jest tanie: następne wejście na ekran
        gry pobierze je od nowa. Zostawienie starego wpisu pokazywałoby opis wydania,
        które właśnie przestało być tą grą.
        """
        dane = self._load()
        if key not in dane:
            return False
        dane.pop(key)
        self._save(dane)
        return True

    def _compat(self, appid: int, jezyk: str) -> dict:
        try:
            return parse_compat(json.loads(self.fetcher(COMPAT_URL % (appid, jezyk)).decode("utf-8")))
        except Exception:
            return {}

    def get(self, key: str, lang: str = ""):
        """Zapamiętany wpis albo None.

        `lang` niepuste znaczy „chcę TEN język": opis, data premiery i gatunki
        przychodzą ze Steama PRZETŁUMACZONE, więc wpis w innym języku jest tak samo
        bezużyteczny jak brak wpisu. Wyjątkiem jest `missing` — „Steam nie zna tej gry"
        to fakt o grze, nie o języku, i pytanie ponownie po przełączeniu języka byłoby
        siecią bez żadnej możliwej nowej odpowiedzi."""
        wpis = self._load().get(key)
        if not wpis or not lang or wpis.get("missing"):
            return wpis
        return wpis if wpis.get("lang") == lang else None

    def all(self) -> dict:
        return self._load()

    @staticmethod
    def appid_key(appid) -> str:
        """Klucz dla gry, której nie ma w naszym rejestrze (kafelek ze Steama).
        Prefiks, żeby nie mógł zderzyć się z żadnym `title_key`."""
        return "steam:%s" % appid

    def fetch_by_appid(self, appid, lang: str = "en") -> dict:
        """Metadane gry, której appid znamy WPROST od Steama.

        Baza Ludusaviego jest tu niepotrzebna — służy do tłumaczenia tytułu na appid,
        a dla kafelka ze Steama appid JEST tożsamością. Dzięki temu ta trasa działa
        także wtedy, gdy Ludusaviego nie ma wcale.
        """
        try:
            numer = int(appid)
        except (TypeError, ValueError):
            return {"error": msg("metadata_store_unreachable", detail="invalid appid %r" % (appid,))}
        return self._fetch_for(self.appid_key(numer), numer, lang)

    def search(self, text: str, lang: str = "en") -> list:
        """Gry ze sklepu Steama pod wpisaną frazą. Sieć, więc wołane przez `to_thread`.

        Nic nie zapamiętuje: to lista do WYBORU, a nie stan gry.
        """
        fraza = (text or "").strip()
        if not fraza:
            return []
        jezyk = STEAM_LANG.get((lang or "")[:2].lower(), DEFAULT_LANG)
        url = SEARCH_URL % (urllib.parse.quote(fraza), jezyk)
        try:
            found = parse_search(json.loads(self.fetcher(url).decode("utf-8")))
        except Exception:
            # awaria sieci nie może wysypać ekranu gry; pusta lista znaczy „nic nie
            # znalazłem", a rozróżnienie od „nie udało się zapytać" niosłoby tu tyle
            # samo co jedno zdanie w interfejsie — ponytail: gdy zacznie mylić, rozdziel
            return []
        return self._only_games(found, jezyk)

    def _only_games(self, found: list, jezyk: str) -> list:
        """Same GRY, każda z rokiem premiery.

        Dwa zgłoszenia, jedno zapytanie. „Chciałbym żeby pojawiała się nazwa gry i rok
        jej wydania, a nie appid (to nie jest dla ludzi znane)" — appid rozróżniał
        wydania („Grand Theft Auto V" Legacy wobec Enhanced), ale jako liczba nie mówi
        człowiekowi nic; rok mówi. „Lista pokazuje też soundtracki i dodatki — możemy
        to wyciąć bo może być mylące" — ZMIERZONE na „007 First Light": jedna gra
        i PIĘĆ pozycji `dlc` (skórki, ulepszenie edycji), a wyszukiwarka sklepu
        wszystkim daje `type: "app"`.

        Jedno i drugie siedzi w tej samej odpowiedzi `filters=basic,release_date`, więc
        odsianie dodatków nie kosztuje ani jednego zapytania więcej.

        Pozycja, której NIE UDAŁO SIĘ opisać, zostaje na liście bez roku — „nie wiem,
        co to jest" nie może znaczyć „to na pewno dodatek". Wycinamy wyłącznie te,
        o których sklep POWIEDZIAŁ, że nie są grą.
        """
        if not found:
            return found

        def opisz(item):
            try:
                payload = json.loads(
                    self.fetcher(DETAILS_URL % (item["appid"], jezyk)).decode("utf-8"))
                return parse_details(payload, item["appid"])
            except Exception:
                return {"year": "", "kind": ""}

        with ThreadPoolExecutor(max_workers=DETAILS_WORKERS) as pula:
            opisy = list(pula.map(opisz, found))
        return [dict(item, year=opis["year"])
                for item, opis in zip(found, opisy)
                if opis["kind"] in ("", "game")]

    def fetch(self, key: str, title: str, lang: str = "en", appid=None) -> dict:
        """Pobiera i zapamiętuje. Trzy stany, nie dwa: mamy dane / gry nie ma na Steamie
        / nie udało się zapytać. Ten trzeci NIE trafia do pamięci — inaczej jedno
        zapytanie bez sieci odbierałoby grze opis na zawsze.

        `appid` podane WPROST bije bazę Ludusaviego i omija ją całkiem: to ręczne
        wskazanie gry w sklepie, robione właśnie dlatego, że baza wskazała nie to
        wydanie (albo nie zna appidu wcale)."""
        if appid:
            return self._fetch_for(key, int(appid), lang)
        if not self.manifest or not os.path.isfile(self.manifest):
            return {"error": msg("metadata_no_ludusavi_db")}

        appid = steam_ids([title], self.manifest).get(title)
        if not appid:
            wpis = {"missing": True, "fetched": int(self.clock())}
            self._store(key, wpis)
            return wpis
        return self._fetch_for(key, appid, lang)

    def _fetch_for(self, key: str, appid: int, lang: str) -> dict:
        """Pobranie i zapamiętanie dla ZNANEGO appidu — wspólne dla obu tras."""
        kod = (lang or "")[:2].lower()
        jezyk = STEAM_LANG.get(kod, DEFAULT_LANG)
        url = STORE_URL % (appid, jezyk)
        try:
            payload = json.loads(self.fetcher(url).decode("utf-8"))
        except Exception as blad:
            return {"error": msg("metadata_store_unreachable",
                               detail=str(blad) or blad.__class__.__name__),
                    "steam_appid": appid}

        dane = parse_appdetails(payload)
        wpis = {"steam_appid": appid, "fetched": int(self.clock()), "lang": kod}
        wpis.update(dane if dane else {"missing": True})
        if dane:
            # Osobne zapytanie, więc osobna awaria: nieudany DODATEK nie może zabrać
            # opisu, który już mamy. Brak zgodności znaczy „nie wiem".
            wpis["compat"] = self._compat(appid, jezyk)
        self._store(key, wpis)
        return wpis
