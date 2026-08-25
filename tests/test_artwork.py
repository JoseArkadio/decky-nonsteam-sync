import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.artwork import Artwork


class FakeFetcher:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, url, api_key):
        self.calls.append((url, api_key))
        for needle, reply in self.replies.items():
            if needle in url:
                return json.dumps(reply).encode()
        return json.dumps({"success": True, "data": []}).encode()


def test_find_game_returns_first_match():
    fetcher = FakeFetcher({"search/autocomplete": {"success": True, "data": [
        {"id": 5148, "name": "Animal Well"}, {"id": 9999, "name": "Animal Well 2"}]}})
    art = Artwork("klucz", fetcher=fetcher)
    assert art.find_game("Animal Well") == 5148
    assert fetcher.calls[0][1] == "klucz", "klucz musi lecieć do API"


def test_find_game_without_results_returns_none():
    art = Artwork("klucz", fetcher=FakeFetcher({}))
    assert art.find_game("Nieistniejąca") is None


def test_assets_for_picks_first_url_per_type():
    fetcher = FakeFetcher({
        "grids/game/5148": {"success": True, "data": [
            {"url": "https://sgdb/grid1.png", "width": 600, "height": 900},
            {"url": "https://sgdb/grid2.png", "width": 600, "height": 900}]},
        "heroes/game/5148": {"success": True, "data": [{"url": "https://sgdb/hero.png"}]},
        "logos/game/5148": {"success": True, "data": [{"url": "https://sgdb/logo.png"}]},
    })
    art = Artwork("klucz", fetcher=fetcher)
    assets = art.assets_for(5148)
    assert assets["grid_p"] == "https://sgdb/grid1.png"
    assert assets["hero"] == "https://sgdb/hero.png"
    assert assets["logo"] == "https://sgdb/logo.png"


def test_no_api_key_means_no_request_at_all():
    fetcher = FakeFetcher({})
    art = Artwork("", fetcher=fetcher)
    assert art.find_game("Animal Well") is None
    assert art.assets_for(5148) == {}
    assert fetcher.calls == [], "bez klucza użytkownika nie wolno pytać SGDB (grozi banem)"


def test_grid_l_and_grid_p_are_distinct_dimensions():
    fetcher = FakeFetcher({
        "grids/game/5148?dimensions=600x900": {"success": True, "data": [
            {"url": "https://sgdb/portrait.png"}]},
        "grids/game/5148?dimensions=920x430": {"success": True, "data": [
            {"url": "https://sgdb/landscape.png"}]},
    })
    assets = Artwork("klucz", fetcher=fetcher).assets_for(5148)
    assert assets["grid_p"] == "https://sgdb/portrait.png"
    assert assets["grid_l"] == "https://sgdb/landscape.png"


def test_as_base64_does_not_leak_key_to_cdn():
    fetcher = FakeFetcher({})
    art = Artwork("klucz", fetcher=fetcher)
    art.as_base64("https://cdn2.steamgriddb.com/grid/abc.png")
    assert fetcher.calls[-1][1] == "", "klucz API nie może lecieć na CDN"
    art.as_base64("https://www.steamgriddb.com/api/v2/grids/game/1")
    assert fetcher.calls[-1][1] == "klucz", "na host API klucz leci normalnie"


def test_default_fetcher_omits_auth_header_without_key():
    from sdsync import artwork
    seen = {}
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    def fake_urlopen(request, timeout=None, context=None):
        seen["headers"] = dict(request.header_items())
        return FakeResponse()
    orig = artwork.urllib.request.urlopen
    artwork.urllib.request.urlopen = fake_urlopen
    try:
        artwork._default_fetcher("https://cdn2.steamgriddb.com/grid/abc.png", "")
    finally:
        artwork.urllib.request.urlopen = orig
    assert not any(k.lower() == "authorization" for k in seen["headers"])


def test_broken_response_is_survivable():
    class Broken:
        def __call__(self, url, api_key):
            return b"to nie jest json"

    art = Artwork("klucz", fetcher=Broken())
    assert art.find_game("Animal Well") is None
    assert art.assets_for(1) == {}


# --- TLS: zmierzone na Decku ---

def test_ca_file_picks_the_first_bundle_that_exists(tmp_path):
    """Python, którego Decky dostarcza wtyczce, ma własne OpenSSL ze skompilowaną
    ścieżką certyfikatów, której na SteamOS nie ma — stąd CERTIFICATE_VERIFY_FAILED
    i CAŁA warstwa sieciowa martwa pod objawem „zły klucz API"."""
    from sdsync import artwork
    brak = str(tmp_path / "nie-ma.pem")
    jest = tmp_path / "cert.pem"
    jest.write_text("-----BEGIN CERTIFICATE-----")
    assert artwork.ca_file((brak, str(jest))) == str(jest)
    assert artwork.ca_file((brak,)) is None


def test_fetcher_verifies_against_the_system_bundle():
    """Bez własnego kontekstu urllib bierze domyślne ścieżki OpenSSL — na Decku
    puste. Ten test pada, gdy ktoś usunie `context=` z wywołania."""
    from sdsync import artwork
    seen = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"] = context
        return FakeResponse()

    orig = artwork.urllib.request.urlopen
    artwork.urllib.request.urlopen = fake_urlopen
    try:
        artwork._default_fetcher("https://www.steamgriddb.com/api/v2/x", "klucz")
    finally:
        artwork.urllib.request.urlopen = orig
    import ssl
    assert isinstance(seen["context"], ssl.SSLContext)
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED, "weryfikacja wyłączona"


def test_fetcher_always_sends_a_user_agent():
    """ZMIERZONE na Decku: SGDB (Cloudflare) odpowiada 403 na domyślne
    „Python-urllib/…" NAWET z poprawnym kluczem — objaw nie do odróżnienia od
    złego klucza."""
    from sdsync import artwork
    seen = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(request, timeout=None, context=None):
        seen["ua"] = request.get_header("User-agent")
        return FakeResponse()

    orig = artwork.urllib.request.urlopen
    artwork.urllib.request.urlopen = fake_urlopen
    try:
        artwork._default_fetcher("https://www.steamgriddb.com/api/v2/x", "klucz")
    finally:
        artwork.urllib.request.urlopen = orig
    assert seen["ua"] and "urllib" not in seen["ua"].lower()
