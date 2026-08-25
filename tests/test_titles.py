import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.titles import canonical_title, search_titles


def test_returns_best_match_by_score():
    # Kolejność celowo odwrotna do oczekiwanego wyniku: test, w którym zwycięzca jest
    # pierwszy, przechodzi także wtedy, gdy kod bierze pierwszy element zamiast sortować.
    games = {
        "The Witcher 3: Wild Hunt - Blood and Wine": {"score": 0.41},
        "The Witcher 3: Wild Hunt": {"score": 0.83},
    }
    payload = json.dumps({"games": games})

    def runner(argv):
        assert "find" in argv and "--api" in argv and "--normalized" in argv
        assert argv[-1] == "The Witcher 3 Wild Hunt"
        return 0, payload

    result = canonical_title("The Witcher 3 Wild Hunt", runner=runner)
    assert result["title"] == "The Witcher 3: Wild Hunt"
    assert result["candidates"][0] == "The Witcher 3: Wild Hunt"


def test_no_match_returns_none_with_empty_candidates():
    def runner(argv):
        return 0, json.dumps({"games": {}})

    result = canonical_title("Cyberpunk2077", runner=runner)
    assert result["title"] is None
    assert result["candidates"] == []


def test_broken_output_is_survivable():
    def runner(argv):
        return 1, "to nie jest json"

    result = canonical_title("Hades", runner=runner)
    assert result["title"] is None
    assert result["candidates"] == []


# ---------- szukanie po bazie Ludusaviego (manifest, lokalnie) ----------
#
# ZMIERZONE na Decku 2026-08-24, i to jest powód istnienia tej funkcji: `ludusavi find`
# nie umie szukać po fragmencie. `--normalized "Marvel Tokon"` zwraca
# `unknownGames`, bo nie zdejmuje makronu z „Tōkon", a `--fuzzy --multiple "Marvel
# Tokon"` odpowiada „Mall Tycoon", „Marco Polo", „Marble Run" — nigdy właściwą grą.
# Przy „binding of isaac" fuzzy daje jedno trafienie: „Raining Coins".
# Manifest leży na dysku (17 MB, 53019 tytułów), więc przejście po nim kosztuje
# 0,33 s wobec 2,0 s startu procesu Ludusaviego przez flatpak.

MANIFEST = os.path.join(os.path.dirname(__file__), "fixtures", "ludusavi_manifest_WYCINEK.yaml")


def test_search_finds_title_by_fragment():
    assert search_titles("isaac", path=MANIFEST) == ["The Binding of Isaac: Rebirth"]


def test_search_ignores_diacritics_in_both_directions():
    # Sedno sprawy: użytkownik nie ma jak wpisać „ō" na klawiaturze ekranowej Steama,
    # a bez tego znaku Ludusavi mówi „nie znam takiej gry".
    assert search_titles("marvel tokon", path=MANIFEST) == ["Marvel Tōkon: Fighting Souls"]
    assert search_titles("Tōkon", path=MANIFEST) == ["Marvel Tōkon: Fighting Souls"]


def test_search_ignores_punctuation_and_case():
    # „The Binding of Isaac Rebirth" bez dwukropka to dokładnie to, co użytkownik wpisał
    # na urządzeniu i przez co gra nie miała obsługi zapisów.
    assert search_titles("the binding of isaac rebirth", path=MANIFEST) == [
        "The Binding of Isaac: Rebirth"]


def test_search_ranks_prefix_hits_before_hits_in_the_middle():
    # Kolejność oczekiwana jest tu ODWROTNA do alfabetycznej i to jest cały sens testu:
    # pierwsza wersja („marvel" → dwa tytuły na M) przechodziła także po zamianie
    # sortowania na alfabetyczne, czyli nie mierzyła niczego. „an" wypada w tym wycinku
    # na pozycji 0 („Animal Well"), 12 („Amazon: …") i 17 („1001 Jigsaw … And …").
    assert search_titles("an", path=MANIFEST) == [
        "Animal Well",
        "Amazon: Guardians of Eden",
        "1001 Jigsaw Castles And Palaces",
    ]


def test_search_honours_the_limit():
    assert len(search_titles("e", path=MANIFEST, limit=3)) == 3


def test_search_of_empty_text_returns_nothing():
    # Pusty fragment pasuje do KAŻDEGO tytułu — wysypanie 53019 pozycji na ekran
    # wyglądałoby jak awaria, a nie jak „nie wpisałeś nic".
    assert search_titles("   ", path=MANIFEST) == []


def test_search_survives_missing_manifest():
    assert search_titles("isaac", path="/nie/ma/takiego/manifestu.yaml") == []


def test_search_reads_the_manifest_once(tmp_path):
    kopia = tmp_path / "manifest.yaml"
    kopia.write_bytes(open(MANIFEST, "rb").read())
    otwarcia = []
    prawdziwy_open = open

    class Licznik:
        def __call__(self, *args, **kwargs):
            otwarcia.append(args[0])
            return prawdziwy_open(*args, **kwargs)

    import builtins
    builtins.open = Licznik()
    try:
        search_titles("marvel", path=str(kopia))
    finally:
        builtins.open = prawdziwy_open
    assert otwarcia == [str(kopia)]
