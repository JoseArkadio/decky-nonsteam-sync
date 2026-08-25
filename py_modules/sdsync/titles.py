import json
import os
import shutil
import subprocess
import unicodedata

FLATPAK_ID = "com.github.mtkennerly.ludusavi"


def ludusavi_command():
    """Preferujemy flatpak — tam użytkownik ma skonfigurowaną chmurę."""
    if shutil.which("flatpak"):
        probe = subprocess.run(["flatpak", "info", FLATPAK_ID],
                               capture_output=True)
        if probe.returncode == 0:
            return ["flatpak", "run", FLATPAK_ID]
    standalone = os.path.expanduser("~/sdsync/bin/ludusavi")
    if os.access(standalone, os.X_OK):
        return [standalone]
    return None


def _default_runner(argv: list):
    # stdin=DEVNULL jak w saves._default_runner: ZMIERZONE, że Ludusavi widzące cudze
    # wejście czeka na nie (11 minut). Ta trasa jest wołana przy każdym skanie karty.
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=90,
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout


def canonical_title(folder_name: str, runner=None) -> dict:
    empty = {"title": None, "candidates": [], "score": 0.0}
    if runner is None:
        cmd = ludusavi_command()
        if not cmd:
            return empty
        runner = _default_runner
        argv = cmd + ["--try-manifest-update", "find", "--api", "--normalized", folder_name]
    else:
        argv = ["find", "--api", "--normalized", folder_name]

    try:
        _, stdout = runner(argv)
        games = json.loads(stdout).get("games", {})
    except Exception:
        return empty

    if not games:
        return empty

    ranked = sorted(games.items(), key=lambda kv: (kv[1] or {}).get("score", 0), reverse=True)
    return {
        "title": ranked[0][0],
        "candidates": [name for name, _ in ranked],
        "score": (ranked[0][1] or {}).get("score", 0.0),
    }


# ---------------------------------------------------------------------------
# Manifest Ludusaviego jako plik na dysku.
#
# Ten sam plik czyta `metadata.steam_ids()` po identyfikator sklepowy — mieszka tu,
# bo jest to spis TYTUŁÓW, a tytuł jest w tym projekcie tożsamością gry.

MANIFEST_CANDIDATES = (
    "~/.var/app/com.github.mtkennerly.ludusavi/config/ludusavi/manifest.yaml",
    "~/.config/ludusavi/manifest.yaml",
)


def manifest_path(candidates=MANIFEST_CANDIDATES):
    for path in candidates:
        rozwinieta = os.path.expanduser(path)
        if os.path.isfile(rozwinieta):
            return rozwinieta
    return None


def title_of(linia: str) -> str:
    """Tytuł z linii manifestu na poziomie zerowym (`Animal Well:`)."""
    tytul = linia.rstrip("\n").rstrip()
    if tytul.endswith(":"):
        tytul = tytul[:-1]
    if len(tytul) > 1 and tytul[0] == '"' and tytul[-1] == '"':
        tytul = tytul[1:-1]      # tytuły z dwukropkiem są w manifeście cytowane
    return tytul


def fold(text: str) -> str:
    """Tytuł sprowadzony do tego, co człowiek jest w stanie wpisać: bez wielkości
    liter, bez znaków diakrytycznych, bez interpunkcji i bez spacji.

    ZMIERZONE na Decku, i to jest cały powód: „Marvel Tōkon: Fighting Souls" ma makron,
    którego nie ma na klawiaturze ekranowej Steama, a `ludusavi find --normalized`
    tego znaku NIE zdejmuje — odpowiada `unknownGames` na „Marvel Tokon". Po złożeniu
    NFKD makron jest osobnym znakiem łączącym i `isalnum()` go odrzuca, więc obie
    strony schodzą do „marveltokonfightingsouls".
    """
    return "".join(z for z in unicodedata.normalize("NFKD", text.lower()) if z.isalnum())


def search_titles(text: str, path=None, limit: int = 20) -> list:
    """Tytuły z bazy Ludusaviego zawierające wpisany fragment.

    Osobna droga od `canonical_title()` i to nie jest dublowanie: tamta odpowiada na
    „jak baza nazywa TĘ grę" (jedno wywołanie, dokładne dopasowanie), ta na „co baza
    w ogóle ma pod tym słowem". ZMIERZONE na Decku 2026-08-24, dlaczego nie da się
    tego zrobić Ludusavim:

        find --api --normalized "Marvel Tokon"        → {"errors":{"unknownGames":[…]}}
        find --api --fuzzy --multiple "Marvel Tokon"  → Mall Tycoon, Marco Polo, …
        find --api --fuzzy --multiple "binding of isaac" → Raining Coins

    czyli dla fragmentu wpisanego z klawiatury ekranowej jedno nie znajduje nic,
    a drugie znajduje cudze gry. Przejście po manifeście kosztuje 0,33 s wobec 2,0 s
    samego startu Ludusaviego przez flatpak — więc jest i lepsze, i tańsze, i działa
    bez sieci.

    Pusty fragment zwraca PUSTO, a nie wszystko: 53019 tytułów na ekranie wyglądałoby
    jak awaria, nie jak „nie wpisałeś nic".
    """
    szukane = fold(text or "")
    if not szukane:
        return []
    if path is None:
        path = manifest_path()
    if not path:
        return []
    trafienia = []
    try:
        with open(path, encoding="utf-8") as plik:
            for linia in plik:
                # tylko klucze na poziomie zerowym; reszta pliku to bloki gier
                if linia[:1] in (" ", "\t", "#", "\n", "-"):
                    continue
                tytul = title_of(linia)
                gdzie = fold(tytul).find(szukane)
                if gdzie >= 0:
                    # od czoła przed środkiem, krótsze przed dłuższym: wpisane „marvel"
                    # ma dać najpierw „Marvel …", a nie „… vs. Capcom …"
                    trafienia.append((gdzie, len(tytul), tytul))
    except OSError:
        return []
    trafienia.sort()
    return [tytul for _, _, tytul in trafienia[:limit]]
