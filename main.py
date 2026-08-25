import asyncio
import functools
import json
import os
import sys
import threading
import time

import decky

sys.path.append(os.path.join(os.path.dirname(__file__), "py_modules"))

from sdsync import (cards, paths, playtime, scan as scanner, shortcuts,  # noqa: E402
                    steamworks, titles)
from sdsync.artwork import Artwork  # noqa: E402
from sdsync.hltb import Hltb  # noqa: E402
from sdsync.log import EventLog  # noqa: E402
from sdsync.messages import msg  # noqa: E402
from sdsync.metadata import Metadata  # noqa: E402
from sdsync.registry import Registry  # noqa: E402
from sdsync.registry import title_key as key_of_title  # noqa: E402
from sdsync.saves import Saves, SyncLocked, backup_dir_name, cloud_remote_set  # noqa: E402
from sdsync.sync import SyncService, remember_card_copy  # noqa: E402


def _clean_child_env() -> None:
    """Decky to binarka PyInstallera: podprocesom podrzuca LD_LIBRARY_PATH ze swoim
    katalogiem /tmp/_MEIxxxxxx. Systemowy flatpak ładuje wtedy cudze libssl/libcrypto
    i pada na „version `OPENSSL_3.4.0' not found" — a wtedy titles.ludusavi_command()
    zwraca None i CAŁA warstwa zapisów milczy, wyglądając na „nie ma czego wysyłać".
    Zmierzone na Decku: z tą zmienną `flatpak info` rc=1, bez niej rc=0, a
    `find --api --normalized "ANIMAL WELL"` zwraca „Animal Well" (score 0.80).
    Zmienną czyta loader systemowy przy starcie procesu, więc już wczytanych
    bibliotek tej wtyczki to nie rusza — dotyczy wyłącznie tego, co odpalamy sami."""
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)  # konwencja PyInstallera
    if original:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


_clean_child_env()

DEFAULT_PROTON = "proton_experimental"
# Pola rejestru, które użytkownik przestawia z ekranu gier. Biała lista jest
# obowiązkowa: bez niej ta sama trasa RPC pozwoliłaby frontendowi przestawić
# appid albo flagę konfliktu i rozjechać rejestr ze Steamem.
EDITABLE_FIELDS = {"excluded": bool, "artwork_done": bool,
                   "check_cloud_before_launch": bool}
SGDB_KEY_FILE = "sgdb_key"
EVENTS_FILE = "events.jsonl"
UI_FILE = "ui.json"


def guarded(fallback):
    """Żadna metoda RPC nie może wysypać wtyczki: frontend Decky nie pokaże stack trace,
    użytkownik zostałby z martwym ekranem. `fallback` to fabryka pustego wyniku o
    kształcie, którego oczekuje frontend (list, dict, str, bool albo lambda)."""

    def wrap(method):
        @functools.wraps(method)
        async def inner(self, *args, **kwargs):
            try:
                return await method(self, *args, **kwargs)
            except Exception as exc:
                problem = msg("internal_error", method=method.__name__,
                              type=type(exc).__name__, detail=str(exc))
                # decky.logger dostaje angielskie zdanie: to log DECKY'EGO, wspólny
                # z innymi wtyczkami, a nie okno użytkownika
                decky.logger.exception("NonSteam Sync: %s", problem["message"])
                try:
                    self._log().add("error", problem)
                except Exception:
                    decky.logger.exception("NonSteam Sync: nie udało się zapisać wpisu w logu")
                out = fallback()
                if isinstance(out, dict):
                    out["error"] = problem
                    if isinstance(out.get("errors"), list):
                        out["errors"].append(problem)
                return out

        return inner

    return wrap


def empty_sync() -> dict:
    return {"restored": [], "conflicts": [], "skipped": [], "blocked": [], "errors": []}


