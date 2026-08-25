import contextlib
import json
import os
import subprocess
import time

from .messages import msg

LOCAL_TIMEOUT = 600
# ZMIERZONE na Decku: Ludusavi przy KAŻDYM wywołaniu sprawdza aktualizację manifestu
# przez sieć, a gdy sieci nie ma, przerywa całe polecenie („Nie można sprawdzić
# aktualizacji dla pliku manifest"). Kosztowało to kopię bezpieczeństwa dwóch gier —
# wtyczka zameldowała „konflikt bez kopii bezpieczeństwa" i odmówiła ochrony zapisów
# przez chwilowy brak sieci. --try-manifest-update nadal aktualizuje, gdy się da,
# ale błąd sprawdzenia przestaje unieważniać robotę. Flaga jest globalna, więc idzie
# przed podpolecenie.
MANIFEST_FLAG = ["--try-manifest-update"]
# rclone przez wolne łącze bywa wielokrotnie dłuższy niż praca na karcie
CLOUD_TIMEOUT = 3600
# Prefiks Protona skrótu non-Steam. Steam nadaje appid sam i INNY na każdym
# urządzeniu, więc ta ścieżka jest lokalna — cała reszta pliku istnieje po to,
# żeby nie wyciekła do chmury.
PREFIX_ROOT = os.path.expanduser("~/.local/share/Steam/steamapps/compatdata")
# Ścieżka, pod którą zapis leży W KOPII: jedna dla wszystkich urządzeń.
CANON_ROOT = "/sdsync"
# Indeks kopii Ludusavi — plik metadanych, nigdy zapis gry.
MAPPING_FILE = "mapping.yaml"
# W podglądzie pobrania: "u nas by to zniknęło, bo chmura tego nie ma" —
# czyli dowód, że MY jesteśmy przed chmurą.
CHANGE_REMOVED = "Removed"
# ...a to dowód, że coś przyjdzie z chmury.
CHANGES_INCOMING = ("New", "Different")
# Ile pełnych kopii gry trzymamy w chmurze. Każda to ~10 katalogów w drzewie,
# a listowanie drzewa przez rclone to ~90% czasu synchronizacji — więc ta liczba
# jest wymianą „głębokość historii" na „czas czekania na karcie". Wybrane
# świadomie przez użytkownika: 5 → 2. Kopia bezpieczeństwa przed przywróceniem
# ma osobny limit niżej i NIE idzie do chmury, więc jej nie tykamy.
CLOUD_FULL_LIMIT = "2"
# Kopia na karcie: karta jest TRANSPORTEM zapisów (bez niej nie da się zagrać, więc
# zawsze ma najświeższy stan i nie potrzebuje sieci). Tyle samo wersji co w chmurze.
CARD_FULL_LIMIT = "2"
# Kopia bezpieczeństwa przed przywróceniem — nie wychodzi z urządzenia.
SAFETY_FULL_LIMIT = "3"


def _default_runner(argv: list, timeout: float = LOCAL_TIMEOUT):
    """Zwraca (code, stdout, stderr). Kod -1 = polecenie nie wystartowało/nie skończyło.

    stdin=DEVNULL jest obowiązkowe: Ludusavi pytające o potwierdzenie czeka na
    wejście, a zmierzone przez kontrolę wywołanie z potokiem na wejściu wisiało
    11 minut przy limicie godziny — i tyle samo trzymałoby zamek synchronizacji.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout po %ss: %s" % (timeout, " ".join(argv))
    except (FileNotFoundError, OSError) as exc:
        return -1, "", "nie udało się uruchomić %s: %s" % (argv[0], exc)
    return proc.returncode, proc.stdout, proc.stderr


def _config_path(command: list) -> str:
    """Konfiguracja, którą czyta WŁAŚNIE to polecenie. Flatpak ma własny katalog
    (~/.var/app/<id>/config/ludusavi) i tam użytkownik ma ustawioną chmurę;
    samodzielne ~/.config/ludusavi/config.yaml ma remote: ~ i jest nieużywane."""
    if command and os.path.basename(command[0]) == "flatpak":
        return os.path.expanduser(
            "~/.var/app/%s/config/ludusavi/config.yaml" % command[-1])
    return os.path.expanduser("~/.config/ludusavi/config.yaml")


def cloud_remote_set(path: str):
    """Czy w konfiguracji Ludusavi ustawiony jest zdalny katalog (cloud.remote).
    Trójstan: True / False / None = nie ma jak sprawdzić (brak pliku, brak dostępu,
    nieznany układ) — wtedy nie zgadujemy.

    Czytamy tekstem, bez pyyaml (w środowisku wtyczki go nie ma). Zmierzone dwa
    układy: "remote: ~" (nieustawiona) i "remote:" z zagnieżdżonym "GoogleDrive:"
    (ustawiona).
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    in_cloud = False
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():
            in_cloud = line.startswith("cloud:")
            continue
        if not in_cloud:
            continue
        key, sep, value = line.strip().partition(":")
        if key != "remote" or not sep:
            continue
        value = value.strip()
        if value:
            return value not in ("~", "null")
        # puste po dwukropku = albo mapa niżej (ustawiona), albo nic (null)
        nxt = next((l for l in lines[index + 1:] if l.strip()), "")
        indent = len(line) - len(line.lstrip())
        return (len(nxt) - len(nxt.lstrip())) > indent
    return None


