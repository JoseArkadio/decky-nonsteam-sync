import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata

FIELDS = {
    "title": "",
    "folder": "",
    "card_label": "",
    # Czym gra jeździ: "card" = wymienną kartą (transport fizyczny),
    # "disk" = gra siedzi na dysku konsoli i nośnikiem jest katalog lokalny,
    # a między urządzeniami przewozi ją chmura. Domyślnie "card", więc
    # istniejące wpisy nie zmieniają zachowania.
    "carrier": "card",
    "exe_abs": "",           # aktualna ścieżka (zależy od punktu montowania)
    "exe_rel": "",           # ścieżka względem karty — pozwala odtworzyć exe_abs
    "appid": None,
    "conflict": False,
    "proton": None,
    "steamworks_neutralized": False,
    "artwork_done": False,
    "playtime_seconds": 0,
    # ostatnia znana suma ze WSZYSTKICH urządzeń — bez karty plik wymiany jest
    # nieczytelny, a liczba na kafelku nie może wtedy maleć
    "playtime_total_seen": 0,    # czas gry NA TYM urządzeniu; sumę wozi karta
    "pending_push": False,   # wysyłka odłożona: zamek był zajęty albo chmura padła
    "last_push_ts": None,
    "last_backup_ts": None,
    # {etykieta karty: `when` kopii, którą na tej karcie widzieliśmy ostatnio}.
    # Per karta, bo ta sama gra może leżeć na dwóch kartach i każda ma swoją
    # linię zapisów. `when` jest TOŻSAMOŚCIĄ kopii, nie datą — porównujemy je
    # tylko na równość, więc rozjechane zegary urządzeń nic tu nie psują.
    # ponytail: kluczem jest etykieta karty, bo tylko ona jest wypełniana;
    # dwie karty o tej samej etykiecie zlałyby się w jedną (i tak samo zlałyby
    # się ścieżki gier), więc na to czekamy z card_uuid.
    "card_seen": {},
    # Gra w sklepie Steama wskazana RĘCZNIE — bije appid z bazy Ludusaviego.
    # ZMIERZONE: baza wiąże „Grand Theft Auto V" z 271590 (Legacy), a na karcie leży
    # Enhanced (3240220), więc opis i ocena były cudzej gry. None = bierzemy z bazy.
    "steam_appid": None,
    "excluded": False,
    # Baza Ludusavi nie zna tego tytułu, więc gra nie ma obsługi zapisów.
    # ZMIERZONE: jedna taka gra na liście wywalała ZBIORCZE wywołanie Ludusavi,
    # a przez to wszystkie gry wychodziły jako „nie wiem", czyli konflikt.
    # Flaga trzyma je poza wywołaniami i zamienia 12 widmowych konfliktów
    # w jeden konkretny problem przy jednej grze. Czyści ją zmiana tytułu.
    "ludusavi_unknown": False,
}


# Litery, których NFKD nie rozkłada (nie mają znaku łączącego) — ręcznie na ASCII.
# ponytail: tylko europejskie przypadki, które realnie trafiają w tytułach gier;
# gdy dojdzie ich więcej, zamień na tabelę z pakietu unidecode.
_ASCII_FALLBACK = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "æ": "ae", "Æ": "Ae",
    "œ": "oe", "Œ": "Oe", "ß": "ss",
})

# Jeden zamek na proces, nie na instancję: Registry jest tworzony na każde wywołanie RPC
# (main.Plugin._registry), a wszystkie te instancje wskazują TEN SAM plik. Zamek na
# instancji nie chroniłby przed niczym.
_WRITE_LOCK = threading.RLock()


def title_key(title: str) -> str:
    """Klucz rejestru: transliteracja do ASCII, spacje na "-".

    Pusty tytuł daje pusty klucz (upsert go odrzuca). Tytuł bez ani jednej
    litery łacińskiej (np. „原神") daje stabilny klucz zastępczy z skrótu —
    nigdy pusty, bo pusty wywalał backend.
    """
    if not title or not title.strip():
        return ""
    normalized = unicodedata.normalize("NFKD", title.strip().translate(_ASCII_FALLBACK))
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", "", stripped.lower())
    key = re.sub(r"\s+", "-", cleaned).strip("-")
    if key:
        return key
    seed = unicodedata.normalize("NFKC", title.strip()).lower().encode("utf-8")
    return "t-" + hashlib.sha1(seed).hexdigest()[:12]