class Plugin:
    # zbiór tytułów aktualnie uruchomionych gier; instancja dostaje własny w _running_set
    _running = None

    # etap trwającego przebiegu, odpytywany przez frontend. Przebieg trwa 20–120 s
    # (czekanie na rclone, ZMIERZONE), więc bez tego przycisk mówi „Pracuję…" przez
    # dwie minuty i wygląda na zawieszony.
    _stage = ""

    # Jedna synchronizacja naraz. Zamek trzyma pętla zdarzeń (acquire bez blokowania),
    # a robota idzie do wątku — drugie wywołanie wraca natychmiast z komunikatem
    # zamiast czekać albo dublować pracę na żywych zapisach.
    # To tylko pierwsza linia (tani odrzut bez ruszania dysku). Przed DRUGIM PROCESEM
    # (wtyczka decky-ludusavi woła to samo Ludusavi i rclone) chroni zamek plikowy
    # z warstwy zapisów — saves.lock() w _sync_all i _resolve_conflict.
    _sync_lock = threading.Lock()

    # --- składniki silnika ---

    def _running_set(self) -> set:
        if self._running is None:
            self._running = set()
        return self._running

    def _registry(self) -> Registry:
        return Registry(paths.games_file(decky.DECKY_PLUGIN_SETTINGS_DIR))

    def _log(self) -> EventLog:
        return EventLog(os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, EVENTS_FILE))

    def _saves(self) -> Saves:
        command = titles.ludusavi_command() or ["ludusavi"]
        saves = Saves(command, paths.safety_dir(decky.DECKY_PLUGIN_RUNTIME_DIR))
        # Redirecty MUSZĄ być świeże przed każdą operacją Ludusavi: appid skrótu
        # zmienia się przy ponownym dodaniu gry, a nieaktualny wpis kieruje
        # przywracanie do prefiksu, którego to urządzenie nie czyta. Awaria zapisu
        # konfiguracji nie może być cicha — bez redirectów chmura znów rozjedzie się
        # na dwie gałęzie, a wtyczka meldowałaby sukces.
        if not saves.apply_redirects(self._registry().all()):
            problem = msg("ludusavi_config_problem",
                          detail=str(saves.last_problem or ""))
            decky.logger.error("NonSteam Sync: %s", problem["message"])
            self._log().add("error", problem)
        return saves

    def _artwork(self) -> Artwork:
        path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SGDB_KEY_FILE)
        key = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip()
        return Artwork(key)

    def _cards(self) -> list:
        return cards.find_cards(cards.default_media_roots())

    def _cloud_enabled(self) -> bool:
        """Czy użytkownik chce w ogóle używać chmury. Domyślnie tak."""
        return self._ui_settings().get("sync_cloud", "on") != "off"

    def _card_saves_dir(self, record) -> str:
        """Katalog kopii zapisów na karcie tej gry. Pusto = karty nie ma w czytniku,
        więc zostaje chmura. Kart może być dowolnie wiele: każda nosi zapisy WŁASNYCH
        gier, a którą kartę ma gra, wie rejestr."""
        mount = self._card_mount(record)
        return paths.card_saves_dir(mount) if mount else ""

    def _record_by_appid(self, appid):
        # appid=None dopasowalby sie do pierwszej gry BEZ appid (swiezo zarejestrowanej):
        # push_after_game zrobilby kopie cudzej gry i wyczyscil jej flage konfliktu
        if not appid:
            return None
        return next((r for r in self._registry().all() if r.get("appid") == appid), None)

    def _card_mount(self, record: dict):
        """Nośnik tej gry albo None (karta wyjęta).

        Dla gry z dysku konsoli nośnikiem jest katalog lokalny — jest ZAWSZE, więc taka
        gra nigdy nie wygląda na „nośnik wyjęty".
        """
        if record.get("carrier") == "disk":
            karta = paths.disk_carrier_dir(decky.DECKY_PLUGIN_RUNTIME_DIR)
            os.makedirs(karta, exist_ok=True)
            return karta
        label = record.get("card_label") or ""
        for card in self._cards():
            if not label or card["label"] == label:
                return card["mount"]
        return None

    def _playtime(self, records: list) -> tuple:
        """(sumy, plik z karty, nasze liczby, nazwa urządzenia).

        Karta bywa wyjęta — wtedy zostaje sam licznik tego urządzenia z rejestru
        i suma jest niepełna, ale nigdy nie znika nasz własny czas.
        """
        device = playtime.device_id()
        mine = {r.get("title_key") or "": r.get("playtime_seconds") or 0
                for r in records}
        card = {}
        for mount in {self._card_mount(r) for r in records}:
            if mount:
                card.update(playtime.read(mount))
        # Ostatnia znana suma jest DOLNĄ granicą: bez karty plik wymiany jest
        # nieczytelny i totals() zna tylko nasze sekundy, więc liczba na kafelku
        # malała (ZMIERZONE: 7,1 min → 5,4 min po wyjęciu karty) i wyglądało to jak
        # zgubiony czas z drugiego urządzenia.
        seen = {r.get("title_key") or "": r.get("playtime_total_seen") or 0
                for r in records}
        totals = playtime.merge_remembered(
            playtime.totals(card, mine, device), seen)
        for record in records:
            key = record.get("title_key") or ""
            if key and totals.get(key, 0) > (record.get("playtime_total_seen") or 0):
                self._registry().set_fields(key, playtime_total_seen=totals[key])
        return totals, card, mine, device

    def _resolve_exe(self, record: dict) -> str:
        """Karta jeździ między urządzeniami i montuje się pod różnymi ścieżkami, więc
        zapisane exe_abs bywa cudze. exe_rel + aktualny punkt montowania daje prawdę."""
        exe = record.get("exe_abs") or ""
        if exe and os.path.isfile(exe):
            return exe
        rel, label = record.get("exe_rel") or "", record.get("card_label") or ""
        if not rel:
            return exe
        for card in self._cards():
            if label and card["label"] != label:
                continue
            candidate = os.path.join(card["mount"], rel)
            if os.path.isfile(candidate):
                return candidate
        return exe

    # --- RPC ---

    @guarded(str)
    async def ping(self) -> str:
        decky.logger.info("NonSteam Sync: ping")
        return "pong"

    @guarded(list)
    async def scan(self) -> list:
        # canonical_title woła Ludusavi, a chodzenie po karcie też trwa: w pętli
        # zdarzeń zablokowałoby mark_running i każdą inną metodę RPC
        return await asyncio.to_thread(self._scan)

    def _scan(self) -> list:
        out = []
        # Kafelki Steama czytamy RAZ na skan, nie raz na grę: to kilka plików na dysku,
        # a gier na karcie bywa kilkadziesiąt.
        existing = []
        for path in shortcuts.config_paths():
            existing += shortcuts.read(path)
        for card in self._cards():
            for candidate in scanner.scan_card(card):
                resolved = titles.canonical_title(candidate["folder"])
                out.append({
                    **candidate,
                    "card_label": card["label"],
                    "title": resolved["title"],
                    "candidates": resolved["candidates"][:5],
                    # Kafelki wskazujące DOKŁADNIE ten plik .exe. Bez tego skan po
                    # przełożeniu karty do drugiego urządzenia robi drugi kafelek dla
                    # gry, w którą użytkownik już tam grał — a nowy kafelek to nowy
                    # prefiks Protona, czyli jego postęp zostaje na boku.
                    "adopt": shortcuts.find_by_exe(existing, candidate["exe_abs"]),
                })
        self._repair_paths(out)
        named = [c["folder"] for c in out if not c["title"]]
        log = self._log()
        log.add("scan", msg("scan_found", count=len(out)))
        if named:
            # cichy brak tytułu blokuje rejestrację gry, a użytkownik widziałby tylko
            # pustą listę — mówimy wprost, czy w ogóle mamy czym pytać Ludusavi
            log.add("error", msg(
                "scan_untitled_title",
                names=", ".join(named),
                command=" ".join(titles.ludusavi_command() or []) or "not found"))
        decky.logger.info("NonSteam Sync: skan znalazł %d gier", len(out))
        return out

    def _repair_paths(self, candidates: list) -> None:
        """Poprawia w rejestrze ścieżkę gier, które właśnie zobaczyliśmy na karcie.

        ZGŁOSZONE z urządzenia: po przezwaniu karty („1281db6f-…" → „Karta 1") trzy
        gry przestały się uruchamiać. Ścieżkę poprawiały tylko te gry, które przechodzą
        przez `addOne` — a tam nie trafiają gry, którym Ludusavi nie nadaje tytułu po
        nazwie folderu (tytuł podał człowiek). Zostawały ze starą ścieżką w rejestrze,
        a przez to i w kafelku Steama, który trzyma ścieżkę na sztywno.

        Dopasowanie idzie po ścieżce WZGLĘDNEJ na karcie: ta nie zmienia się ani przy
        przezwaniu karty, ani między urządzeniami, w przeciwieństwie do bezwzględnej.
        """
        registry = self._registry()
        by_rel = {}
        for record in registry.all():
            rel = (record.get("exe_rel") or "").strip()
            if rel:
                by_rel.setdefault((rel, record.get("folder") or ""), record)
        for candidate in candidates:
            record = by_rel.get(((candidate.get("exe_rel") or "").strip(),
                                 candidate.get("folder") or ""))
            if not record:
                continue
            fresh = candidate.get("exe_abs") or ""
            if not fresh or fresh == record.get("exe_abs"):
                continue
            registry.set_fields(record["title_key"], exe_abs=fresh,
                                card_label=candidate.get("card_label") or "")
            self._log().add("scan", msg("path_repaired", title=record.get("title")))

    @guarded(dict)
    async def card_badges(self) -> dict:
        """{appid: "green"|"white"} — kropka na kafelku.

        Zielona = plik gry jest na miejscu (karta w czytniku), biała = grę obsługujemy,
        ale jej karty nie ma. Front pyta o to CZĘSTO (kropki muszą zmienić kolor zaraz
        po wyjęciu karty), więc to musi być tanie: sam rejestr i `os.path.isfile`,
        bez czytania czasu gry z karty i bez wywołań Ludusavi.
        """
        return await asyncio.to_thread(self._card_badges)

    def _card_badges(self) -> dict:
        out = {}
        for record in self._registry().all():
            appid = record.get("appid")
            if not appid:
                continue
            # Ikonka odpowiada na pytanie „czy karta jest w czytniku". Dla gry
            # z dysku to pytanie nie ma sensu, więc nie rysujemy jej wcale —
            # zielona kropka „zawsze obecna" byłaby myląca.
            if record.get("carrier") == "disk":
                continue
            out[str(appid)] = "green" if os.path.isfile(self._resolve_exe(record)) else "white"
        return out

    @guarded(lambda: {"error": "could not add the game from disk"})
    async def add_disk_game(self, exe_abs: str, title: str) -> dict:
        return await asyncio.to_thread(self._add_disk_game, exe_abs, title)

    def _add_disk_game(self, exe_abs: str, title: str) -> dict:
        """Rejestruje grę zainstalowaną na dysku konsoli.

        Plik wskazany NA KARCIE nie zostaje grą dyskową: rozpoznajemy to i zapisujemy
        grę jako kartową. Inaczej ta sama gra miałaby dwa nośniki i dwie tożsamości
        kopii, a przy przełożeniu karty nie dałoby się powiedzieć, który stan jest
        prawdziwy.
        """
        exe = os.path.realpath((exe_abs or "").strip())
        if not exe or not os.path.isfile(exe):
            return {"error": msg("no_such_file", path=exe_abs or "(empty)")}
        if not (title or "").strip():
            return {"error": msg("title_required")}
        for card in self._cards():
            mount = os.path.realpath(card["mount"])
            if exe.startswith(mount + os.sep):
                return self._register_game(
                    os.path.basename(os.path.dirname(exe)), title, exe,
                    card["label"], os.path.isfile(
                        os.path.join(os.path.dirname(exe), "steam_appid.txt")))
        record = self._registry().upsert({
            "title": title.strip(),
            "folder": os.path.basename(os.path.dirname(exe)),
            "card_label": "",
            "carrier": "disk",
            "exe_abs": exe,
            # PUSTE świadomie: naprawa ścieżek po przezwaniu karty dopasowuje kandydata
            # po `exe_rel` i folderze, a gra z dysku nie ma nic wspólnego z kartami
            "exe_rel": "",
            "proton": DEFAULT_PROTON,
            "ludusavi_unknown": False,
        })
        self._log().add("scan", msg("disk_game_added", title=title.strip(), path=exe))
        return record

    @guarded(lambda: {"ok": False, "error": "could not save the path"})
    async def set_exe(self, title_key: str, exe_abs: str) -> dict:
        return await asyncio.to_thread(self._set_exe, title_key, exe_abs)

    def _set_exe(self, title_key: str, exe_abs: str) -> dict:
        """Ręczne wskazanie pliku wykonywalnego gry.

        ZGŁOSZONE z urządzenia: automat (`scan.pick_exe`) wybrał dla jednej gry nie ten
        plik. Zgadywanie po heurystyce zawsze będzie się mylić na jakimś wydaniu, więc
        człowiek musi mieć jak to poprawić.

        Plik MUSI leżeć na karcie tej gry: ścieżkę zapisujemy też względną i to ona
        przeżywa przezwanie karty oraz przełożenie jej do drugiego urządzenia. Plik
        z dysku wewnętrznego dałby kafelek, który wstaje na jednym urządzeniu i nie
        wstaje na drugim — a taka gra i tak nie mogłaby jeździć na karcie.
        """
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        wanted = os.path.realpath((exe_abs or "").strip())
        if not wanted or not os.path.isfile(wanted):
            return {"ok": False, "error": msg("no_such_file", path=exe_abs)}
        mount = self._card_mount(record)
        if not mount:
            return {"ok": False,
                    "error": msg("card_not_in_reader", title=record.get("title"))}
        real_mount = os.path.realpath(mount)
        if not wanted.startswith(real_mount + os.sep):
            return {"ok": False,
                    "error": msg("file_not_on_card", card_label=record.get("card_label"))}
        updated = registry.set_fields(
            title_key, exe_abs=wanted,
            exe_rel=os.path.relpath(wanted, real_mount))
        self._log().add("scan", msg("exe_path_set", title=record.get("title"),
                                    path=updated["exe_rel"]))
        return {"ok": True, "appid": record.get("appid"), "exe_abs": wanted,
                "exe_rel": updated["exe_rel"]}

    def _metadata(self) -> Metadata:
        return Metadata(decky.DECKY_PLUGIN_SETTINGS_DIR)

    def _effective_lang(self, lang: str) -> str:
        """Język metadanych. Łańcuch z projektu wielojęzyczności: świadomy wybór
        człowieka bije język Steama, który podaje frontend (tylko on go zna)."""
        wybrany = self._ui_settings().get("lang", "auto")
        if wybrany and wybrany != "auto":
            return wybrany
        return (lang or "en")[:2].lower()

    @guarded(lambda: {})
    async def game_metadata(self, title_key: str, lang: str = "") -> dict:
        """Zapamiętane metadane gry albo pusty słownik. Bez sieci.

        `lang` puste = „daj, co masz" (starszy frontend). Podany język znaczy, że wpis
        w INNYM języku jest bezużyteczny: opis i gatunki są tłumaczone przez sklep."""
        return self._metadata().get(
            title_key, self._effective_lang(lang) if lang else "") or {}

    @guarded(lambda: {"error": "could not fetch metadata"})
    async def fetch_metadata(self, title_key: str, lang: str = "en") -> dict:
        """Pobiera metadane ze sklepu Steama. Sieć, więc przez `to_thread`.

        Zwracany kształt rozróżnia TRZY stany i frontend musi je rozróżniać razem z nim:
        dane, `missing: True` („Steam nie zna tej gry") i `error` („nie udało się
        zapytać"). Zlanie dwóch ostatnich w jedno odebrałoby grze opis na zawsze.
        """
        record = self._registry().get(title_key)
        if not record:
            return {"error": msg("record_not_found", title_key=title_key)}
        # wskazanie człowieka bije bazę Ludusaviego — po to istnieje
        return await asyncio.to_thread(
            self._metadata().fetch, title_key, record.get("title") or "",
            self._effective_lang(lang), record.get("steam_appid"))

    @guarded(dict)
    async def tile_metadata(self, lang: str = "") -> dict:
        """{appid: {description, developers, publishers}} — do wypełnienia zakładki
        „Informacje o grze" na ekranie gry Steama.

        TANIE i BEZ SIECI, jak `card_badges`: tylko rejestr i zapamiętane metadane.
        Front woła to przy starcie i po każdym pobraniu, więc pytanie do sklepu byłoby
        tu siecią za plecami użytkownika.
        """
        return await asyncio.to_thread(self._tile_metadata, lang)

    def _tile_metadata(self, lang: str) -> dict:
        jezyk = self._effective_lang(lang) if lang else ""
        zapamietane = self._metadata().all()
        out = {}
        for record in self._registry().all():
            appid = record.get("appid")
            wpis = zapamietane.get(record.get("title_key"))
            if not appid or not wpis or wpis.get("missing"):
                continue
            # Wpis w innym języku pomijamy: Steam pokazałby opis w języku, którego
            # użytkownik już nie używa, i nie byłoby po czym poznać dlaczego.
            if jezyk and wpis.get("lang") != jezyk:
                continue
            out[str(appid)] = {
                "description": wpis.get("description") or "",
                "developers": wpis.get("developers") or [],
                "publishers": wpis.get("publishers") or [],
            }
        return out

    @guarded(lambda: {})
    async def store_metadata(self, appid: int, lang: str = "") -> dict:
        """Metadane gry ZE STEAMA, po appidzie. Dla kafelków, których NIE obsługujemy.

        Osobna trasa od `fetch_metadata`, bo tożsamość jest inna: naszą grę znajdujemy
        po tytule w bazie Ludusaviego, a gra Steama ma appid wprost od Steama. Wpis nie
        wchodzi do rejestru i nie zmienia niczego w obsłudze zapisów — wtyczka rusza
        tylko wpisy z własnego rejestru.
        """
        jezyk = self._effective_lang(lang)
        meta = self._metadata()
        zapamietane = meta.get(Metadata.appid_key(appid), jezyk)
        if zapamietane:
            return zapamietane
        return await asyncio.to_thread(meta.fetch_by_appid, appid, jezyk)

    @guarded(lambda: {"title": None, "candidates": []})
    async def resolve_title(self, text: str) -> dict:
        """Nazwa, pod którą Ludusavi zna tę grę — dla tytułu wpisanego RĘCZNIE.

        ZMIERZONE na Decku: użytkownik wpisał „Baba is You", a baza zna „Baba Is You",
        i przez tę jedną literę każde wywołanie kończyło się „Brak informacji dla tych
        gier" — gra nie miała obsługi zapisów w ogóle. Tytuł jest w tym projekcie
        tożsamością gry, więc musi być TĄ nazwą, którą zna Ludusavi.

        `title: None` znaczy „nie zna" i nie wolno tego zamienić na wpisany tekst —
        wtedy interfejs ma powiedzieć wprost, że zapisy nie będą obsługiwane.
        """
        return await asyncio.to_thread(self._resolve_title, text)

    def _resolve_title(self, text: str) -> dict:
        if not (text or "").strip():
            return {"title": None, "candidates": []}
        runner = getattr(self, "canonical_title_runner", None)
        found = titles.canonical_title(text.strip(), runner=runner)
        return {"title": found.get("title"), "candidates": found.get("candidates") or []}

    @guarded(list)
    async def search_titles(self, text: str, limit: int = 20) -> list:
        """Tytuły z bazy Ludusaviego zawierające wpisany fragment — do wyboru z listy.

        `resolve_title` odpowiada na „jak baza nazywa TĘ grę" i wymaga trafienia
        w tytuł co do znaku. To za mało: ZMIERZONE na Decku, „Marvel Tōkon: Fighting
        Souls" ma makron, którego nie ma na klawiaturze ekranowej Steama, a Ludusavi
        na „Marvel Tokon" odpowiada `unknownGames`. Szczegóły pomiaru i powód, dla
        którego nie robi tego `--fuzzy`, są w `titles.search_titles`.
        """
        return await asyncio.to_thread(
            titles.search_titles, text or "", getattr(self, "manifest_override", None),
            int(limit or 20))

    @guarded(dict)
    async def register_game(self, folder: str, title: str, exe_abs: str,
                            card_label: str, neutralize_steamworks: bool) -> dict:
        # przemianowanie steam_appid.txt i zapis rejestru to dysk karty SD:
        # zawieszona pętla zdarzeń to niedostarczone mark_running (zasada 4)
        return await asyncio.to_thread(self._register_game, folder, title, exe_abs,
                                       card_label, neutralize_steamworks)

    def _register_game(self, folder: str, title: str, exe_abs: str,
                       card_label: str, neutralize_steamworks: bool) -> dict:
        game_dir = os.path.dirname(exe_abs)
        if neutralize_steamworks:
            steamworks.neutralize(game_dir)
        # stan czytamy z dysku, nie z wyniku wywołania: rekord musi zgadzać się z
        # rzeczywistością także wtedy, gdy plik był zneutralizowany wcześniej
        neutralized = steamworks.state(game_dir) == "neutralized"
        mount = next((c["mount"] for c in self._cards()
                      if c["label"] == card_label), None)
        # przez realpath, bo exe_abs mogło przyjść ścieżką z symlinku; bez exe_rel
        # rekord nie odnajdzie gry po przełożeniu karty do drugiego urządzenia
        real_exe = os.path.realpath(exe_abs)
        exe_rel = (os.path.relpath(real_exe, mount)
                   if mount and real_exe.startswith(mount + os.sep) else "")
        record = self._registry().upsert({
            "title": title,
            "folder": folder,
            "card_label": card_label,
            "exe_abs": exe_abs,
            "exe_rel": exe_rel,
            "steamworks_neutralized": neutralized,
            "proton": DEFAULT_PROTON,
            # nowy tytuł = nowa szansa dla Ludusavi; bez tego poprawiona nazwa nadal
            # byłaby pomijana i nie dałoby się wyjść z tego stanu
            "ludusavi_unknown": False,
        })
        self._log().add("scan", msg("game_registered_neutralized", title=title)
                                if neutralized else msg("game_registered", title=title))
        return record

    @guarded(dict)
    async def set_appid(self, title_key: str, appid: int) -> dict:
        return await asyncio.to_thread(self._set_appid, title_key, appid)

    def _set_appid(self, title_key: str, appid: int) -> dict:
        registry = self._registry()
        if not registry.get(title_key):
            # bez tego frontend dostawał surowe "KeyError: 'nazwa'" z Pythona
            return {"error": msg("record_not_found_refresh", title_key=title_key)}
        return registry.set_fields(title_key, appid=appid)

    @guarded(list)
    async def games(self) -> list:
        # rejestr + glob po /run/media + odczyt playtime.json Z KARTY: karta po wybudzeniu
        # odpowiada sekundami, a zawieszona pętla zdarzeń to niedostarczone mark_running
        return await asyncio.to_thread(self._games)

    def _games(self) -> list:
        records = self._registry().all()
        totals, card, mine, device = self._playtime(records)
        out = []
        for record in records:
            record = dict(record)
            record["exe_abs"] = self._resolve_exe(record)
            record["available"] = os.path.isfile(record["exe_abs"] or "")
            # „nie ma pliku gry" to DWA różne stany: karty nie ma w czytniku (czekamy)
            # albo karta jest, a gry na niej nie ma (gra usunięta → kafelek może zniknąć).
            # Bez tego rozróżnienia interfejs przy wyjętej karcie proponowałby usunięcie
            # całej biblioteki.
            record["card_present"] = bool(self._card_mount(record))
            key = record.get("title_key") or ""
            record["playtime_total"] = totals.get(key, 0)
            record["playtime_devices"] = playtime.per_device(card, mine, device, key)
            out.append(record)
        return out

    @guarded(dict)
    async def set_flag(self, title_key: str, field: str, value: bool) -> dict:
        return await asyncio.to_thread(self._set_flag, title_key, field, value)

    def _set_flag(self, title_key: str, field: str, value: bool) -> dict:
        if field not in EDITABLE_FIELDS:
            return {"error": msg("field_not_editable", field=field)}
        registry = self._registry()
        if not registry.get(title_key):
            return {"error": msg("record_not_found_refresh", title_key=title_key)}
        return registry.set_fields(title_key, **{field: EDITABLE_FIELDS[field](value)})

    @guarded(lambda: {"ok": False, "error": "could not archive to the card"})
    async def archive_to_card(self, title_key: str) -> dict:
        return await asyncio.to_thread(self._archive_to_card, title_key)

    def _archive_to_card(self, title_key: str) -> dict:
        """Odkłada zapis i czas gry NA KARTĘ przed usunięciem kafelka ze Steama.

        Gra zniknęła z karty (przełożona, skasowana), więc kafelek nie ma po co
        zostawać — ale zapis MUSI, bo karta jest jedynym nośnikiem, który wróci razem
        z grą. Kolejność jest wiążąca: kopia na kartę idzie PRZED zdjęciem kafelka,
        bo Ludusavi rozwiązuje prefiks gry non-Steam przez `shortcuts.vdf` — po
        zniknięciu kafelka nie miałby już czego szukać.

        Zwraca `appid`, żeby wywołujący (front) mógł zdjąć kafelek: to jedyna część,
        której nie da się zrobić z Pythona.
        """
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        title = record.get("title") or ""
        card_dir = self._card_saves_dir(record)
        if not card_dir:
            return {"ok": False, "error": msg("archive_no_card", title=title)}
        saves = self._saves()
        try:
            with saves.lock():
                # None = ta gra nie ma zapisów (G2) i to NIE jest awaria; False = jest,
                # ale kopia nie doszła — wtedy usuwanie kafelka odcięłoby jedyny egzemplarz
                outcome = saves.card_backup_many([title], card_dir).get(title)
                if outcome is False:
                    return {"ok": False,
                            "error": msg("archive_backup_failed", title=title,
                                        detail=_stderr_hint(saves))}
                if outcome is True:
                    remember_card_copy(
                        registry, record, card_dir,
                        (saves.card_when_many([title], card_dir) or {}).get(title))
        except SyncLocked as exc:
            return {"ok": False, "error": exc.msg}
        # czas gry musi wyjechać na kartę razem z zapisem — w rejestrze zniknie
        # z wpisem gry, a na karcie przeżyje i wróci przy ponownym skanie
        mount = self._card_mount(record)
        seconds = record.get("playtime_seconds") or 0
        if mount and seconds:
            playtime.publish(mount, playtime.device_id(), {title_key: seconds})
        self._log().add("scan", msg("archive_done", title=title))
        return {"ok": True, "appid": record.get("appid"), "title": title,
                "had_saves": outcome is True}

    @guarded(dict)
    async def forget_game(self, title_key: str) -> dict:
        # przywrócenie steam_appid.txt sięga na kartę — to samo ryzyko co w games()
        return await asyncio.to_thread(self._forget_game, title_key)

    def _forget_game(self, title_key: str) -> dict:
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        restored = False
        if record.get("steamworks_neutralized"):
            game_dir = os.path.dirname(self._resolve_exe(record))
            restored = bool(game_dir) and steamworks.restore(game_dir)
            if not restored:
                # gra przestaje być obsługiwana, ale plik został przemianowany —
                # cisza zostawiłaby użytkownika z niedziałającym Steamworks
                self._log().add("error", msg("steamworks_restore_failed",
                                             title=record.get("title")))
        registry.remove(title_key)
        self._log().add("scan", msg("game_forgotten_restored", title=record.get("title"))
                                if restored else msg("game_forgotten", title=record.get("title")))
        return {"ok": True, "steamworks_restored": restored}

    @guarded(dict)
    async def player_count(self, appid: int) -> dict:
        """Ilu ludzi gra TERAZ w tę grę na Steamie. `{"players": N}`, `{}` albo błąd.

        Bierze appid WPROST od wywołującego, bo obie karty już go znają: nasza gra ma
        go w metadanych (albo we wskazaniu ręcznym), a gra Steama jest nim sama.
        Dzięki temu ta liczba działa też dla gier z karty SD — czego wtyczka pytająca
        wyłącznie o gry Steama nie umie.

        Sieć, więc przez `to_thread`; szczegóły pamięci podręcznej przy `Metadata.players`.
        """
        return await asyncio.to_thread(self._metadata().players, appid)

    @guarded(lambda: "steamos")
    async def device_kind(self) -> str:
        """„deck" / „machine" / „steamos" — NA CZYM to działa.

        ZMIERZONE i używane w tym projekcie od początku do rozpoznawania urządzeń:
        `/sys/devices/virtual/dmi/id/product_name` to `Jupiter` na Steam Decku
        i `Fremont` na Steam Machine. Nieznana nazwa to „steamos", a nie zgadywanie:
        SteamOS chodzi też na zwykłych pecetach.

        Po co: karta na ekranie gry pokazywała TRZY pastylki zgodności naraz i napisy
        zjadały jej pół szerokości (ZGŁOSZONE). Człowiek stoi przy jednym urządzeniu,
        więc interesuje go jedna odpowiedź — a pełne rozbicie zostaje na naszym ekranie,
        gdzie jest miejsce.
        """
        try:
            with open("/sys/devices/virtual/dmi/id/product_name", encoding="utf-8") as plik:
                nazwa = plik.read().strip().lower()
        except OSError:
            return "steamos"
        if nazwa == "jupiter":
            return "deck"
        if nazwa == "fremont":
            return "machine"
        return "steamos"

    def _hltb(self) -> Hltb:
        return Hltb(decky.DECKY_PLUGIN_SETTINGS_DIR)

    @guarded(dict)
    async def hltb_times(self, title_key: str) -> dict:
        """Czas przejścia NASZEJ gry. Sieć, więc przez `to_thread`.

        Appid ze sklepu podajemy, gdy go znamy: nazwa bywa dwuznaczna („Gothic" wobec
        „Gothic 1 Remake"), a appid nie jest — i to jest jedyna rzecz, którą możemy
        zrobić lepiej niż wtyczka szukająca wyłącznie po nazwie.
        """
        record = self._registry().get(title_key)
        if not record:
            return {"error": msg("record_not_found", title_key=title_key)}
        appid = record.get("steam_appid") or (
            self._metadata().get(title_key) or {}).get("steam_appid")
        return await asyncio.to_thread(
            self._hltb().times, title_key, record.get("title") or "", appid)

    @guarded(dict)
    async def store_hltb_times(self, appid: int, name: str = "") -> dict:
        """To samo dla gry ZE STEAMA, której nie mamy w rejestrze.

        Osobna trasa, bo tożsamość jest inna: naszą grę znajdujemy po tytule z rejestru,
        a gra Steama ma appid wprost od Steama — i to on rozstrzyga dopasowanie w HLTB.
        Nazwę bierzemy z zapamiętanych metadanych, żeby nie pytać sklepu drugi raz.
        """
        numer = int(appid or 0)
        klucz = Hltb.appid_key(numer)
        tytul = (name or "").strip() or (
            self._metadata().get(Metadata.appid_key(numer)) or {}).get("name") or ""
        return await asyncio.to_thread(self._hltb().times, klucz, tytul, numer)

    @guarded(list)
    async def store_search(self, text: str, lang: str = "") -> list:
        """Gry w sklepie Steama pod wpisaną frazą — [{appid, name}].

        Do ręcznego wskazania, z której gry brać opis i ocenę. Baza Ludusaviego wiąże
        tytuł z JEDNYM appidem i potrafi trafić w inne wydanie (ZMIERZONE: „Grand Theft
        Auto V" → 271590 Legacy, a na karcie leży Enhanced), a bywa też, że appidu
        nie zna wcale. Sieć, więc przez `to_thread`.
        """
        return await asyncio.to_thread(self._metadata().search, text or "",
                                       self._effective_lang(lang))

    @guarded(lambda: {"ok": False})
    async def set_store_appid(self, title_key: str, appid: int, lang: str = "") -> dict:
        """Zapamiętanie ręcznie wskazanej gry ze sklepu i pobranie jej metadanych.

        `appid=0` kasuje wskazanie i wraca do bazy Ludusaviego. Wpis w pamięci
        podręcznej kasujemy ZAWSZE: opisywał poprzednią grę, a zostawienie go
        pokazywałoby cudzy opis do najbliższego wygaśnięcia.
        """
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        numer = int(appid or 0)
        registry.set_fields(title_key, steam_appid=numer or None)
        self._metadata().forget(title_key)
        self._log().add("scan",
                        msg("appid_override_set", title=record.get("title"), appid=numer)
                        if numer else
                        msg("appid_override_cleared", title=record.get("title")))
        meta = await asyncio.to_thread(
            self._metadata().fetch, title_key, record.get("title") or "",
            self._effective_lang(lang), numer or None)
        return {"ok": True, "steam_appid": numer or None, "metadata": meta}

    @guarded(lambda: {"ok": False})
    async def retitle(self, title_key: str, title: str) -> dict:
        """Zmiana tytułu gry JUŻ dodanej — czyli zmiana jej TOŻSAMOŚCI.

        ZGŁOSZONE z urządzenia: automat (albo człowiek przy dodawaniu) potrafi trafić
        w inne wydanie tej samej gry, a wtedy nie da się tego cofnąć inaczej niż
        zdejmując kafelek. ZMIERZONE, że to nie jest teoretyczne: baza Ludusaviego
        wiąże „Grand Theft Auto V" z appidem 271590, czyli wydaniem Legacy, a na karcie
        użytkownika leży Enhanced.

        Od tytułu zależą TRZY rzeczy naraz i wszystkie muszą pojechać razem: klucz
        rejestru, nazwa katalogu kopii Ludusaviego na karcie i klucz w pliku wymiany
        czasu gry. Zostawienie któregokolwiek pod starą nazwą daje grę, która „zgubiła"
        zapisy albo godziny — przy odpowiedzi RPC bez błędu, czyli awarię wyglądającą
        na sukces (zasada 1).

        Czego tu NIE ma i to jest decyzja: kopia pod starą nazwą zostaje w chmurze.
        Transportem są karta i rejestr, więc nic nie ginie, a kasowanie cudzych
        katalogów w chmurze jest nieodwracalne i nie jest tego warte — mówimy o tym
        w logu zdarzeń i zostawiamy sprzątanie człowiekowi.
        """
        return await asyncio.to_thread(self._retitle, title_key, title)

    def _retitle(self, old_key: str, title: str) -> dict:
        registry = self._registry()
        record = registry.get(old_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=old_key)}
        wanted = (title or "").strip()
        if not wanted:
            return {"ok": False, "error": msg("title_required")}
        old_title = record.get("title") or ""
        # Zasada 4: zapisy uruchomionej gry są nietykalne, a przemianowanie katalogu
        # kopii spod ręki grającego jest dokładnie ich ruszaniem.
        if old_title in self._running_set():
            return {"ok": False, "error": msg("retitle_running", title=old_title)}
        new_key = key_of_title(wanted)
        if new_key != old_key and registry.get(new_key):
            return {"ok": False, "error": msg("retitle_taken", title=wanted)}
        if wanted == old_title:
            return {"ok": True, "title_key": old_key, "title": old_title,
                    "appid": record.get("appid")}
        # ponytail: sam zamek w pamięci procesu, bez plikowego. Plikowy chroni przed
        # decky-ludusavi, a ono nie zna naszych katalogów na karcie i nie przemianuje
        # ich; przed WŁASNYM przebiegiem chroni ten.
        if not self._sync_lock.acquire(blocking=False):
            return {"ok": False, "error": msg("sync_already_running")}
        try:
            # Karty nie ma w czytniku → nie ma jak przemianować katalogu kopii, a bez
            # tego rejestr szukałby zapisów pod nową nazwą, a na karcie leżałyby pod
            # starą. ZMIERZONE na Decku: taka zmiana przechodziła i meldowała sukces
            # (rejestr, kafelek, log), a gra traciła zapisy przy pierwszym włożeniu
            # karty. Gra z dysku konsoli ma nośnik ZAWSZE, więc jej to nie dotyczy.
            mount = self._card_mount(record)
            if not mount:
                return {"ok": False, "error": msg("card_not_in_reader", title=old_title)}
            problem = self._move_card_saves(mount, old_title, wanted, old_key, new_key)
            if problem:
                return {"ok": False, "error": problem}
            zmieniony, blad = registry.rename(old_key, wanted)
            if blad:
                # katalog na karcie już pojechał, więc cisza znaczyłaby grę bez kopii
                return {"ok": False, "error": msg("retitle_taken", title=wanted)}
        finally:
            self._sync_lock.release()
        # metadane i czas przejścia opisywały STARĄ grę — zostawienie ich pokazywałoby
        # opis i godziny cudzego tytułu
        self._metadata().forget(old_key)
        self._hltb().forget(old_key)
        log = self._log()
        log.add("scan", msg("retitled", old=old_title, title=wanted))
        if self._cloud_enabled():
            log.add("scan", msg("retitle_cloud_leftover", old=old_title))
        return {"ok": True, "title_key": new_key, "title": wanted,
                "appid": zmieniony.get("appid")}

    def _move_card_saves(self, mount: str, old_title: str, title: str,
                         old_key: str, new_key: str):
        """Katalog kopii Ludusaviego i klucz czasu gry na karcie pod nową nazwą.

        Nazwa katalogu NIE jest tytułem: Ludusavi zamienia w niej dwukropek na
        podkreślenie (ZMIERZONE: „The Binding of Isaac: Rebirth" → „The Binding of
        Isaac_ Rebirth"), stąd `backup_dir_name` po obu stronach.
        """
        saves_dir = paths.card_saves_dir(mount)
        source = os.path.join(saves_dir, backup_dir_name(old_title))
        target = os.path.join(saves_dir, backup_dir_name(title))
        if os.path.isdir(source):
            try:
                os.rename(source, target)
            except OSError as exc:
                return msg("retitle_card_busy", title=old_title, detail=str(exc))
        # plik wymiany czasu gry: klucze to title_key, więc stary zostałby sierotą
        # i godziny z DRUGIEGO urządzenia wypadłyby z sumy
        path = playtime.file_path(mount)
        try:
            with open(path, encoding="utf-8") as handle:
                carried = json.load(handle)
        except (OSError, ValueError):
            return None                      # nie ma czego przenosić
        if not isinstance(carried, dict) or old_key not in carried:
            return None
        carried[new_key] = carried.pop(old_key)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(carried, handle, ensure_ascii=False, indent=1, sort_keys=True)
        except OSError as exc:
            # zapisy pojechały, godziny nie — to strata ozdoby, nie stanu, więc
            # mówimy o tym w logu i nie cofamy całej operacji
            self._log().add("error", msg("retitle_card_busy", title=old_title,
                                         detail=str(exc)))
        return None

    @guarded(dict)
    async def set_excluded(self, title_key: str, excluded: bool) -> dict:
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"error": msg("record_not_found_refresh", title_key=title_key)}
        updated = registry.set_fields(title_key, excluded=bool(excluded))
        self._log().add("scan", msg("game_excluded", title=record.get("title"))
                                if excluded else msg("game_included", title=record.get("title")))
        return updated

    @guarded(empty_sync)
    async def sync_all(self, title_keys: list = None) -> dict:
        """`title_keys=None` → cała biblioteka bez gier wykluczonych;
        lista kluczy → tylko te gry (wskazanie z ekranu bije wykluczenie)."""
        return await self._sync_entry(title_keys)

    @guarded(empty_sync)
    async def sync_game(self, title_key: str) -> dict:
        return await self._sync_entry([title_key])

    async def _sync_entry(self, title_keys) -> dict:
        if not self._sync_lock.acquire(blocking=False):
            problem = msg("sync_already_running")
            self._log().add("pull", problem)
            result = empty_sync()
            result["error"] = problem
            result["errors"].append(problem)  # front pokazuje errors, cisza tu myli
            return result
        try:
            return await asyncio.to_thread(self._sync_all, title_keys)
        finally:
            self._sync_lock.release()

    def _sync_all(self, title_keys=None) -> dict:
        saves = self._saves()
        try:
            with saves.lock():
                return self._sync_locked(saves, title_keys)
        except SyncLocked as exc:
            problem = exc.msg  # gotowy komunikat z warstwy zapisów
            self._log().add("error", problem)
            result = empty_sync()
            result["error"] = problem
            result["errors"].append(problem)
            return result

    def _sync_locked(self, saves, title_keys=None) -> dict:
        # is_running czyta zbiór ZA KAŻDYM RAZEM: mark_running dociera w trakcie
        # przebiegu (RPC jest wolne, bo tu jesteśmy w wątku), a kopia zbioru z chwili
        # startu przepuściłaby przywracanie na żywy zapis uruchomionej gry
        service = SyncService(self._registry(), saves,
                              is_running=lambda title: title in self._running_set(),
                              on_stage=self._set_stage,
                              card_dir=self._card_saves_dir,
                              cloud_enabled=self._cloud_enabled)
        try:
            result = service.sync_all(title_keys)
        finally:
            # etap musi zgasnąć także po wyjątku, inaczej frontend pokazuje w
            # nieskończoność robotę, której nikt już nie robi
            self._set_stage("")
        log = self._log()
        # czasy etapów w logu, bo „synchronizacja trwa długo" bez pomiaru nie mówi,
        # czy winne są wywołania Ludusavi, czy czekanie na rclone
        timing = " ".join("%s=%ss" % (name, value)
                          for name, value in service.last_seconds.items())
        log.add("pull", msg("sync_summary",
                            restored=", ".join(result["restored"]) or "—",
                            conflicts=", ".join(result["conflicts"]) or "—",
                            skipped=", ".join(result["skipped"]) or "—",
                            blocked=", ".join(result["blocked"]) or "—",
                            timing=timing or "—"))
        if result["errors"]:
            # JEDEN wpis, nie N: log jest okrągłym buforem 500 pozycji i przebieg na
            # dużej bibliotece wypchnąłby z niego całą resztę historii. Poszczególne
            # komunikaty użytkownik i tak dostaje PRZETŁUMACZONE przez `errors`
            # w wyniku RPC — ten wpis jest zapisem diagnostycznym.
            log.add("error", msg(
                "sync_problems",
                count=len(result["errors"]),
                detail="; ".join(e["message"] for e in result["errors"])
                       + _stderr_hint(saves)))
        decky.logger.info("NonSteam Sync: pull %s", result)
        return result

    @guarded(lambda: {"title": None, "ok": False, "conflict": False})
    async def push_after_game(self, appid: int) -> dict:
        return await asyncio.to_thread(self._push_after_game, appid)

    def _push_after_game(self, appid: int) -> dict:
        registry = self._registry()
        record = self._record_by_appid(appid)
        if not record:
            return {"title": None, "ok": False, "conflict": False,
                    "error": msg("appid_not_ours", appid=appid)}
        title = record["title"]
        saves = self._saves()
        log = self._log()
        try:
            # ten sam zamek plikowy co synchronizacja: backup(cloud=True) to rclone
            # W GÓRĘ na katalogu kopii, po którym trwający przebieg jedzie W DÓŁ
            with saves.lock():
                # Karta jest transportem zapisów: leży przy grze, nie potrzebuje sieci
                # i wraca do drugiego urządzenia razem z grą. Chmura jest kopią i idzie
                # DRUGA — gdyby dostała stan, którego nie ma karta, drugie urządzenie
                # widziałoby „karta bez zmian" i grało od starszego zapisu.
                card_dir = self._card_saves_dir(record)
                on_card = None
                if card_dir:
                    on_card = saves.card_backup_many([title], card_dir).get(title)
                    if on_card is not False:
                        # tożsamość kopii MUSI trafić do rejestru tu, nie tylko
                        # w przebiegu synchronizacji — inaczej następny przebieg
                        # przywraca własną kopię z karty bez powodu
                        remember_card_copy(
                            registry, record, card_dir,
                            (saves.card_when_many([title], card_dir) or {}).get(title))
                    if on_card is False:
                        registry.set_fields(record["title_key"], pending_push=True)
                        problem = msg("push_no_card", title=title,
                                     detail=_stderr_hint(saves))
                        log.add("error", problem)
                        return {"title": title, "ok": False, "conflict": False,
                                "error": problem}
                if not self._cloud_enabled() or saves.cloud_configured() is False:
                    # Chmura jest kopią zapasową, nie transportem: kto jej nie
                    # skonfigurował, ma dostać działającą wtyczkę. Ale „nie ma gdzie
                    # zapisać" NIE może wyglądać jak sukces.
                    if on_card is not True:
                        problem = (msg("push_nowhere_no_card", title=title)
                                  if not card_dir
                                  else msg("push_nowhere_no_saves", title=title))
                        log.add("error", problem)
                        return {"title": title, "ok": False, "conflict": False,
                                "error": problem}
                    registry.set_fields(record["title_key"], pending_push=False,
                                        last_push_ts=time.time(),
                                        last_backup_ts=time.time())
                    log.add("push", msg("push_saved_card_only", title=title))
                    return {"title": title, "ok": True, "conflict": False}
                outcome = saves.backup(title)
        except SyncLocked as exc:
            # zapis użytkownika NIE może zginąć po cichu — najbliższy przebieg go dokończy
            registry.set_fields(record["title_key"], pending_push=True)
            problem = msg("push_deferred", title=title, detail=str(exc))
            log.add("error", problem)
            return {"title": title, "ok": False, "conflict": False, "error": problem}
        fields = {}
        if outcome["ok"]:
            fields["last_push_ts"] = fields["last_backup_ts"] = time.time()
            fields["conflict"] = outcome["conflict"]
            fields["pending_push"] = False
        elif outcome["conflict"]:
            fields["conflict"] = True
        else:
            fields["pending_push"] = True
        if fields:
            registry.set_fields(record["title_key"], **fields)
        if outcome["ok"]:
            push_code = "push_sent_conflict" if outcome["conflict"] else "push_sent"
        else:
            push_code = "push_failed_conflict" if outcome["conflict"] else "push_failed"
        log.add("push", msg(push_code, title=title))
        if not outcome["ok"]:
            log.add("error", msg("push_upload_failed", title=title,
                                 detail=_stderr_hint(saves)))
        decky.logger.info("NonSteam Sync: push %s → %s", title, outcome)
        return {"title": title, "ok": outcome["ok"], "conflict": outcome["conflict"]}

    @guarded(lambda: {"ok": False})
    async def resolve_conflict(self, title_key: str, choice: str) -> dict:
        return await asyncio.to_thread(self._resolve_conflict, title_key, choice)

    @guarded(lambda: {"ok": False})
    async def conflict_options(self, title_key: str) -> dict:
        """Trzy kopie tej gry i data każdej — do wyboru przy rozjeździe.

        ZGŁOSZONE: „nie wiem, który jest nowszy". Dat nie da się wywieść jedna
        z drugiej i każda ma inne źródło (szczegóły przy `Saves.cloud_when`
        i `Saves.live_save_when`), więc pytamy o wszystkie trzy. Każda jest TRÓJSTANEM:
        data / `""` („pytaliśmy, nie ma kopii") / `None` („nie wiem"). Zlanie dwóch
        ostatnich kazałoby wybierać w przekonaniu, że gdzieś nic nie ma — a mógłby
        tam leżeć najnowszy zapis.

        Sieć jest tylko w pytaniu o chmurę, więc wywołanie idzie przez `to_thread`
        i wisi tyle, ile rclone (ZMIERZONE: 4,4 s na grę).
        """
        return await asyncio.to_thread(self._conflict_options, title_key)

    def _conflict_options(self, title_key: str) -> dict:
        record = self._registry().get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        saves = self._saves()
        title = record["title"]
        card_dir = self._card_saves_dir(record)
        if card_dir:
            widziane = saves.card_when_many([title], card_dir)
            # None zamiast całego słownika = awaria wywołania, nie „karta pusta"
            card_when = None if widziane is None else widziane.get(title)
        else:
            card_when = None
        cloud_when = saves.cloud_when(title) if self._cloud_enabled() else None
        opcje = {
            "local": {"when": saves.live_save_when(title)},
            "card": {"when": card_when, "present": bool(card_dir),
                     "label": record.get("card_label") or ""},
            "cloud": {"when": cloud_when, "enabled": self._cloud_enabled()},
        }
        # „Najnowsza" liczona TU, nie na froncie: to porównanie znaczników czasu
        # w jednym formacie (ISO-8601 UTC), a nie rzecz do rysowania. Puste i None
        # nie startują — nieznana data nie może wygrać ze znaną.
        znane = {gdzie: dane["when"] for gdzie, dane in opcje.items() if dane["when"]}
        opcje["newest"] = max(znane, key=znane.get) if znane else ""
        opcje["ok"] = True
        return opcje

    def _resolve_conflict(self, title_key: str, choice: str) -> dict:
        if choice not in ("local", "cloud", "card"):
            return {"ok": False, "error": msg("unknown_choice", choice=choice)}
        registry = self._registry()
        record = registry.get(title_key)
        if not record:
            return {"ok": False, "error": msg("record_not_found", title_key=title_key)}
        title = record["title"]
        saves = self._saves()
        log = self._log()
        card_dir = self._card_saves_dir(record)
        if choice == "card" and not card_dir:
            return {"ok": False, "error": msg("card_not_in_reader", title=title)}
        try:
            # ten sam zamek plikowy co synchronizacja: rozstrzyganie konfliktu rusza
            # katalog kopii i chmurę, więc nie może iść równolegle z przebiegiem
            with saves.lock():
                if choice == "card":
                    # kolejność jak w SyncService._card_phase i z tego samego powodu:
                    # przywracanie bez kopii bezpieczeństwa zamazuje żywy zapis bez odwrotu
                    if saves.safety_backup(title) is False:
                        log.add("error", msg("conflict_no_safety_backup", title=title,
                                             detail=_stderr_hint(saves)))
                        return {"ok": False,
                                "error": msg("conflict_safety_backup_failed")}
                    ok = saves.card_restore(title, card_dir)
                    if ok:
                        # własny katalog kopii musi poznać przywrócony stan, inaczej
                        # najbliższy przebieg uzna go za nasz nowy postęp i zacznie
                        # wywozić go na kartę bez końca
                        saves.backup(title, cloud=False)
                        swiezy = saves.card_when_many([title], card_dir) or {}
                        remember_card_copy(registry, record, card_dir, swiezy.get(title))
                    log.add("pull", msg("conflict_card_restored" if ok
                                        else "conflict_card_failed", title=title))
                elif choice == "local":
                    # NIEZMIENNIK: chmura nigdy nie dostaje stanu, którego nie ma karta.
                    # Wcześniej ta gałąź robiła kopię lokalną i od razu wysyłkę — czyli
                    # chmura mogła wyprzedzić kartę, a wtedy drugie urządzenie widzi
                    # „karta bez zmian", gra od starszego zapisu i nie ma jak dowiedzieć
                    # się o nowszym (ten scenariusz z pociągu z AGENTS.md).
                    ok = True
                    if card_dir:
                        ok = (saves.card_backup_many([title], card_dir) or {}).get(title) is True
                        if ok:
                            swiezy = saves.card_when_many([title], card_dir) or {}
                            remember_card_copy(registry, record, card_dir,
                                               swiezy.get(title))
                        else:
                            log.add("error", msg("card_write_failed", title=title))
                    # filtr gry OBOWIĄZKOWY: bez listy gier operacja chmurowa
                    # przepisuje CAŁĄ chmurę stanem lokalnym — po przełożeniu karty
                    # to utrata kopii pozostałych gier
                    if ok and self._cloud_enabled():
                        ok = (saves.backup(title, cloud=False)["ok"]
                              and saves.cloud_upload([title]))
                    log.add("push", msg("conflict_local_sent" if ok
                                        else "conflict_local_failed", title=title))
                else:
                    # bez kopii zapasowej przywrócenie z chmury zamazałoby lokalny zapis
                    # bez odwrotu — tej samej zasady trzyma się SyncService.
                    # `is False` z kontraktu: None = ta gra nie ma tu zapisów, czyli nie
                    # ma czego chronić (pierwsze uruchomienie na drugim urządzeniu)
                    if saves.safety_backup(title) is False:
                        log.add("error", msg("conflict_no_safety_backup", title=title,
                                             detail=_stderr_hint(saves)))
                        return {"ok": False, "error": msg("conflict_safety_backup_failed")}
                    ok = saves.cloud_download([title]) and saves.restore(title)
                    log.add("pull", msg("conflict_cloud_restored" if ok
                                        else "conflict_cloud_failed", title=title))
        except SyncLocked as exc:
            log.add("error", msg("conflict_lock_error", title=title, detail=str(exc)))
            return {"ok": False, "error": exc.msg}
        if ok:
            registry.set_fields(title_key, conflict=False, last_backup_ts=time.time())
        else:
            log.add("error", msg("conflict_resolve_failed", title=title, choice=choice,
                                 detail=_stderr_hint(saves)))
        return {"ok": ok}

    @guarded(lambda: None)
    async def mark_running(self, appid: int, running: bool) -> None:
        record = self._record_by_appid(appid)
        if not record:
            return None
        if running:
            self._running_set().add(record["title"])
        else:
            self._running_set().discard(record["title"])
        return None

    # --- czas gry ---

    @guarded(lambda: {"ok": False, "total": 0})
    async def add_playtime(self, appid: int, seconds: float) -> dict:
        # zapis na kartę to dysk — w pętli zdarzeń wisiałaby cała wtyczka
        return await asyncio.to_thread(self._add_playtime, appid, seconds)

    @guarded(lambda: {"ok": False, "total": 0})
    async def seed_playtime(self, appid: int, seconds: float) -> dict:
        """Historia sprzed wtyczki — przy PRZEJMOWANIU kafelka dodanego ręcznie.

        USTAWIA licznik na max(dotychczasowy, seconds), nie dodaje. Dodawanie
        podwoiłoby użytkownikowi czas przy powtórzonym przejęciu (drugi skan,
        ponowna rejestracja gry), a liczba Steama jest za każdym razem ta sama.
        """
        return await asyncio.to_thread(self._add_playtime, appid, seconds, True)

    def _add_playtime(self, appid: int, seconds: float, seed: bool = False) -> dict:
        try:
            gained = int(float(seconds))
        except (TypeError, ValueError):
            return {"ok": False, "total": 0,
                    "error": msg("invalid_seconds", seconds=seconds)}
        # ujemny przyrost odjąłby użytkownikowi już nagrany czas (zegar cofnięty
        # przez NTP po starcie sesji potrafi to zrobić), więc go nie przyjmujemy
        if gained <= 0:
            return {"ok": True, "total": 0}
        record = self._record_by_appid(appid)
        if not record:
            return {"ok": False, "total": 0,
                    "error": msg("appid_not_ours", appid=appid)}
        key = record["title_key"]
        device = playtime.device_id()
        mount = self._card_mount(record)
        # Karta jest JEDYNYM nośnikiem historii tego urządzenia. Rejestr bywa świeżo
        # zerowy (przeinstalowana wtyczka, forget_game + ponowna rejestracja), a wtedy
        # publikacja z rejestru skasowałaby na karcie dziesiątki godzin.
        card = playtime.read(mount) if mount else {}
        known = max(record.get("playtime_seconds") or 0,
                    (card.get(key) or {}).get(device, 0))
        # `seed` to przejęta historia (ta sama liczba przy każdym przejęciu),
        # więc bierzemy większą z dwóch, nie sumę
        here = max(known, gained) if seed else known + gained
        self._registry().set_fields(key, playtime_seconds=here)
        # karta wyjęta = suma z innych urządzeń jest niedostępna, ale NASZ licznik
        # już siedzi w rejestrze i trafi na kartę przy następnej sesji
        card = playtime.publish(mount, device, {key: here}) if mount else {}
        if not mount:
            self._log().add("error", msg("playtime_card_missing", title=record["title"]))
        return {"ok": True,
                "total": playtime.totals(card, {key: here}, device).get(key, here)}

    @guarded(dict)
    async def playtime_by_appid(self) -> dict:
        """{appid: sekundy} — łączny czas ze wszystkich urządzeń, do podmiany
        liczby na kafelku Steama. Klucze tekstem, bo idą przez JSON."""
        # wołane przy starcie i po każdej sesji, a czyta playtime.json z karty
        return await asyncio.to_thread(self._playtime_by_appid)

    def _playtime_by_appid(self) -> dict:
        records = self._registry().all()
        totals, _card, _mine, _device = self._playtime(records)
        return {str(r["appid"]): totals.get(r.get("title_key") or "", 0)
                for r in records if r.get("appid")}

    def _set_stage(self, name: str) -> None:
        self._stage = name or ""

    @guarded(str)
    async def sync_stage(self) -> str:
        """Etap trwającego przebiegu albo pusty łańcuch. Odpytywanie zamiast zdarzeń:
        robota siedzi w wątku, a pytanie raz na sekundę jest tańsze niż kanał zdarzeń."""
        return self._stage or ""

    # Pozycja panelu na ekranie gry. „left" domyślnie, bo prawą stronę zajmuje
    # popularna wtyczka hltb-for-deck i panele nachodziły na siebie (ZMIERZONE).
    UI_DEFAULTS = {"game_page": "left", "sync_cloud": "on",
                   "badge_pos": "bottom-right", "lang": "auto",
                   "game_page_steam": "on"}
    UI_ALLOWED = {"game_page": ("left", "right", "bar", "off"),
                  # „off" = zapisy jeżdżą TYLKO na karcie. Nie to samo co brak
                  # konfiguracji chmury: to wybór, więc nie ma o nim błędu.
                  "sync_cloud": ("on", "off"),
                  # narożnik ikonki karty na kafelku; „off" = nie rysuj wcale
                  "badge_pos": ("bottom-right", "bottom-left", "top-right",
                                "top-left", "off"),
                  # Język interfejsu. „auto" = ten, którym mówi Steam. Klucz należy do
                  # wielojęzyczności (`docs/superpowers/specs/2026-08-22-wielojezycznosc-design.md`),
                  # a stoi tu już teraz, bo metadane ze sklepu przychodzą PRZETŁUMACZONE
                  # i bez niego świadomy wybór człowieka nie miałby jak na nie wpłynąć.
                  "lang": ("auto", "pl", "en"),
                  # Karta informacyjna także na ekranie gier ZE STEAMA. Gra Steama nie
                  # wchodzi przez to pod opiekę wtyczki — zapisów jej nie tykamy,
                  # to wyłącznie informacja, której Valve nie pokazuje na tym ekranie.
                  "game_page_steam": ("on", "off")}

    def _ui_file(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, UI_FILE)

    @guarded(lambda: dict(Plugin.UI_DEFAULTS))
    async def get_ui_settings(self) -> dict:
        return self._ui_settings()

    def _ui_settings(self) -> dict:
        # wersja synchroniczna: czytają ją też ścieżki spoza RPC (wyjście z gry,
        # przebieg synchronizacji), a te biegną w wątku
        settings = dict(self.UI_DEFAULTS)
        try:
            with open(self._ui_file(), encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return settings  # brak pliku = domyślne; uszkodzony plik nie może zgasić UI
        for key, value in (stored or {}).items():
            if key in self.UI_ALLOWED and value in self.UI_ALLOWED[key]:
                settings[key] = value
        return settings

    @guarded(dict)
    async def set_ui_setting(self, key: str, value: str) -> dict:
        allowed = self.UI_ALLOWED.get(str(key))
        if not allowed:
            return {"error": msg("unknown_setting", key=key)}
        if value not in allowed:
            # cicha akceptacja rozjechałaby układ ekranu gry i nikt by nie wiedział czemu
            return {"error": msg("invalid_setting_value", value=value, key=key,
                                 allowed=", ".join(allowed))}
        settings = self._ui_settings()
        settings[str(key)] = value
        os.makedirs(os.path.dirname(self._ui_file()), exist_ok=True)
        with open(self._ui_file(), "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False)
        return settings

    @guarded(lambda: None)
    async def log_add(self, kind: str, message: str) -> None:
        """Wpis zgłoszony przez frontend. Istnieje dla awarii, których backend nie
        widzi — nieudane wstrzyknięcie sekcji w ekran gry Steama. Bez tego taka
        porażka byłaby cicha, a log zdarzeń jest jedynym oknem diagnostycznym."""
        self._log().add(str(kind), str(message))

    @guarded(list)
    async def log_tail(self, count: int = 50) -> list:
        log = self._log()
        try:
            return log.tail(int(count))
        except (TypeError, ValueError):
            # EventLog.tail sam podstawia 50, ale front dostałby wtedy log bez słowa
            # o zignorowanym argumencie; komunikat wchodzi jako wpis, bo to jedyny
            # kształt, który ten widok pokazuje
            problem = msg("log_tail_bad_count", count=count)
            return [{"ts": time.time(), "kind": "error", "code": problem["code"],
                     "params": problem["params"],
                     "message": problem["message"]}] + log.tail(50)

    # --- grafiki ---

    @guarded(dict)
    async def artwork_for(self, title: str) -> dict:
        return await asyncio.to_thread(self._artwork_for, title)

    def _artwork_for(self, title: str) -> dict:
        art = self._artwork()
        if not art.api_key:
            problem = msg("no_sgdb_key")
            self._log().add("error", problem)
            return {"error": problem}
        game_id = art.find_game(title)
        if not game_id:
            problem = msg("sgdb_no_game", title=title,
                          detail=art.last_error or "empty response")
            self._log().add("error", problem)
            return {"error": problem}
        assets = art.assets_for(game_id)
        if not assets:
            problem = msg("sgdb_no_assets", title=title,
                          detail=art.last_error or "every kind came back empty")
            self._log().add("error", problem)
            return {"error": problem}
        return assets

    @guarded(str)
    async def artwork_base64(self, url: str) -> str:
        # pobranie obrazka to sieć — w pętli zdarzeń wisiałaby cała wtyczka
        return await asyncio.to_thread(self._artwork().as_base64, url)

    @guarded(lambda: None)
    async def set_sgdb_key(self, key: str) -> None:
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SGDB_KEY_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write((key or "").strip())
        os.chmod(path, 0o600)  # osobisty klucz API użytkownika
        return None

    @guarded(bool)
    async def has_sgdb_key(self) -> bool:
        return await asyncio.to_thread(self._has_sgdb_key)

    def _has_sgdb_key(self) -> bool:
        return bool(self._artwork().api_key)

    @guarded(lambda: {"configured": None})
    async def cloud_configured(self) -> dict:
        """Czy Ludusavi ma ustawiony cloud.remote. Trójstan True/False/None —
        None znaczy "nie ma jak sprawdzić" i tak też pokazujemy to w interfejsie.
        Chmurę konfiguruje się POZA wtyczką (`ludusavi cloud set`), my tylko czytamy.
        Ścieżkę bierzemy z Saves.config_path, żeby nie zgadywać katalogu flatpaka."""
        return {"configured": cloud_remote_set(self._saves().config_path)}

    @guarded(dict)
    async def set_artwork_done(self, title_key: str, done: bool) -> dict:
        registry = self._registry()
        if not registry.get(title_key):
            return {"error": msg("record_not_found", title_key=title_key)}
        return registry.set_fields(title_key, artwork_done=bool(done))

    # --- cykl życia ---

    async def _main(self):
        self._running = set()
        decky.logger.info("NonSteam Sync: start (ustawienia: %s, runtime: %s)",
                          decky.DECKY_PLUGIN_SETTINGS_DIR, decky.DECKY_PLUGIN_RUNTIME_DIR)

    async def _unload(self):
        decky.logger.info("NonSteam Sync: stop")


def _stderr_hint(saves) -> str:
    """Ogon stderr Ludusavi w logu zdarzeń — bez tego użytkownik widzi tylko
    „nieudane" i nie ma czego pokazać.

    Nasza własna diagnoza bije stderr: gdy kopia objęła obcy prefiks, Ludusavi
    kończy kodem 0 i NIC nie pisze na stderr — a to jest właśnie ta awaria,
    która wcześniej wyglądała jak sukces."""
    problem = " ".join((getattr(saves, "last_problem", "") or "").split())
    if problem:
        return " [%s]" % problem
    text = " ".join((saves.last_stderr or "").split())
    return " [ludusavi: %s]" % text[-200:] if text else ""
