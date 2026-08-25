"""Czas przejścia gry z HowLongToBeat.

HLTB nie ma publicznego API i to nie jest wygoda, tylko cena. ZMIERZONE 2026-08-25
(tak samo robi to hltb-for-deck, stąd cała jego maszyneria z unieważnianiem klucza):
adres wyszukiwarki i token siedzą w PACZCE JS strony i rotują, więc dojście do danych
ma cztery kroki:

    1. GET https://howlongtobeat.com            → lista `<script src=…>`
    2. GET tej paczki, która zawiera `searchTerms` i `searchOptions`
                                                → `/api/search/site` z wywołania fetch
    3. GET /api/search/site/init?t=<ms>         → {token, hpKey, hpVal}
    4. POST /api/search/site                    → wyniki

Krok 4 wymaga pary `hpKey`/`hpVal` DWA RAZY: w nagłówkach `x-hp-key`/`x-hp-val`
ORAZ jako pole w treści żądania. ZMIERZONE: bez tego pola w treści odpowiedź to 404 —
i to był jedyny powód, dla którego pierwsze podejście nie działało.

Każdy z tych czterech kroków może się zmienić bez zapowiedzi, więc każda awaria znaczy
„nie wiem", nigdy „ta gra nie ma czasu przejścia". Moduł nie importuje `decky`.
"""

import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.request

from .artwork import _ssl_context
from .messages import msg

BASE = "https://howlongtobeat.com"
# Przeglądarkowy User-Agent jest OBOWIĄZKOWY, jak przy SteamGridDB: domyślny
# „Python-urllib/3.x" bywa odrzucany przez warstwę ochronną, a odmowa wygląda
# wtedy dokładnie jak „gry nie ma w bazie".
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
CACHE_FILE = "hltb.json"
# Ile trzymamy odpowiedź „HLTB nie zna tej gry", zanim zapytamy ponownie. Gra może
# do bazy dojść (premiery!), więc to nie jest fakt na zawsze — ale i nie powód,
# żeby pytać przy każdym wejściu na ekran gry.
MISSING_TTL = 30 * 24 * 3600


def _fold(text: str) -> str:
    """Nazwa sprowadzona do tego, co da się porównać: bez wielkości liter, znaków
    diakrytycznych i interpunkcji. Ta sama składanka co w `titles.fold` — HLTB pisze
    „Gothic® 3", Steam „Gothic 3"."""
    return "".join(z for z in unicodedata.normalize("NFKD", (text or "").lower())
                   if z.isalnum())


def pick(results, title: str, steam_appid):
    """Właściwa gra z listy trafień, albo `None`.

    Kolejność jest wiążąca i nie jest kosmetyczna: appid ze sklepu Steama jest
    JEDNOZNACZNY, a nazwa nie („Gothic" wobec „Gothic 1 Remake"). Pokazanie czasu
    cudzej gry byłoby liczbą, której nikt nie ma jak zakwestionować — więc przy
    kilku trafieniach bez dopasowania wolimy nie powiedzieć nic.
    """
    lista = [g for g in (results or []) if isinstance(g, dict)]
    if not lista:
        return None
    if steam_appid:
        for gra in lista:
            if gra.get("profile_steam") == int(steam_appid):
                return gra
    szukane = _fold(title)
    for gra in lista:
        if _fold(gra.get("game_name")) == szukane or _fold(gra.get("game_alias")) == szukane:
            return gra
    # Jedno trafienie na wpisaną nazwę to mocny sygnał — przy kilku nie ma czym
    # rozstrzygnąć i zgadywanie znaczy cudzy czas na naszej karcie.
    return lista[0] if len(lista) == 1 else None


def parse_times(game) -> dict:
    """`{hltb_id, main, plus, full}` w GODZINACH.

    ZMIERZONE na żywym HLTB: pola `comp_*` są w sekundach (128160 = 35,6 h) — te same
    liczby, które pokazuje hltb-for-deck. Zero znaczy „nikt tego nie zgłosił", a nie
    „gra trwa zero godzin", więc takie pole po prostu wypada.
    """
    out = {"hltb_id": (game or {}).get("game_id")}
    for pole, nazwa in (("comp_main", "main"), ("comp_plus", "plus"), ("comp_100", "full")):
        sekundy = (game or {}).get(pole) or 0
        try:
            sekundy = float(sekundy)
        except (TypeError, ValueError):
            continue
        if sekundy > 0:
            out[nazwa] = round(sekundy / 3600, 1)
    return out


def _default_fetcher(url: str, headers=None, body=None) -> bytes:
    request = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": UA, "Referer": BASE + "/", "Origin": BASE,
                 **({"Content-Type": "application/json"} if body is not None else {}),
                 **(headers or {})})
    with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
        return response.read()