def cloud_target(path: str) -> tuple:
    """`(zdalny, katalog)` z konfiguracji Ludusavi, albo `(None, None)`.

    Adres dla rclone to `<zdalny>:<katalog>`. ZMIERZONE na Decku, jak to leży:

        cloud:
          remote:
            GoogleDrive:
              id: ludusavi-1787098149
          path: ludusavi-backup

    czyli identyfikator jest o DWA poziomy głębiej niż `remote:` i nazwa dostawcy
    („GoogleDrive") nie jest adresem — wzięcie jej dałoby nieistniejący zdalny.
    Czytamy tekstem, bez pyyaml: w środowisku wtyczki go nie ma (tak samo jak
    `cloud_remote_set` obok).
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None, None
    remote, folder, w_cloud, w_remote = None, None, False, False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():
            w_cloud = line.startswith("cloud:")
            w_remote = False
            continue
        if not w_cloud:
            continue
        wciecie = len(line) - len(line.lstrip())
        key, sep, value = line.strip().partition(":")
        if wciecie <= 2:
            w_remote = key == "remote"
            if key == "path" and sep:
                folder = value.strip() or None
            continue
        if w_remote and key == "id" and sep:
            remote = value.strip() or None
    if not remote or not folder:
        return None, None
    return remote, folder


class SyncLocked(Exception):
    """Zamek synchronizacji jest w cudzych rękach.

    Niesie `msg`, bo `main.py` podaje to użytkownikowi jako komunikat — a nie wolno
    mu podać zdania, którego nie da się przetłumaczyć. `str(exc)` zostaje angielskim
    zdaniem, żeby `logger.exception` i debugowanie działały jak dotąd.
    """

    def __init__(self, problem):
        # napis dopuszczamy dla wywołań, których jeszcze nie przerobiono (etap B)
        self.msg = problem if isinstance(problem, dict) else msg(
            "sync_lock_takeover_failed", detail=str(problem))
        super().__init__(self.msg["message"])


def _lock_holder(path: str):
    """Komunikat, gdy zamek trzyma żywy proces; None, gdy zamek jest do przejęcia
    (nieczytelny albo po zabitym procesie — inaczej jedno ubicie procesu blokowałoby
    synchronizację na zawsze)."""
    try:
        with open(path, encoding="utf-8") as handle:
            pid_text, _, when = handle.read().strip().partition(" ")
        pid = int(pid_text)
    except (OSError, ValueError):
        return None
    if pid != os.getpid():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass  # cudzy proces, ale żyje
    return msg("sync_lock_held", pid=pid, when=when or "?")


@contextlib.contextmanager
def sync_lock(path: str):
    """Jedna synchronizacja naraz na urządzeniu — zamek plikowy, bo równolegle
    zainstalowana wtyczka decky-ludusavi woła to samo Ludusavi i to samo rclone,
    a zmienna w pamięci nie chroni przed drugim przebiegiem.

    ponytail: rozpoznanie „proces nie żyje" opiera się na pid — przy przekręconej
    numeracji pid zamek po zabitym procesie może wyglądać na żywy. Ceną jest jedno
    pominięte przejście, więc nie dokładamy tu ani sekundy więcej kodu.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        holder = _lock_holder(path)
        if holder:
            raise SyncLocked(holder)
        try:
            os.unlink(path)
            fd = os.open(path, flags, 0o644)
        except OSError as exc:
            raise SyncLocked(msg("sync_lock_takeover_failed", detail=str(exc)))
    with os.fdopen(fd, "w") as handle:
        handle.write("%d %s" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    try:
        yield
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _blank(title) -> bool:
    """Ludusavi bez filtra gry pracuje na całej bibliotece — pusty tytuł nie wychodzi
    poza tę granicę, niezależnie od tego, kto woła Saves."""
    return not (title or "").strip()


def _filter(games) -> list:
    """Lista gier dla `[GAMES]...`. Same puste tytuły to błąd wywołującego, nie
    zaproszenie do pracy na całej bibliotece — dlatego rozpoznajemy to osobno."""
    return [t.strip() for t in (games or []) if not _blank(t)]


def _changed(game) -> bool:
    """Czy podgląd Ludusavi ma dla tej gry cokolwiek do skopiowania.

    Brak wpisu przy kodzie 0 to G2: ta gra nie ma tu zapisów — jednoznaczne
    „nie ma czego chronić", a nie „nie wiem". „Nie wiem" powstaje piętro wyżej,
    z kodu błędu albo braku klucza `games`.
    """
    if not game:
        return False
    if game.get("change") not in (None, "Same"):
        return True
    return any(info.get("change") not in (None, "Same") and not info.get("ignored")
               for info in (game.get("files") or {}).values())


# Znaki, których Ludusavi nie wstawia do nazwy katalogu kopii. ZMIERZONE na Decku
# dla dwukropka: „The Binding of Isaac: Rebirth" dało katalog
# „The Binding of Isaac_ Rebirth". Pozostałe to te same znaki niedozwolone
# w nazwach plików na Windows — dopisujemy je z ostrożności, nie z pomiaru.
_UNSAFE_IN_DIR = ':/\\*?"<>|'


def _when_of(folder: str) -> str:
    """`backup-20260820T231843Z` → `2026-08-20T23:18:43Z`.

    Nazwa katalogu kopii JEST znacznikiem czasu (ZMIERZONE), więc wystarczy ją
    rozłożyć — nieznany kształt oddajemy bez zmian zamiast zgadywać datę.
    """
    stamp = (folder or "")[len("backup-"):]
    if len(stamp) != 16 or stamp[8] != "T" or not stamp[-1] == "Z":
        return folder or ""
    return "%s-%s-%sT%s:%s:%sZ" % (stamp[0:4], stamp[4:6], stamp[6:8],
                                   stamp[9:11], stamp[11:13], stamp[13:15])


def backup_dir_name(title: str) -> str:
    """Nazwa katalogu, pod którą Ludusavi trzyma kopię tej gry.

    Nie zawsze równa tytułowi, a `_cloud_diff` rozpoznaje grę po PIERWSZYM segmencie
    ścieżki z chmury — więc bez tego przeliczenia gra z dwukropkiem w tytule po cichu
    wypadała z synchronizacji: chmura nigdy nie byłaby dla niej „przed nami".
    """
    text = (title or "").strip()
    for char in _UNSAFE_IN_DIR:
        text = text.replace(char, "_")
    return text


def prefix_source(appid, root: str = None) -> str:
    """Prefiks Protona skrótu o tym appid. Pusto = nie znamy appid, czyli gra nie
    jest jeszcze dodana do Steama i nie ma dla niej czego przekierowywać."""
    try:
        number = int(appid)
    except (TypeError, ValueError):
        return ""
    return os.path.join(root or PREFIX_ROOT, str(number), "pfx")


def canon_target(title_key: str) -> str:
    """Docelowa ścieżka W KOPII — po jednej na grę. Wspólny katalog dla wszystkich
    gier NIE wystarczy: przy przywracaniu Ludusavi dopasowuje redirect po ścieżce
    źródłowej, więc dwie gry z tym samym targetem trafiłyby w ten sam prefiks."""
    key = (title_key or "").strip()
    return "%s/%s" % (CANON_ROOT, key) if key else ""


def _yaml_str(value: str) -> str:
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def _foreign_items(block: list) -> list:
    """Wpisy z bloku `redirects`, których NIE zarządzamy (target poza CANON_ROOT).
    Konfiguracja należy do użytkownika — własne przekierowania muszą przeżyć.

    ponytail: komentarz między `redirects:` a pierwszym wpisem przepada. Ludusavi
    pisze ten plik sam i komentarzy tam nie stawia; gdyby zaczął, chunk trzeba
    zacząć od pierwszej linii bloku, nie od pierwszego `- `.
    """
    items, current = [], None
    for line in block[1:]:  # block[0] to sama linia "redirects:"
        if line.lstrip().startswith("- "):
            if current:
                items.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current:
        items.append(current)
    joined = ["".join(item) for item in items]
    return [text for text in joined
            if 'target: "%s/' % CANON_ROOT not in text
            and "target: %s/" % CANON_ROOT not in text]


def redirects_yaml(config_text: str, wanted) -> str:
    """Treść config.yaml z NASZYMI redirectami zamiast poprzednich naszych.

    Podmiana jest tekstowa, bo w środowisku wtyczki nie ma pyyaml, a `redirects`
    to klucz najwyższego poziomu — blok kończy się na pierwszej linii bez wcięcia.
    """
    lines = config_text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.startswith("redirects:")), None)
    if start is None:
        head, tail, keep = lines, [], []
        if head and not head[-1].endswith("\n"):
            head = head + ["\n"]
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip()
                                    or lines[end][:1].isspace()):
            end += 1
        head, tail = lines[:start], lines[end:]
        keep = _foreign_items(lines[start:end])
    ours = ['  - kind: bidirectional\n    source: %s\n    target: %s\n'
            % (_yaml_str(source), _yaml_str(target))
            for source, target in wanted if source and target]
    body = keep + ours
    block = "redirects:\n" + "".join(body) if body else "redirects: []\n"
    return "".join(head) + block + "".join(tail)