class Registry:
    def __init__(self, path: str):
        self.path = path

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            # Uszkodzony rejestr to NIE to samo co brak rejestru. Ciche {} kazałoby
            # najbliższemu upsert utrwalić pustkę i skasować wszystkie appid, flagi
            # konfliktów oraz steamworks_neutralized — czyli odebrać nam czym cofnąć
            # zmiany w plikach gier. Odkładamy plik obok, żeby dało się go odzyskać.
            try:
                os.replace(self.path, self.path + ".broken")
            except OSError:
                pass
            return {}

    def _write(self, data: dict) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        # Unikatowa nazwa pliku tymczasowego jest obowiązkowa: przy stałej ".tmp" dwóch
        # piszących otwiera ten sam plik, drugi go obcina, pierwszy dopisuje do i-węzła,
        # który jest już games.json — i powstaje niepoprawny JSON.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".games-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _blank(self, key: str) -> dict:
        rec = {"title_key": key}
        for field, default in FIELDS.items():
            # kopia wartości zmiennych: wspólny słownik znaczyłby, że znacznik jednej
            # gry pojawia się przy wszystkich pozostałych
            rec[field] = dict(default) if isinstance(default, dict) else default
        return rec

    def all(self) -> list:
        return list(self._read().values())

    def get(self, key: str):
        return self._read().get(key)

    def upsert(self, record: dict) -> dict:
        title = (record.get("title") or "").strip()
        key = record.get("title_key") or title_key(title)
        if not key:
            raise ValueError("rekord musi mieć title albo title_key")
        # read-modify-write musi być niepodzielne: między _read a _write inny wątek
        # zdążyłby zapisać swoją, starszą kopię całego pliku
        with _WRITE_LOCK:
            data = self._read()
            merged = data.get(key) or self._blank(key)
            for field, value in record.items():
                if value is not None or field not in merged:
                    merged[field] = value
            if title:
                merged["title"] = title
            # Tytul przycinamy ZAWSZE, nie tylko gdy przyszedl niepusty: _filter
            # w warstwie zapisow i tak przycina, wiec odpowiedz Ludusavi wraca pod
            # kluczem przycietym i rekord ze spacja nigdy by sie nie dopasowal.
            merged["title"] = (merged.get("title") or "").strip()
            # bez tytułu Ludusavi dostałby wywołanie bez nazwy gry — odrzucamy przed zapisem
            if not (merged.get("title") or "").strip():
                raise ValueError("rekord musi mieć niepusty title")
            merged["title_key"] = key
            data[key] = merged
            self._write(data)
            return merged

    def set_fields(self, key: str, **fields) -> dict:
        # read-modify-write musi być niepodzielne: między _read a _write inny wątek
        # zdążyłby zapisać swoją, starszą kopię całego pliku
        with _WRITE_LOCK:
            data = self._read()
            if key not in data:
                raise KeyError(key)
            data[key].update(fields)
            self._write(data)
            return data[key]

    def rename(self, key: str, title: str) -> tuple:
        """Przeniesienie rekordu pod klucz nowego tytułu. Zwraca `(rekord, problem)`.

        Niepodzielnie, pod tym samym zamkiem co reszta zapisów: klucz jest TOŻSAMOŚCIĄ
        gry, więc chwila, w której wpis istnieje pod obiema nazwami (albo pod żadną),
        znaczy grę z dwoma katalogami zapisów albo bez żadnego.

        `problem` to kod, nie zdanie: „nie ma takiego wpisu" i „nowy klucz jest zajęty"
        to dwie różne wiadomości dla człowieka, a jedno `None` na oba kazałoby
        wywołującemu zgadywać, którą pokazać.
        """
        nowy = title_key((title or "").strip())
        if not nowy:
            return None, "empty"
        with _WRITE_LOCK:
            data = self._read()
            record = data.get(key)
            if record is None:
                return None, "missing"
            if nowy != key and nowy in data:
                return None, "taken"
            record = dict(record)
            record["title"] = title.strip()
            record["title_key"] = nowy
            # nowy tytuł = nowa szansa dla Ludusaviego; bez tego poprawiona nazwa
            # nadal wypadałaby z wywołań i nie dałoby się wyjść z tego stanu
            record["ludusavi_unknown"] = False
            data.pop(key, None)
            data[nowy] = record
            self._write(data)
            return record, None

    def remove(self, key: str) -> bool:
        # read-modify-write musi być niepodzielne: między _read a _write inny wątek
        # zdążyłby zapisać swoją, starszą kopię całego pliku
        with _WRITE_LOCK:
            data = self._read()
            if key not in data:
                return False
            del data[key]
            self._write(data)
            return True
