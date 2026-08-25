"""Testy katalogów tłumaczeń (plugin/src/i18n/*.json).

Czytamy je Pythonem, bo w projekcie nie ma runnera JS, a to są pliki DANYCH —
dokładanie zależności testowej dla frontendu, żeby sprawdzić dwa JSON-y, byłoby
drugą drogą do utrzymania.

Sedno: napis, którego brakuje, musi paść TUTAJ, a nie u użytkownika. Kompletności
samego frontendu ten plik NIE pilnuje (grep po polskich znakach przepuszcza „Karta"
i „Zapisy", więc dawałby zielone światło przy połowie roboty niezrobionej) — pilnuje
spójności katalogów między sobą i z kodami backendu.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.messages import CODES, fields

I18N = os.path.join(os.path.dirname(__file__), "..", "src", "i18n")
FRONT_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
FRONT_PREFIXES = ("ui.", "qa.", "status.", "stage.", "log.", "lang.")
# Tylko `fromBackend` wolno dotykać kodów backendu: `en.json` celowo nie ma haseł
# `err.*` (zapas jest w CODES, nie w katalogu), więc `t("err.xxx")` pod `en` oddaje
# goły klucz zamiast zdania — a nic w typach tego nie złapie, bo `t()` przyjmuje
# dowolny string.
ERR_CALL = re.compile(r"""t\(\s*['"`]err\.""")


def _catalogs():
    out = {}
    for name in sorted(os.listdir(I18N)):
        if name.endswith(".json"):
            with open(os.path.join(I18N, name), encoding="utf-8") as fh:
                out[name] = json.load(fh)
    return out


def _texts(value):
    """Napis albo obiekt form liczby mnogiej → lista napisów."""
    return [value] if isinstance(value, str) else list(value.values())


def _placeholders(value):
    found = set()
    for text in _texts(value):
        found |= set(re.findall(r"\{(\w+)\}", text))
    return found


def test_sa_przynajmniej_dwa_katalogi():
    cats = _catalogs()
    assert {"pl.json", "en.json"} <= set(cats), sorted(cats)


def test_kazdy_kod_backendu_ma_polskie_haslo():
    pl = _catalogs()["pl.json"]
    braki = sorted(code for code in CODES if "err.%s" % code not in pl)
    assert not braki, "kody bez tłumaczenia w pl.json: %s" % braki


def test_kazde_haslo_err_ma_swoj_kod():
    """Sierota po przemianowanym kodzie to napis, którego nikt już nie pokaże."""
    for name, cat in _catalogs().items():
        sieroty = sorted(k for k in cat
                         if k.startswith("err.") and k[len("err."):] not in CODES)
        assert not sieroty, "%s: hasła err.* bez kodu w CODES: %s" % (name, sieroty)


def test_katalogi_maja_te_same_klucze_frontendu():
    """`err.*` są z tego testu WYŁĄCZONE i to nie jest luka: dla nich istnieje
    angielski zapas w CODES, więc brak hasła daje zdanie, nie surowy klucz.
    Gdyby test wymagał ich wszędzie, en.json musiałby duplikować całe CODES."""
    cats = _catalogs()
    fronts = {name: {k for k in cat if not k.startswith("err.")}
              for name, cat in cats.items()}
    wzor = fronts["pl.json"]
    for name, keys in fronts.items():
        assert keys == wzor, ("%s rozjechał się z pl.json — brakuje: %s, nadmiar: %s"
                              % (name, sorted(wzor - keys), sorted(keys - wzor)))


def test_klucze_frontendu_maja_znany_prefiks():
    """Bez tego przestrzeń kluczy rozjedzie się w dowolność i nie da się już
    powiedzieć, co jest napisem frontendu, a co kodem backendu."""
    for name, cat in _catalogs().items():
        zle = sorted(k for k in cat
                     if not k.startswith("err.") and not k.startswith(FRONT_PREFIXES))
        assert not zle, "%s: klucze bez znanego prefiksu: %s" % (name, zle)


def test_kazdy_katalog_mowi_jak_sie_nazywa():
    """Selektor języka pokazuje nazwę WŁASNĄ („Deutsch", nie „German"), więc nowy
    plik języka musi opisać się sam — inaczej dołożenie języka wymaga zmian w kodzie."""
    for name, cat in _catalogs().items():
        own = cat.get("lang.own_name")
        assert isinstance(own, str) and own.strip(), name


def test_haslo_nie_uzywa_pola_ktorego_kod_nie_przysyla():
    """Tłumaczenie z `{tytul}` przy kodzie przysyłającym `title` renderuje dziurę.
    Wolno POMINĄĆ pole, nie wolno WYMYŚLIĆ."""
    for name, cat in _catalogs().items():
        for key, value in cat.items():
            if not key.startswith("err."):
                continue
            template = CODES.get(key[len("err."):])
            if template is None:
                continue
            nadmiar = _placeholders(value) - fields(template)
            assert not nadmiar, "%s / %s: pola nieznane kodowi: %s" % (
                name, key, sorted(nadmiar))


def test_katalogi_maja_te_same_zmienne_w_kluczach():
    """`err.*` są z tego testu WYŁĄCZONE i to nie jest luka: dla nich istnieje
    angielski zapas w CODES (nie w en.json), więc każde `{placeholder}` musi
    odpowiadać kodowi — to pilnuje osobny test.

    Dla kluczy frontendu (nie err.*) wszystkie katalogi muszą mieć DOKŁADNIE
    ten sam zbiór `{zmiennych}`, bo niezgodność renderuje dosłownie w interfejsie."""
    cats = _catalogs()
    pl_ref = cats["pl.json"]

    for key, pl_value in pl_ref.items():
        if key.startswith("err."):
            continue
        pl_pholds = _placeholders(pl_value)

        for name, cat in cats.items():
            if name == "pl.json":
                continue
            if key not in cat:
                continue
            other_pholds = _placeholders(cat[key])

            if pl_pholds != other_pholds:
                missing = pl_pholds - other_pholds
                extra = other_pholds - pl_pholds
                msg = "%s / %s: zmienne się różnią" % (name, key)
                if missing:
                    msg += " — brakuje: %s" % sorted(missing)
                if extra:
                    msg += " — nadmiar: %s" % sorted(extra)
                assert False, msg


def test_formy_liczby_mnogiej_maja_other():
    """`Intl.PluralRules` może zwrócić formę, której dany język nie wypisał —
    `other` jest wtedy zapasem, więc bez niego napis znika."""
    for name, cat in _catalogs().items():
        for key, value in cat.items():
            if isinstance(value, dict):
                assert "other" in value, "%s / %s bez formy `other`" % (name, key)
                assert set(value) <= {"zero", "one", "two", "few", "many", "other"}, (
                    name, key, sorted(value))


def test_zadne_haslo_nie_jest_puste():
    """Puste hasło wygląda w interfejsie jak brak problemu."""
    for name, cat in _catalogs().items():
        for key, value in cat.items():
            for text in _texts(value):
                assert isinstance(text, str) and text.strip(), (name, key)


def test_nikt_nie_woła_t_z_kodem_bledu():
    """Kod błędu idzie WYŁĄCZNIE przez `fromBackend` — ono zna zapas z `CODES`
    dla katalogu, który (jak `en.json`) świadomie nie ma haseł `err.*`. `t()` takiego
    zapasu nie ma i pod nieangielskim/nie-`pl` językiem oddałby goły klucz."""
    winowajcy = []
    for root, _dirs, files in os.walk(FRONT_SRC):
        for name in files:
            if not name.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if ERR_CALL.search(line):
                        winowajcy.append("%s:%d" % (os.path.relpath(path), lineno))
    assert not winowajcy, "t(\"err....\") poza fromBackend: %s" % winowajcy


# ---------- napisy na przyciskach nie mogą się zawijać ----------
#
# ZGŁOSZONE dwa razy z urządzenia: „napisy na przyciskach są za długie i nie mieszczą
# się w jednej linii" (najpierw po angielsku, potem „Wyślij mój zapis"). Zawinięty napis
# nie jest tylko brzydki: robi przycisk o pół wiersza wyższym niż sąsiad w tej samej
# siatce, a nawigacja pada w Steamie jest PRZESTRZENNA — nierówne pudełka psują
# skakanie strzałkami (ta sama rodzina co nagłówki grup w spisie po lewej).
#
# Budżet w znakach, nie w pikselach, i to jest świadome uproszczenie: piksele zależą od
# czcionki i rozdzielczości, a test ma paść na Macu przed wdrożeniem, nie po. Liczby
# poniżej wzięte z POMIARU na Decku (okno 985×616, kolumna treści 595 px):
#   * siatka dwukolumnowa → przycisk ~293 px; „Refresh description" (19 znaków) mieści
#     się w jednej linii, „Point at the Steam store game" (29) zawijało się;
#   * siatka trzykolumnowa w rozjeździe → przycisk ~185 px, czyli ok. dwie trzecie tego.
# Stąd 22 i 14, z zapasem na języki gęstsze od polskiego i angielskiego.
BUDZET_POLOWA = 22
BUDZET_TRZECIA = 14

PRZYCISKI = {
    # siatka akcji na ekranie gier — dwie kolumny
    "ui.sync_this_game": BUDZET_POLOWA,
    "ui.fetch_artwork": BUDZET_POLOWA,
    "ui.fetch_artwork_again": BUDZET_POLOWA,
    "ui.meta.refresh": BUDZET_POLOWA,
    "ui.meta.fetching": BUDZET_POLOWA,
    "ui.pick_exe_button": BUDZET_POLOWA,
    "ui.retitle_button": BUDZET_POLOWA,
    "ui.storepick_button": BUDZET_POLOWA,
    "ui.hide_duplicates": BUDZET_POLOWA,
    "ui.show_hidden": BUDZET_POLOWA,
    # `ui.exclude_toggle_label` NIE jest tu celowo: to etykieta przełącznika, który
    # stoi w osobnym wierszu na całą szerokość karty, więc budżet siatki go nie dotyczy.
    "qa.working": BUDZET_POLOWA,
    # rozjazd zapisów — trzy kolumny, więc ciaśniej
    "ui.conflict.send_mine": BUDZET_TRZECIA,
    "ui.conflict.take_card": BUDZET_TRZECIA,
    "ui.conflict.take_cloud": BUDZET_TRZECIA,
    # karta na ekranie gry Steama — najciaśniejsze miejsce, jakie mamy
    "ui.page.sync": BUDZET_TRZECIA,
    "ui.page.store": BUDZET_TRZECIA,
    "ui.page.details": BUDZET_TRZECIA,
    # nagłówki wiersza faktów o graniu — stoją obok siebie w jednej linii
    "ui.hltb.label": BUDZET_POLOWA,
    "ui.players.label": BUDZET_TRZECIA,
}


def test_napisy_na_przyciskach_mieszcza_sie_w_jednej_linii():
    """Budżet obowiązuje KAŻDY język. Sprawdzenie „wygląda dobrze po polsku" przepuściło
    już dwie wpadki, bo angielski bywa dłuższy (Send mine / Wyślij mój to 9 wobec 11,
    ale Point at the Steam store game / Wskaż grę w sklepie Steam to 29 wobec 24)."""
    za_dlugie = []
    for lang, catalog in _catalogs().items():
        for key, budzet in PRZYCISKI.items():
            napis = catalog.get(key)
            if napis is None:
                continue          # brak hasła łapie osobny test o kompletności
            # `{count}` i `{title}` podstawiane są w locie; do budżetu liczymy sam
            # szkielet plus dwa znaki na liczbę, bo tyle mają realne wartości (1–99)
            widoczne = re.sub(r"\{[a-z_]+\}", "00", str(napis))
            if len(widoczne) > budzet:
                za_dlugie.append("%s/%s: %d > %d (%r)" % (lang, key, len(widoczne), budzet, napis))
    assert not za_dlugie, "napisy na przyciskach zawiną się w dwie linie:\n" + "\n".join(za_dlugie)