class Saves:
    def __init__(self, command: list, safety_path: str, runner=None, lock_path=None,
                 config_path=None, prefix_root=None):
        self.command = command
        self.safety_path = safety_path
        self.runner = runner or _default_runner
        self.last_stderr = ""
        # gotowy komunikat po ostatnim cloud_state() — pusty, gdy stan odczytany
        self.cloud_error = ""
        # nasza własna diagnoza ostatniej awarii (nie ogon stderr Ludusavi)
        self.last_problem = ""
        self.config_path = config_path or _config_path(command)
        self.prefix_root = prefix_root or PREFIX_ROOT
        # {tytuł: prefiks naszego skrótu} — wypełnia apply_redirects(); dopóki jest
        # puste, bramka prefiksu milczy (nie wiemy, czego oczekiwać)
        self._prefixes = {}
        # tytuły, których baza Ludusavi nie zna — z ostatniego zejścia na wywołania
        # pojedyncze. Wywołujący ma je zapamiętać, żeby nie płacić tego drugi raz.
        self.unknown_titles = set()
        # zamek obok katalogu kopii bezpieczeństwa, nie w środku: `--path` należy
        # do Ludusavi i nie wkładamy mu tam własnych plików
        self.lock_path = lock_path or os.path.join(
            os.path.dirname(safety_path.rstrip(os.sep)) or ".", "sdsync-sync.lock")

    def lock(self):
        """Kontekst „jedna synchronizacja naraz". Zajęty zamek → SyncLocked z
        gotowym komunikatem (nigdy ciche pominięcie roboty)."""
        return sync_lock(self.lock_path)

    # --- jedna kopia w chmurze na oba urządzenia ---

    def apply_redirects(self, records) -> bool:
        """Przepisuje `redirects` w konfiguracji Ludusavi z naszego rejestru.

        Bez tego chmura NIE MA jednej kopii do dziedziczenia: Ludusavi zapisuje
        ścieżkę bezwzględną żywego zapisu, a ta zawiera appid skrótu — inny na
        każdym urządzeniu. ZMIERZONE: kopia z Machine (`compatdata/4064390628`)
        przywracała się na Decku do tego samego, obcego katalogu, którego Deck
        nie czyta, i meldowała sukces. Z `kind: bidirectional` kopia zapisuje
        `/sdsync/<klucz>/…`, a przywracanie odwzorowuje to na LOKALNY prefiks —
        sprawdzone sumą sha1 tego samego pliku na obu urządzeniach.

        Wołać PRZED każdą operacją Ludusavi: appid zmienia się przy ponownym
        dodaniu gry, a nieaktualny wpis kieruje przywracanie w martwy prefiks.
        """
        wanted, prefixes = [], {}
        for record in records or []:
            title = (record.get("title") or "").strip()
            source = prefix_source(record.get("appid"), self.prefix_root)
            target = canon_target(record.get("title_key"))
            if not (title and source and target):
                continue
            wanted.append((source, target))
            prefixes[title] = source
        self._prefixes = prefixes
        try:
            with open(self.config_path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            self.last_problem = msg("ludusavi_config_read_failed",
                                    path=self.config_path, detail=str(exc))["message"]
            return False
        fresh = redirects_yaml(text, wanted)
        if fresh == text:
            return True
        # zapis przez plik tymczasowy: urwany config.yaml to Ludusavi, które nie
        # wstaje, czyli cała warstwa zapisów martwa
        temporary = self.config_path + ".sdsync-new"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(fresh)
            os.replace(temporary, self.config_path)
        except OSError as exc:
            self.last_problem = msg("ludusavi_config_write_failed",
                                    path=self.config_path, detail=str(exc))["message"]
            return False
        return True

    def _wrong_prefix(self, title: str, files) -> bool:
        """Ludusavi zgłosił pliki, ale ŻADEN nie leży w prefiksie NASZEGO skrótu.

        ZMIERZONE na Decku: przez nieaktualny `shortcuts.vdf` (martwe katalogi
        `userdata/0` i `userdata/1218901420`) Ludusavi robił kopię prefiksu
        2911071959, choć skrót wtyczki miał 2238188571 — i meldował „bez zmian".
        Prawdziwy postęp gracza nie trafił do chmury ani razu, a wtyczka pokazywała
        stan „zsynchronizowane" (grabla „awaria nie może wyglądać jak sukces").

        ponytail: gra trzymająca zapisy WYŁĄCZNIE poza prefiksem wypadnie tu jako
        awaria. Wszystkie nasze gry to .exe pod Protonem, więc dziś takiej nie ma;
        gdy się pojawi, warunek trzeba zawęzić (np. pole w rejestrze).
        """
        source = self._prefixes.get((title or "").strip())
        if not source or not files:
            return False
        ours = any(str(path).startswith(source + os.sep) for path in files)
        if ours:
            return False
        # Poza compatdata leżą LEGALNE miejsca zapisów: chmura Steama
        # (`userdata/<id>/<appid>/remote` — ZMIERZONE dla „The Binding of Isaac:
        # Rebirth"), prefiksy innych launcherów (Heroic) i katalogi natywne.
        # Bramka ma łapać wyłącznie kopię CUDZEGO prefiksu Protona.
        root = os.path.dirname(os.path.dirname(source)) + os.sep
        return any(str(path).startswith(root) for path in files)

    def _prefix_problem(self, title: str) -> str:
        self.last_problem = msg(
            "wrong_prefix", title=title,
            prefix=self._prefixes.get((title or "").strip(), "?"))["message"]
        return self.last_problem

    def _api(self, args: list, timeout: float = LOCAL_TIMEOUT):
        """(code, dane|None). Strumienie zostają rozdzielone — stderr idzie też do logu.

        Wybór strumienia jest świadomy, nie „pierwszy, który się sparsuje":
        polecenia `cloud …` wypisują JSON na stderr, pozostałe (backup/backups/restore)
        na stdout. Drugi strumień jest tylko zapasem — inaczej obcy JSON na stdout
        (np. `{}` z opakowania flatpaka) przesłoniłby prawdziwe dane ze stderr
        i wyszłoby ciche „brak zmian". None = nie wiem.
        """
        code, out, err = self.runner(self.command + MANIFEST_FLAG + args, timeout)
        self.last_stderr = err or ""
        streams = (err, out) if args[:1] == ["cloud"] else (out, err)
        for stream in streams:
            try:
                data = json.loads(stream)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return code, data
        return code, None

    def _rclone_command(self) -> list:
        """Polecenie rclone. Nie „rclone z PATH": ZMIERZONE na Decku, że Ludusavi
        niesie WŁASNY (`apps.rclone.path: /app/bin/rclone`) i ta ścieżka istnieje
        wyłącznie WEWNĄTRZ sandboksa flatpaka. Stąd `flatpak run --command=rclone`
        z tym samym identyfikatorem aplikacji — wtedy dostajemy rclone'a z jego
        konfiguracją zdalnych, czyli tę, którą chmura jest naprawdę ustawiona.
        Przy instalacji samodzielnej rclone jest zwykłym programem z PATH."""
        if self.command and os.path.basename(self.command[0]) == "flatpak":
            return ["flatpak", "run", "--command=rclone", self.command[-1]]
        return ["rclone"]

    def cloud_when(self, title: str):
        """Tożsamość NAJNOWSZEJ kopii tej gry w chmurze. Trójstan.

        `None` = nie wiem (brak sieci, chmura nieustawiona, nieczytelna odpowiedź),
        `""` = pytaliśmy i kopii nie ma, tekst = `when` tej kopii. Zlanie dwóch
        pierwszych kazałoby użytkownikowi wybrać kartę w przekonaniu, że w chmurze
        nie ma nic — a tam mógłby leżeć nowszy zapis.

        ZMIERZONE na Decku 2026-08-24: katalog kopii w chmurze NAZYWA SIĘ znacznikiem
        czasu (`backup-20260820T231843Z`), więc jedno `rclone lsjson` (4,4 s) daje
        datę bez ściągania choćby bajtu zapisu. Ludusavi nie ma polecenia „pokaż kopie
        w chmurze", a `cloud … --preview` nie niesie ŻADNEJ daty (sprawdzone).
        """
        remote, folder = cloud_target(self.config_path)
        if not remote or _blank(title):
            return None
        adres = "%s:%s/%s" % (remote, folder, backup_dir_name(title))
        code, out, err = self.runner(
            self._rclone_command() + ["lsjson", "--fast-list", "--max-depth", "1", adres],
            CLOUD_TIMEOUT)
        self.last_stderr = err or ""
        if code != 0:
            return None
        try:
            wpisy = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(wpisy, list):
            return None
        # mapping.yaml leży obok kopii i NIE jest kopią — bez filtra po „backup-"
        # wygrywałby przy każdym listowaniu
        kopie = [str(w.get("Name")) for w in wpisy
                 if isinstance(w, dict) and w.get("IsDir")
                 and str(w.get("Name", "")).startswith("backup-")]
        return _when_of(max(kopie)) if kopie else ""

    def live_save_when(self, title: str):
        """Czas zapisu W GRZE (nie kopii). Trójstan jak wyżej.

        To jedyna z trzech dat, która nie jest `when` żadnej kopii — bo zapis w grze
        kopią nie jest. Ludusavi podaje ŚCIEŻKI plików zapisu w podglądzie (ZMIERZONE),
        więc czas bierzemy sami z systemu plików: najświeższy plik gry to „kiedy
        ostatnio grałem". Pliki, których już nie ma (`change: Removed`), pomijamy —
        `os.stat` i tak by na nich padł.
        """
        if _blank(title):
            return None
        code, data = self._api(["backup", "--preview", "--api", title])
        if code != 0 or data is None:
            return None
        files = ((data.get("games") or {}).get(title) or {}).get("files") or {}
        czasy = []
        for path in files:
            try:
                czasy.append(os.stat(path).st_mtime)
            except OSError:
                continue
        if not czasy:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(czasy)))

    def cloud_configured(self):
        """Trójstan: True / False / None („nie ma jak sprawdzić"). Chmura jest u nas
        KOPIĄ, nie transportem — jej brak nie może zatrzymać zapisu na kartę."""
        return cloud_remote_set(self.config_path)

    # --- chmura ---

    def cloud_download(self, games=None) -> bool:
        """`games` = lista tytułów (`[GAMES]...`). Bez niej operacja obejmuje cały
        katalog kopii — a to znaczy „przepisz chmurę/katalog w całości", więc dla
        pojedynczej gry filtr jest obowiązkowy (G4)."""
        return self._cloud_apply("download", games)

    def cloud_upload(self, games=None) -> bool:
        return self._cloud_apply("upload", games)

    def _cloud_apply(self, direction: str, games) -> bool:
        titles = _filter(games)
        if games and not titles:
            return False
        # --force jest konieczne: bez tty polecenie kończy się błędem potwierdzenia
        code, _ = self._api(["cloud", direction, "--force"] + titles, CLOUD_TIMEOUT)
        return code == 0

    def cloud_state(self) -> tuple:
        """(ok, cloud_ahead, local_ahead) — po jednym podglądzie na kierunek, na całą
        bibliotekę. Kierunek zastępuje zgadywanie „co jest nowsze": rozjazd w obu
        kierunkach to konflikt, nie przywracanie."""
        # Bez ustawionego cloud.remote podgląd kończy się kodem 0 i {"games": {}} —
        # nie do odróżnienia od "wszystko zsynchronizowane". Cicha zgoda znaczyłaby,
        # że wtyczka melduje sukces, choć nic nigdy nie zostanie przeniesione.
        configured = cloud_remote_set(self.config_path)
        if configured is False:
            self.cloud_error = msg("cloud_not_configured")
            return False, set(), set()
        if configured is None:
            # "nie wiem, czy chmura jest ustawiona" nie moze przejsc po cichu: podglady
            # odpowiedza wtedy kodem 0 i pusta lista, a przebieg zamelduje sukces bez
            # ani jednej przeniesionej gry. ponytail: ta sama etykieta co dla podglądu
            # nieczytelnego niżej — obie mówią "nie wiem, jaki jest stan chmury",
            # a ścieżka pliku konfiguracji jest w self.config_path, gdyby ktoś kopał dalej.
            self.cloud_error = msg("cloud_unreadable")
            return False, set(), set()
        ok, cloud_ahead, local_ahead = self._cloud_diff()
        if not ok:
            self.cloud_error = msg("cloud_unreadable")
            return False, set(), set()
        self.cloud_error = ""
        return True, cloud_ahead, local_ahead

    def _cloud_diff(self) -> tuple:
        """(ok, cloud_ahead, local_ahead) z JEDNEGO podglądu pobrania.

        Pole "cloud" to mapa ścieżek względem folderu chmury; nazwa gry to pierwszy
        segment (kopie leżą w <Gra>/backup-…/). Poprawny JSON bez tego pola = brak
        zmian.

        Dwa podglądy (osobno download i upload) były zmarnowanym przebiegiem rclone
        po całej chmurze, czyli ~połową czasu synchronizacji. ZMIERZONE na Decku przy
        TYM SAMYM stanie — plik tylko lokalny plus nowsza kopia tylko w chmurze
        (fixtures `*_BOTH_WAYS_*`):

            download: probe "Removed",   kopia z chmury "Different"
            upload:   probe "Different", kopia z chmury "Removed"

        Podglądy są dokładnym odbiciem siebie, więc drugi nie niósł ani jednej nowej
        informacji. Zmiany opisują skutek dla strony DOCELOWEJ, więc w podglądzie
        pobrania: "New"/"Different" = coś przyjdzie z chmury (chmura przed nami),
        "Removed" = coś by u nas zniknęło, bo chmura tego nie ma (my przed chmurą).

        `mapping.yaml` pomijamy w obie strony: to indeks Ludusavi i różni się zawsze,
        gdy drzewa nie są identyczne, więc nie rozstrzyga kierunku.
        """
        # --force także w podglądzie: bez tty pytanie o potwierdzenie wisi
        code, data = self._api(
            ["cloud", "download", "--preview", "--api", "--force"], CLOUD_TIMEOUT)
        if code != 0 or data is None:
            return False, set(), set()
        cloud_ahead, local_ahead = set(), set()
        for path, info in (data.get("cloud") or {}).items():
            text = str(path)
            if text.rsplit("/", 1)[-1] == MAPPING_FILE:
                continue
            game = text.split("/")[0].strip()
            if not game:
                continue
            change = (info or {}).get("change") if isinstance(info, dict) else None
            if change == CHANGE_REMOVED:
                local_ahead.add(game)
            elif change in CHANGES_INCOMING:
                cloud_ahead.add(game)
            else:
                # nieznany rodzaj zmiany to niewiedza, a niewiedza idzie na konflikt
                cloud_ahead.add(game)
                local_ahead.add(game)
        return True, cloud_ahead, local_ahead

    # --- kopie ---

    def backup(self, title: str, cloud: bool = True) -> dict:
        result = {"ok": False, "changed_bytes": 0, "conflict": False}
        if _blank(title):
            return result
        args = ["backup", "--force", "--api", "--full-limit", CLOUD_FULL_LIMIT]
        args.append("--cloud-sync" if cloud else "--no-cloud-sync")
        args.append(title)
        code, data = self._api(args, CLOUD_TIMEOUT if cloud else LOCAL_TIMEOUT)
        # brak klucza "games" = odpowiedź nie mówi, czy cokolwiek przetworzono
        if code != 0 or data is None or "games" not in data:
            return result
        errors = data.get("errors") or {}
        result["conflict"] = bool(errors.get("cloudConflict"))
        if errors.get("cloudSyncFailed") or errors.get("someGamesFailed"):
            return result
        # G3: kryterium jak w restore()/safety_backup() — gra przetworzona i niepusta
        # lista plików. Kod 0 z {"games": {}} znaczy „gra nie ma zapisów", a nie
        # „kopia powstała": fałszywy sukces czyścił flagę konfliktu i zapisywał
        # świeży znacznik kopii, choć w chmurze nic nie było.
        game = (data.get("games") or {}).get(title) or {}
        files = game.get("files") or {}
        if game.get("decision") != "Processed" or not files:
            return result
        if self._wrong_prefix(title, files):
            self._prefix_problem(title)
            return result
        result["ok"] = True
        for info in files.values():
            if info.get("ignored") or info.get("change") == "Same":
                continue
            result["changed_bytes"] += info.get("bytes", 0)
        return result

    def _batch_or_single(self, wanted, call, on_fail):
        # Zbiorcze wywołanie Ludusavi, a po jego awarii — po jednym na grę.
        #
        # ZMIERZONE na Decku: gdy choć jeden tytuł z listy jest bazie Ludusavi
        # nieznany, wywala się CAŁE polecenie (kod != 0, „Brak informacji dla tych
        # gier"). Jeden taki wpis („GTA V Enhanced") zamieniał wszystkie 12 gier
        # w konflikty, bo o każdej wiedzieliśmy „nie wiem". Kierunek był słuszny,
        # zasięg nie.
        #
        # Komunikatu NIE parsujemy — Ludusavi tłumaczy go na język systemu, więc kod
        # oparty na jego treści psułby się przy zmianie języka. Pytamy o każdą grę
        # osobno: wolniej, ale prawda jest wtedy o każdej z nich.
        #
        # `call(titles)` zwraca słownik wyników albo None, gdy całe wywołanie padło.
        out = call(wanted)
        if out is not None:
            return out
        merged, padly, udane = {}, [], 0
        for title in wanted:
            single = call([title])
            if single is None:
                padly.append(title)
                merged[title] = on_fail
            else:
                udane += 1
                merged.update(single)
        # Oflagowanie jako „baza nie zna tego tytułu" wolno tylko wtedy, gdy CHOĆ JEDNA
        # gra przeszła. Gdy padły wszystkie, problemem jest narzędzie (brak Ludusavi,
        # nieudana aktualizacja manifestu), a nie nazwy — a flaga siedzi w rejestrze
        # i odcięłaby wtedy całą bibliotekę od obsługi zapisów na stałe.
        if udane:
            self.unknown_titles.update(padly)
        return merged

    def local_changed_many(self, titles) -> dict:
        """{tytuł: True/False/None} — JEDNYM wywołaniem Ludusavi na całą listę.

        Ludusavi przyjmuje `[GAMES]...`, a każde uruchomienie kosztuje start procesu
        (u nas przez flatpak — kilka sekund, zanim cokolwiek policzy). Pytanie osobno
        o każdą grę mnożyło ten koszt przez wielkość biblioteki.

        Awaria dotyczy całego wywołania, więc niewiedza obejmuje wszystkie gry z listy
        — „nie wiem" nigdy nie zamienia się w „brak zmian" (F4).
        """
        wanted = _filter(titles)
        if not wanted:
            return {}
        return self._batch_or_single(wanted, self._preview_call, None)

    def _preview_call(self, wanted):
        # Jeden podgląd; None = całe polecenie padło (patrz _batch_or_single).
        code, data = self._api(
            ["backup", "--preview", "--api", "--no-cloud-sync"] + wanted)
        if code != 0 or data is None or "games" not in data:
            return None
        games = data.get("games") or {}
        out = {}
        for title in wanted:
            game = games.get(title)
            if self._wrong_prefix(title, (game or {}).get("files")):
                self._prefix_problem(title)
                out[title] = None  # niewiedza → konflikt, czyli decyzja człowieka
                continue
            out[title] = _changed(game)
        return out

    def local_changed(self, title: str):
        """Czy żywy zapis różni się od ostatniej kopii. Podgląd — nie zapisuje nic
        i nie dotyka chmury. Trójstan: True / False / None = nie wiem."""
        if _blank(title):
            return None
        return self.local_changed_many([title]).get(title.strip())

    def safety_backup(self, title: str):
        """KONTRAKT — trójstan:
          True  = skopiowano (gra "Processed" i niepusta lista plików),
          None  = nie ma czego chronić (kod 0 i puste `games`; zmierzone: gra bez
                  żadnych lokalnych zapisów odpowiada dokładnie tak),
          False = awaria (kod != 0, brak JSON-a, brak klucza `games`, pusty tytuł,
                  albo gra w odpowiedzi bez plików / nieprzetworzona).

        Przywracanie wolno kontynuować przy True i przy None (nie ma czego stracić);
        blokuje wyłącznie False. Wywołujący MUSI sprawdzać `is False`, nie `not`.
        """
        if _blank(title):
            return False
        return self.safety_backup_many([title]).get(title.strip(), False)

    def safety_backup_many(self, titles) -> dict:
        """{tytuł: True/None/False} wg kontraktu safety_backup(), jednym wywołaniem.

        Awaria wywołania to awaria dla KAŻDEJ gry z listy (False), bo nie wiadomo,
        której kopia powstała — przywracanie musi się wtedy zatrzymać na wszystkich.
        """
        return self._backup_to(titles, self.safety_path, SAFETY_FULL_LIMIT)

    def _backup_to(self, titles, path: str, limit: str) -> dict:
        """{tytuł: True/None/False} wg kontraktu safety_backup(): kopia do WSKAZANEGO
        katalogu, bez chmury. Jeden silnik dla kopii bezpieczeństwa i kopii na karcie —
        to samo polecenie Ludusavi, inny katalog i inna retencja."""
        wanted = _filter(titles)
        if not wanted or not (path or "").strip():
            return {}
        return self._batch_or_single(
            wanted, lambda lista: self._backup_call(lista, path, limit), False)

    def _backup_call(self, wanted, path: str, limit: str):
        # Jedno wywołanie kopii; None = całe polecenie padło (patrz _batch_or_single).
        code, data = self._api(["backup", "--force", "--api", "--no-cloud-sync",
                                "--path", path, "--full-limit", limit] + wanted)
        if code != 0 or data is None or "games" not in data:
            return None
        games = data.get("games") or {}
        out = {}
        for title in wanted:
            game = games.get(title)
            if game is None:
                out[title] = None  # kod 0 bez wpisu = ta gra nie ma tu zapisów (G2)
                continue
            if self._wrong_prefix(title, game.get("files")):
                self._prefix_problem(title)
                out[title] = False  # kopia objęła nie ten plik → nie ma zapasu
                continue
            # ponytail: gra z zapisem wyłącznie w rejestrze Windows wypadnie tu jako
            # porażka; na Decku takich nie ma, a fałszywy sukces kosztowałby zapis
            out[title] = (game.get("decision") == "Processed"
                          and bool(game.get("files")))
        return out

    # --- karta jako transport zapisów ---

    def card_backup_many(self, titles, path: str) -> dict:
        """Kopia zapisów na KARTĘ, tym samym kontraktem co kopia bezpieczeństwa.
        Bez sieci — dlatego ta faza może iść przed chmurą i działać w pociągu."""
        return self._backup_to(titles, path, CARD_FULL_LIMIT)

    def card_when_many(self, titles, path: str):
        """{tytuł: najnowszy `when` z karty albo None} — JEDNO wywołanie na kartę.

        Rozróżnienie jest tu wszystkim: `None` przy grze znaczy „karta nie ma jej
        kopii" i wolno wtedy sięgnąć do chmury, a `None` zamiast całego słownika
        znaczy „nie wiem" (awaria wywołania). Zamienienie tych dwóch stanów oddawałoby
        decyzję chmurze przy nieczytelnej karcie — a chmura może mieć starszy stan.

        `when` traktujemy jako TOŻSAMOŚĆ kopii, nie jako datę: porównujemy je na
        równość, więc rozjechane zegary dwóch urządzeń nic tu nie psują. Najnowszy
        wpis to `max` po tekście — format ISO-8601 UTC porządkuje się leksykograficznie,
        więc nie parsujemy dat i nie zależymy od kolejności listy.
        """
        wanted = _filter(titles)
        if not wanted or not (path or "").strip():
            return None
        code, data = self._api(["backups", "--api", "--path", path] + wanted)
        if code != 0 or data is None or "games" not in data:
            return None
        games = data.get("games") or {}
        out = {}
        for title in wanted:
            whens = [str(entry.get("when"))
                     for entry in ((games.get(title) or {}).get("backups") or [])
                     if entry.get("when")]
            out[title] = max(whens) if whens else None
        return out

    def card_restore(self, title: str, path: str) -> bool:
        """Przywrócenie z KARTY. Pusta ścieżka to nie „katalog domyślny" — to praca na
        katalogu chmury, czyli inna operacja niż zamierzona, więc odmawiamy."""
        if not (path or "").strip():
            return False
        return self.restore(title, path=path)

    def restore(self, title: str, path: str = None) -> bool:
        """Udane tylko wtedy, gdy odpowiedź potwierdza przetworzenie gry i jakieś pliki.
        Ludusavi kończy kodem 0 także przy "No saves found for <gra>" — sam kod wyjścia
        zamieniał brak zapisu w meldunek „przywrócono"."""
        if _blank(title):
            return False
        args = ["restore", "--force", "--api"]
        if (path or "").strip():
            args += ["--path", path]
        code, data = self._api(args + [title])
        if code != 0 or data is None:
            return False
        game = (data.get("games") or {}).get(title) or {}
        return game.get("decision") == "Processed" and bool(game.get("files"))

    def backups(self, title: str, path: str = None) -> list:
        if _blank(title):
            return []
        args = ["backups", "--api"]
        if (path or "").strip():
            args += ["--path", path]
        code, data = self._api(args + [title])
        if code != 0 or data is None:
            return []
        entry = (data.get("games") or {}).get(title) or {}
        return [{"name": b.get("name"), "when": b.get("when")}
                for b in entry.get("backups", [])]
