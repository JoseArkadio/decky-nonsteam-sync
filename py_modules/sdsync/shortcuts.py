"""Odczyt `shortcuts.vdf` Steama — po to, żeby PRZEJĄĆ istniejący kafelek gry
zamiast robić drugi.

Powód jest praktyczny i kosztowny: użytkownik ma kafelki dodane ręcznie, zanim ta
wtyczka istniała, i w ICH prefiksach siedzi jego postęp. Nowy kafelek to nowy appid,
czyli nowy prefiks Protona — stary zapis zostaje na boku i nikt go już nie wozi ani
nie chroni.

Dopasowanie idzie po `os.path.realpath` pliku wykonywalnego, nigdy po nazwie:
ZMIERZONE na Decku, `/run/media/SD256` jest symlinkiem na `/run/media/deck/SD256`,
więc ta sama gra miała kafelki różniące się tylko ścieżką (007 First Light: trzy).
Nazwa jest tu bezużyteczna jako klucz — dwie różne gry mogą nazywać się tak samo,
a przejęcie cudzego prefiksu znaczy kopiowanie i przywracanie zapisów NIE TEJ gry.

Plik czytamy wyłącznie do odczytu. Zapisu do `shortcuts.vdf` nie robimy nigdy:
kafelki zakłada Steam swoim API (patrz `steam.ts`), a ten plik nadpisuje sam.
"""

import glob
import os
import struct

STEAM_ROOT = os.path.expanduser("~/.local/share/Steam")

# typy pól binarnego VDF (zmierzone na żywym pliku z Decka)
_MAP, _STR, _INT, _END = 0, 1, 2, 8


def config_paths(root: str = None) -> list:
    """Wszystkie `userdata/*/config/shortcuts.vdf`. Także martwe katalogi kont —
    kafelek z martwego pliku odrzuca warstwa wyżej (frontend sprawdza, czy Steam
    zna ten appid), a pominięcie ich tutaj gubiłoby żywe konta po migracji."""
    return sorted(glob.glob(os.path.join(root or STEAM_ROOT, "userdata", "*",
                                         "config", "shortcuts.vdf")))


def parse(data: bytes) -> list:
    """[{appid, name, exe}] z binarnego VDF. Uszkodzony plik daje pustą listę —
    to podpowiedź do dopasowania, a nie dane, na których stoi zapis gracza."""
    entries = []
    try:
        # od ZERA: bajt 0 to typ mapy zewnętrznej ("shortcuts"), a jej klucz zaczyna
        # się dopiero za nim — wersja liczona od pierwszego \x00 gubiła cały plik
        _read_map(data, 0, {}, entries)
    except (ValueError, IndexError, struct.error, UnicodeDecodeError):
        pass
    return [e for e in entries if e.get("appid")]


def _read_map(data: bytes, index: int, into: dict, entries: list) -> int:
    while index < len(data):
        kind = data[index]
        index += 1
        if kind == _END:
            break
        stop = data.index(b"\x00", index)
        key = data[index:stop].decode("utf-8", "replace").lower()
        index = stop + 1
        if kind == _MAP:
            nested = {}
            index = _read_map(data, index, nested, entries)
            if "appid" in nested:
                entries.append({"appid": nested["appid"],
                                "name": nested.get("appname") or "",
                                "exe": _unquote(nested.get("exe") or "")})
        elif kind == _STR:
            stop = data.index(b"\x00", index)
            into[key] = data[index:stop].decode("utf-8", "replace")
            index = stop + 1
        elif kind == _INT:
            into[key] = struct.unpack("<I", data[index:index + 4])[0]
            index += 4
        else:
            break  # nieznany typ = dalej nie wiemy, gdzie jesteśmy
    return index


def _unquote(value: str) -> str:
    """Steam trzyma ścieżkę w cudzysłowach. Bez ich zdjęcia realpath nigdy nie
    trafi w plik i dopasowanie milczy — a milczenie tutaj znaczy „zrób drugi
    kafelek", czyli dokładnie to, czego unikamy."""
    return (value or "").strip().strip('"')


def read(path: str) -> list:
    try:
        with open(path, "rb") as handle:
            return parse(handle.read())
    except OSError:
        return []


def find_by_exe(entries, exe_abs: str) -> list:
    """Kafelki wskazujące DOKŁADNIE ten plik wykonywalny (po realpath)."""
    target = _real(exe_abs)
    if not target:
        return []
    out, seen = [], set()
    for entry in entries or []:
        if _real(entry.get("exe")) != target or entry["appid"] in seen:
            continue
        seen.add(entry["appid"])
        out.append({"appid": entry["appid"], "name": entry.get("name") or ""})
    return out


def _real(path: str) -> str:
    text = (path or "").strip()
    return os.path.realpath(text) if text else ""


def matches_for(exe_abs: str, root: str = None) -> list:
    """Istniejące kafelki tej gry ze WSZYSTKICH kont Steama, bez powtórzeń."""
    if not (exe_abs or "").strip():
        return []
    out, seen = [], set()
    for path in config_paths(root):
        for match in find_by_exe(read(path), exe_abs):
            if match["appid"] in seen:
                continue
            seen.add(match["appid"])
            out.append(match)
    return out
