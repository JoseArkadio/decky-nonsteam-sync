import json
import os
import time


class EventLog:
    """Log zdarzeń: jeden JSON na linię. Jedyne okno diagnostyczne użytkownika,
    więc żadna uszkodzona linia ani błędny argument nie może go zgasić."""

    def __init__(self, path: str, limit: int = 500):
        self.path = path
        self.limit = limit

    def add(self, kind: str, message) -> None:
        """`message` to słownik z `messages.msg()` albo goły napis.

        Napis zostaje napisem BEZ pola `code`: tak przysyła RPC `log_add` z frontendu
        i tak wyglądają wpisy sprzed wielojęzyczności. Interfejs rozpoznaje brak `code`
        i pokazuje wtedy `message` — dzięki temu historia logu na urządzeniu
        użytkownika nie wymaga migracji pliku.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        entry = {"ts": time.time(), "kind": kind}
        if isinstance(message, dict):
            # `or ""` / `or {}`: uszkodzony słownik nie może wywalić jedynego okna
            # diagnostycznego użytkownika
            entry["code"] = str(message.get("code") or "")
            entry["params"] = message.get("params") or {}
            entry["message"] = str(message.get("message") or "")
        else:
            entry["message"] = str(message)
        with open(self.path, "a", encoding="utf-8") as fh:
            # `default=str`: parametry przychodzą z rejestru i z treści wyjątków, więc
            # kiedyś trafi się wartość, której JSON nie zna (ZMIERZONE: `pathlib.Path`
            # rzuca „Object of type PosixPath is not JSON serializable"). Wyjątek stąd
            # byłby DRUGĄ awarią, wywołaną przez zapisywanie pierwszej — a docstring tej
            # klasy obiecuje, że błędny argument jej nie zgasi. `str` ZACHOWUJE treść;
            # zapasowy wpis by ją zgubił.
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        lines = self._lines()
        if len(lines) > self.limit:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-self.limit:])

    def _lines(self) -> list:
        try:
            # errors="replace": jeden uszkodzony bajt (przerwany zapis, obcięcie pliku)
            # nie może zabrać całego logu — taka linia po prostu nie sparsuje się na JSON
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                return fh.readlines()
        except OSError:
            return []

    def tail(self, count: int = 50) -> list:
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 50  # count przychodzi z frontendu przez RPC; log musi się pokazać
        if count <= 0:
            return []
        out = []
        for line in reversed(self._lines()):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= count:
                break
        return out