class Hltb:
    """Pamięć podręczna czasów przejścia: `hltb.json` w katalogu ustawień.

    Osobny plik od metadanych ze sklepu, bo to inne źródło i inna trwałość: metadane
    zależą od JĘZYKA i wygasają razem z nim, a czas przejścia jest liczbą i nie ma
    tłumaczenia. Wspólny plik znaczyłby, że przełączenie języka kasuje też to, czego
    język nie dotyczy.
    """

    def __init__(self, settings_dir: str, fetcher=None, clock=None):
        self.path = os.path.join(settings_dir, CACHE_FILE)
        self.fetcher = fetcher or _default_fetcher
        self.clock = clock or time.time
        # rozpoznana paczka: (ścieżka api, token, hpKey, hpVal). Trzymamy w pamięci
        # procesu, bo rotuje — na dysku byłaby częściej nieaktualna niż aktualna.
        self._boot = None

    # --- pamięć ---

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as plik:
                dane = json.load(plik)
            return dane if isinstance(dane, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, dane: dict):
        # Unikatowa nazwa pliku tymczasowego, jak w registry i metadata: przy stałej
        # dwóch piszących otwiera ten sam plik, a pierwszy `os.replace` zabiera go
        # drugiemu spod ręki (ZMIERZONE w logu Decka, trzy razy).
        katalog = os.path.dirname(self.path)
        os.makedirs(katalog, exist_ok=True)
        uchwyt, tmp = tempfile.mkstemp(dir=katalog, prefix=".hltb-", suffix=".tmp")
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

    def get(self, key: str):
        wpis = self._load().get(key)
        if not wpis:
            return None
        if wpis.get("missing") and self.clock() - (wpis.get("fetched") or 0) > MISSING_TTL:
            return None       # gra mogła w tym czasie wyjść i trafić do bazy
        return wpis

    @staticmethod
    def appid_key(appid) -> str:
        """Klucz dla gry ZE STEAMA, której nie mamy w rejestrze — jak w metadata."""
        return "steam:%s" % appid

    # --- sieć ---

    def _bootstrap(self, odswiez=False):
        """(ścieżka, token, hpKey, hpVal) albo None. Cztery kroki opisane u góry pliku."""
        if self._boot and not odswiez:
            return self._boot
        try:
            html = self.fetcher(BASE).decode("utf-8", "replace")
            sciezka = None
            for src in re.findall(r'<script\b[^>]*\bsrc=["\'](.*?)["\']', html):
                url = src if src.startswith("http") else BASE + "/" + src.lstrip("/")
                try:
                    tekst = self.fetcher(url).decode("utf-8", "replace")
                except Exception:
                    continue
                # ta jedna paczka, która niesie wyszukiwarkę
                if "searchTerms" not in tekst or "searchOptions" not in tekst:
                    continue
                trafienia = re.findall(
                    r'fetch\s*\(\s*["\'`](/api/[^"\'`]*)["\'`]\s*,\s*\{[^}]*'
                    r'method:\s*["\'`]POST["\'`]', tekst)
                if trafienia:
                    sciezka = trafienia[-1]
                    break
            if not sciezka:
                return None
            auth = json.loads(self.fetcher(
                "%s%s/init?t=%d" % (BASE, sciezka, int(self.clock() * 1000))))
            token = auth.get("token")
            klucz = next((v for k, v in auth.items()
                          if isinstance(v, str) and "key" in k.lower()), None)
            wartosc = next((v for k, v in auth.items()
                            if isinstance(v, str) and "val" in k.lower()), None)
            if not (token and klucz and wartosc):
                return None
            self._boot = (sciezka, token, klucz, wartosc)
            return self._boot
        except Exception:
            return None

    def _search(self, title: str, boot) -> list:
        sciezka, token, klucz, wartosc = boot
        payload = {
            "searchType": "games",
            "searchTerms": (title or "").split(),
            "searchPage": 1,
            "size": 20,
            "searchOptions": {
                "games": {"userId": 0, "platform": "", "sortCategory": "name",
                          "rangeCategory": "main", "rangeTime": {"min": 0, "max": 0},
                          "gameplay": {"perspective": "", "flow": "", "genre": "",
                                       "difficulty": ""},
                          "modifier": "hide_dlc"},
                "users": {}, "filter": "", "sort": 0, "randomizer": 0,
            },
            # ta sama para co w nagłówkach — ZMIERZONE, że bez niej TUTAJ jest 404
            klucz: wartosc,
        }
        raw = self.fetcher("%s%s" % (BASE, sciezka),
                           headers={"x-auth-token": token, "x-hp-key": klucz,
                                    "x-hp-val": wartosc},
                           body=json.dumps(payload).encode("utf-8"))
        return (json.loads(raw) or {}).get("data") or []

    def times(self, key: str, title: str, steam_appid=None) -> dict:
        """Czas przejścia tej gry. TRZY stany, jak przy metadanych ze sklepu:
        dane / `missing` („HLTB nie zna tej gry", zapamiętane) / `error` („nie udało
        się zapytać", NIEzapamiętane).

        Trzeci stan nie trafia do pamięci i to jest cała różnica: zapisanie awarii
        sieci jako braku danych odebrałoby grze czas przejścia po jednym wejściu
        bez internetu.
        """
        zapamietane = self.get(key)
        if zapamietane:
            return zapamietane
        if not (title or "").strip():
            return {"error": msg("hltb_unreachable", detail="empty title")}

        wyniki, blad = None, None
        # Adres i token ROTUJĄ, więc pierwsza odpowiedź po zmianie jest błędem.
        # Druga próba rozpoznaje paczkę od nowa — bez niej wtyczka mówiłaby
        # „nie udało się zapytać" aż do przeładowania.
        for odswiez in (False, True):
            boot = self._bootstrap(odswiez)
            if not boot:
                blad = "bootstrap"
                continue
            try:
                wyniki = self._search(title, boot)
                break
            except Exception as exc:
                blad = str(exc) or exc.__class__.__name__
                self._boot = None
        if wyniki is None:
            return {"error": msg("hltb_unreachable", detail=blad or "unknown")}

        gra = pick(wyniki, title, steam_appid)
        if not gra:
            wpis = {"missing": True, "fetched": int(self.clock())}
        else:
            wpis = parse_times(gra)
            wpis["fetched"] = int(self.clock())
        dane = self._load()
        dane[key] = wpis
        self._save(dane)
        return wpis

    def forget(self, key: str) -> bool:
        """Po zmianie tytułu zapamiętany czas dotyczy CUDZEJ gry."""
        dane = self._load()
        if key not in dane:
            return False
        dane.pop(key)
        self._save(dane)
        return True
