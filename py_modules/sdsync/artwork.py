import base64
import functools
import json
import os
import ssl
import urllib.parse
import urllib.request

API_BASE = "https://www.steamgriddb.com/api/v2"
API_HOST = urllib.parse.urlsplit(API_BASE).hostname

# Kolejność ma znaczenie: pierwszy wynik z listy jest najlepiej oceniony przez SGDB.
# types=static odsiewa grafiki animowane (.webm/.apng), których
# SetCustomArtworkForApp nie przyjmie. ZMIERZONE: dla naszych trzech gier filtr nie
# zmienia ani jednego wyniku (12/12 te same adresy), więc to zabezpieczenie na
# wypadek gry z animowaną pozycją na czele listy, a NIE naprawa czegokolwiek —
# prawdziwą przyczyną martwych grafik był brak certyfikatów (patrz CA_CANDIDATES).
ENDPOINTS = {
    "grid_p": "grids/game/%d?dimensions=600x900&types=static",
    "grid_l": "grids/game/%d?dimensions=920x430&types=static",
    "hero": "heroes/game/%d?types=static",
    "logo": "logos/game/%d?types=static",
}


# ZMIERZONE na Decku: systemowy python3 łączy się z SGDB bez problemu (HTTP 403 przy
# braku klucza — czyli TLS przeszedł), a Python, którego Decky dostarcza wtyczce, pada
# na CERTIFICATE_VERIFY_FAILED „unable to get local issuer certificate". Binarka
# PyInstalera ma własne OpenSSL ze skompilowaną ścieżką certyfikatów, której na
# SteamOS nie ma. Bez tego CAŁA warstwa sieciowa wtyczki jest martwa, a objaw wygląda
# jak „zły klucz API".
CA_CANDIDATES = (
    "/etc/ssl/cert.pem",                    # SteamOS/Arch → ca-certificates/extracted
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora/RHEL
)


def ca_file(candidates=CA_CANDIDATES):
    """Pierwszy istniejący zestaw certyfikatów albo None (wtedy zostają domyślne
    ścieżki OpenSSL — na Decku puste, ale na innym systemie mogą być dobre)."""
    return next((path for path in candidates if os.path.isfile(path)), None)


@functools.lru_cache(maxsize=1)
def _ssl_context():
    """Kontekst z systemowym CA. Wczytanie zestawu to kilkaset kilobajtów, a przy
    jednej grze idzie pięć zapytań — stąd pamięć podręczna na jedną pozycję."""
    path = ca_file()
    return ssl.create_default_context(cafile=path) if path else ssl.create_default_context()


def _default_fetcher(url: str, api_key: str) -> bytes:
    # User-Agent jest OBOWIĄZKOWY: ZMIERZONE na Decku — SGDB (Cloudflare) odpowiada
    # 403 na domyślne "Python-urllib/3.13" nawet z poprawnym kluczem w nagłówku
    headers = {"User-Agent": "sd-sync decky plugin"}
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
        return response.read()


class Artwork:
    """Klient SteamGridDB. Wymaga WŁASNEGO klucza API użytkownika —
    klucz z wtyczki decky-steamgriddb jest zastrzeżony i grozi banem."""

    def __init__(self, api_key: str, fetcher=None):
        self.api_key = api_key or ""
        self.fetcher = fetcher or _default_fetcher
        # powód ostatniej nieudanej odpowiedzi; pusty, gdy poszło dobrze
        self.last_error = ""

    def _get(self, path: str):
        """Dane z SGDB albo None. Powód porażki ZAWSZE ląduje w `last_error`.

        Wcześniej każdy wyjątek dawał ciche None i użytkownik z poprawnym kluczem
        dostawał komunikat „zły tytuł, zły klucz albo brak sieci" — nie do
        odróżnienia od HTTP 401, timeoutu i literówki w tytule.
        """
        if not self.api_key:
            self.last_error = "no API key"
            return None
        url = "%s/%s" % (API_BASE, path)
        try:
            raw = self.fetcher(url, self.api_key)
        except Exception as exc:
            self.last_error = "%s: %s: %s" % (path, type(exc).__name__, exc)
            return None
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self.last_error = "%s: response is not JSON (%s)" % (path, exc)
            return None
        if not payload.get("success"):
            self.last_error = "%s: SGDB refused: %s" % (
                path, payload.get("errors") or payload)
            return None
        self.last_error = ""
        return payload.get("data")

    def find_game(self, title: str):
        data = self._get("search/autocomplete/%s" % urllib.parse.quote(title))
        if not data:
            # pusta lista przy udanej odpowiedzi to inny przypadek niż awaria —
            # bez tego komunikat dla użytkownika zawsze brzmiał tak samo
            self.last_error = self.last_error or "SGDB does not know the title %r" % title
            return None
        return data[0].get("id")

    def assets_for(self, game_id: int) -> dict:
        """{rodzaj: url}. Brak jednego rodzaju to normalka (nie każda gra ma logo),
        więc nie przerywa reszty — powody trafiają do `last_error` po kolei."""
        out, problems = {}, []
        for kind, template in ENDPOINTS.items():
            data = self._get(template % game_id)
            if data:
                out[kind] = data[0].get("url")
            elif self.last_error:
                problems.append("%s (%s)" % (kind, self.last_error))
        self.last_error = "; ".join(problems)
        return out

    def as_base64(self, url: str) -> str:
        # obrazki leżą na CDN — klucz API leci wyłącznie na host API SGDB
        key = self.api_key if urllib.parse.urlsplit(url).hostname == API_HOST else ""
        raw = self.fetcher(url, key)
        return base64.b64encode(raw).decode("ascii")
