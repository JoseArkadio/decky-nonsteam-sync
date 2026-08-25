"""Czas gry zliczany przez wtyczkę i wożony między urządzeniami na karcie SD.

Dlaczego własny licznik, a nie licznik Steama: Steam trzyma czas skrótów non-Steam
w `localconfig.vdf` i nie ma API do jego ZAPISANIA — a plik nadpisuje przy wyjściu.
SDH-PlayTime rozwiązuje to samo tak samo: mierzy czas sam, a liczbę na kafelku
podmienia w pamięci klienta (patrz `steam.ts`, `patchPlaytime`).

Dlaczego karta, a nie chmura: karta i tak jeździ między urządzeniami — to cała
przesłanka tego projektu. Chmura Ludusavi wozi zapisy, a nie nasze statystyki,
i dokładanie do niej rclone'a dla jednej liczby byłoby drugą drogą do utrzymania.

Każde urządzenie pisze WYŁĄCZNIE swój klucz w mapie, więc scalanie jest sumą i nie
ma tu konfliktów do rozstrzygania (a i tak nie da się grać na dwóch urządzeniach
naraz — gra leży na tej jednej karcie).
"""

import json
import os
import socket

from . import paths

DIR_NAME = paths.CARD_DIR_NAME
FILE_NAME = "playtime.json"

# ZMIERZONE na Decku: hostname to `steamdeck` — domyślna nazwa obrazu SteamOS, więc
# Deck i Machine z pudełka nazywają się TAK SAMO. Sam hostname jako klucz znaczy, że
# jedno urządzenie nadpisuje czas drugiego przy każdej sesji. machine-id jest unikatowy
# na instalację; hostname zostaje z przodu, żeby plik dało się czytać po ludzku.
MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def device_id() -> str:
    """Klucz urządzenia w pliku na karcie: `hostname-xxxxxxxx`.

    Pusty hostname dostaje nazwę zastępczą — pusty klucz zlewałby ze sobą liczby
    dwóch urządzeń. Brak machine-id daje sam hostname (lepiej czytelnie niż wcale).
    """
    try:
        name = (socket.gethostname() or "").strip()
    except OSError:
        name = ""
    name = name or "nieznane-urzadzenie"
    for path in MACHINE_ID_PATHS:
        try:
            with open(path, encoding="utf-8") as handle:
                machine = handle.read().strip()
        except OSError:
            continue
        if machine:
            return "%s-%s" % (name, machine[:8])
    return name


def file_path(mount: str) -> str:
    return os.path.join(mount, DIR_NAME, FILE_NAME)


def read(mount: str) -> dict:
    """{title_key: {urządzenie: sekundy}} z karty.

    Brak pliku, uszkodzony JSON i nieczytelna karta dają {}. Czas gry to statystyka,
    nie zapis użytkownika: jego brak nie może wywalić ekranu gier ani zatrzymać
    synchronizacji zapisów.
    """
    try:
        with open(file_path(mount), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, per_device in data.items():
        if isinstance(per_device, dict):
            out[str(key)] = {str(dev): _seconds(value)
                             for dev, value in per_device.items()}
    return out


def _seconds(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def publish(mount: str, device: str, mine: dict) -> dict:
    """Wpisuje NASZE sumy do pliku na karcie i zwraca stan po scaleniu.

    Cudze klucze zostają nietknięte. Zwraca też scalony stan, gdy zapis się nie
    uda (karta tylko do odczytu, wyjęta w trakcie) — licznik tego urządzenia
    żyje w rejestrze i tak, a karta jest tylko nośnikiem wymiany.
    """
    merged = read(mount)
    for key, seconds in (mine or {}).items():
        merged.setdefault(str(key), {})[device] = _seconds(seconds)
    path = file_path(mount)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)  # karta bywa wyjęta w trakcie — nigdy plik w połowie
    except OSError:
        pass
    return merged


def totals(card: dict, mine: dict, device: str) -> dict:
    """{title_key: sekundy} — suma po wszystkich urządzeniach.

    Nasza liczba z rejestru BIJE to, co leży na karcie: kartę mogło nie być w
    urządzeniu, gdy kończyliśmy grę, więc plik bywa starszy niż rejestr.
    """
    keys = set(card) | set(mine or {})
    out = {}
    for key in keys:
        per_device = dict(card.get(key) or {})
        per_device[device] = _seconds((mine or {}).get(key, per_device.get(device, 0)))
        out[key] = sum(per_device.values())
    return out


def per_device(card: dict, mine: dict, device: str, key: str) -> dict:
    """Rozbicie na urządzenia dla jednej gry — do pokazania „u kogo ile"."""
    out = dict(card.get(key) or {})
    out[device] = _seconds((mine or {}).get(key, out.get(device, 0)))
    return out


def merge_remembered(totals: dict, seen: dict) -> dict:
    """Suma po urządzeniach nie może MALEĆ, gdy karty nie ma w czytniku.

    Plik wymiany żyje na karcie, więc bez karty `totals()` zna tylko sekundy tego
    urządzenia. ZMIERZONE na Decku: kafelek spadł wtedy z 7,1 min (322 s Deck +
    106 s Machine) na 5,4 min — czyli wyglądało to jak „czas gry się nie sumuje",
    choć suma była poprawna, dopóki karta była w środku.

    `max` jest tu bezpieczne, bo liczby czasu gry tylko rosną. Zapamiętana wartość
    jest DOLNĄ granicą, nigdy nie przebija świeższej z karty.

    ponytail: gdyby ktoś ręcznie skasował czas na drugim urządzeniu, zapamiętana
    suma zostanie za wysoka do najbliższego wzrostu. To statystyka, nie zapis gracza.
    """
    return {key: max(_seconds(value), _seconds((seen or {}).get(key, 0)))
            for key, value in (totals or {}).items()}
