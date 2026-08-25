"""Testy warstwy RPC wtyczki (plugin/main.py).

Sedno: obsługa zdarzeń wtyczki nie może być zajęta pracą Ludusavi/rclone.
`mark_running` to jedyny bezpiecznik chroniący żywy zapis uruchomionej gry,
więc MUSI działać w trakcie trwającej synchronizacji.
"""
import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.messages import msg
from sdsync.registry import Registry
from sdsync.saves import sync_lock

_MAIN = os.path.join(os.path.dirname(__file__), "..", "main.py")

# opóźnienie udające podproces (Ludusavi/rclone) — na tyle długie, że gdyby
# działało w pętli zdarzeń, pomiar odpowiedzi RPC by je zobaczył
SLOW = 0.3


def _load_main(tmp_path):
    """main.py importuje `decky` (jest tylko na urządzeniu) — podstawiamy atrapę."""
    stub = types.ModuleType("decky")
    stub.logger = logging.getLogger("sdsync-test")
    stub.DECKY_PLUGIN_SETTINGS_DIR = str(tmp_path / "settings")
    stub.DECKY_PLUGIN_RUNTIME_DIR = str(tmp_path / "runtime")
    sys.modules["decky"] = stub
    spec = importlib.util.spec_from_file_location("sdsync_main_%s" % id(tmp_path), _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SlowSaves:
    """Każde wywołanie „podprocesu" śpi — tak jak prawdziwe Ludusavi.

    Zamek jest PRAWDZIWY (sdsync.saves.sync_lock) — atrapa z własnym zamkiem
    w pamięci nie pokazałaby, czy plik faktycznie powstaje i czy chroni przed
    drugim procesem."""

    def __init__(self, title, lock_path, safety=True):
        self.title = title
        self.lock_path = lock_path
        self.safety = safety
        self.last_stderr = ""
        self.calls = []
        self.lock_present = []   # czy plik zamka istniał w trakcie każdej operacji
        self.cloud_args = {}
        self.card_args = []
        self.card_ok = True
        self.card_when_value = None
        self.cloud_ready = True

    def lock(self):
        return sync_lock(self.lock_path)

    def _slow(self, name, value):
        self.calls.append(name)
        self.lock_present.append(os.path.exists(self.lock_path))
        time.sleep(SLOW)
        return value

    def cloud_state(self):
        return self._slow("cloud_state", (True, {self.title}, set()))

    def local_changed_many(self, titles):
        return {t: self.local_changed(t) for t in titles}

    def safety_backup_many(self, titles):
        return {t: self.safety_backup(t) for t in titles}

    def local_changed(self, title):
        return self._slow("local_changed", False)

    def safety_backup(self, title):
        return self._slow("safety_backup", self.safety)

    def card_backup_many(self, titles, path):
        self.card_args.append(path)
        return self._slow("card_backup", {t: self.card_ok for t in titles})

    def card_when_many(self, titles, path):
        self.card_args.append(path)
        return self._slow("card_when", {t: self.card_when_value for t in titles})

    def cloud_configured(self):
        return self.cloud_ready

    def card_restore(self, title, path):
        return self._slow("card_restore", True)

    def cloud_download(self, games=None):
        self.cloud_args["download"] = games
        return self._slow("cloud_download", True)

    def cloud_upload(self, games=None):
        self.cloud_args["upload"] = games
        return self._slow("cloud_upload", True)

    def restore(self, title):
        return self._slow("restore", True)

    def backup(self, title, cloud=True):
        return self._slow("backup", {"ok": True, "changed_bytes": 1, "conflict": False})


def _plugin(tmp_path, title="Animal Well", appid=4242, safety=True):
    main = _load_main(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": title, "appid": appid, "folder": "AnimalWell"})
    saves = SlowSaves(title, str(tmp_path / "runtime" / "sdsync-sync.lock"), safety)
    plugin = main.Plugin()
    plugin._saves = lambda: saves
    return main, plugin, saves


def test_mark_running_dziala_w_trakcie_synchronizacji(tmp_path):
    """Kryterium przyjęcia G1: bezpiecznik dociera W TRAKCIE przebiegu i jest widziany."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        task = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)          # przebieg już trwa
        started = time.monotonic()
        await plugin.mark_running(4242, True)
        latency = time.monotonic() - started
        return latency, await task, saves

    latency, result, saves = asyncio.run(scenario())
    assert latency < SLOW, "mark_running czekał %.2fs na zajętą pętlę zdarzeń" % latency
    assert result["blocked"] == ["Animal Well"], result
    assert result["restored"] == [], "żywy zapis uruchomionej gry został nadpisany"
    assert "restore" not in saves.calls


def test_ping_odpowiada_w_trakcie_synchronizacji(tmp_path):
    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        task = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)
        started = time.monotonic()
        answer = await plugin.ping()
        latency = time.monotonic() - started
        # przebieg wciąż musi trwać — inaczej „szybki ping" tylko dlatego, że
        # zablokowana pętla oddała sterowanie już PO całej synchronizacji
        in_flight = not task.done()
        await task
        return answer, latency, in_flight

    answer, latency, in_flight = asyncio.run(scenario())
    assert answer == "pong"
    assert in_flight, "pętla zdarzeń oddała sterowanie dopiero po całej synchronizacji"
    assert latency < SLOW, "ping czekał %.2fs" % latency


def test_drugie_rownolegle_sync_all_odrzucone(tmp_path):
    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        first = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)
        started = time.monotonic()
        second = await plugin.sync_all()
        latency = time.monotonic() - started
        return second, latency, await first

    second, latency, first = asyncio.run(scenario())
    assert latency < SLOW, "odrzucenie zajęło %.2fs — czyli czekało, a nie odrzuciło" % latency
    assert second["error"] and second["error"]["code"] == "sync_already_running", second
    assert second["errors"], "odrzucenie musi być widoczne w wyniku, nie cicho pominięte"
    assert first["restored"] == ["Animal Well"], first  # pierwszy przebieg nietknięty


def test_artwork_for_bez_klucza_mowi_dlaczego(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    result = asyncio.run(plugin.artwork_for("Animal Well"))
    assert result.get("error"), "cicha porażka: brak klucza API bez słowa wyjaśnienia"
    assert any(e.get("code") == "no_sgdb_key" for e in plugin._log().tail(10))


def test_set_appid_nieznanej_gry_bez_surowego_wyjatku(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    result = asyncio.run(plugin.set_appid("nie-ma-takiej", 7))
    assert result.get("error")
    assert "KeyError" not in result["error"]["message"], result["error"]
    assert result["error"]["code"] == "record_not_found_refresh", result["error"]
    assert result["error"]["params"]["title_key"] == "nie-ma-takiej", result["error"]


# --- wpięcie gotowych modułów (blokada plikowa, filtr gry, log) ---


def test_zamek_plikowy_zyje_tylko_w_trakcie_synchronizacji(tmp_path):
    """Ochrona przed DRUGIM PROCESEM: zamek musi być plikiem na dysku, nie zmienną."""
    _, plugin, saves = _plugin(tmp_path)
    result = asyncio.run(plugin.sync_all())
    assert result["restored"] == ["Animal Well"], result
    assert saves.lock_present and all(saves.lock_present), \
        "operacje Ludusavi szły bez pliku zamka: %s" % list(zip(saves.calls, saves.lock_present))
    assert not os.path.exists(saves.lock_path), "zamek został po zakończonym przebiegu"


def test_synchronizacja_ustepuje_zamkowi_innego_procesu(tmp_path):
    """Zamek trzyma ŻYWY obcy proces (prawdziwy pid, nie wymyślony) — wtyczka
    nie ma prawa ruszyć Ludusavi ani udać, że wszystko poszło dobrze."""
    _, plugin, saves = _plugin(tmp_path)
    os.makedirs(os.path.dirname(saves.lock_path), exist_ok=True)
    other = subprocess.Popen(["sleep", "30"])
    try:
        with open(saves.lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d 2026-08-20 10:00:00" % other.pid)
        result = asyncio.run(plugin.sync_all())
    finally:
        other.terminate()
        other.wait()
    assert saves.calls == [], "wtyczka weszła w chmurę mimo zajętego zamku: %s" % saves.calls
    assert result["errors"] and result["errors"][0]["code"] == "sync_lock_held", result
    assert result["error"], "porażka bez pola error wygląda na czysty przebieg"
    assert result["restored"] == [] and result["skipped"] == []


def test_rozstrzyganie_konfliktu_tez_bierze_zamek(tmp_path):
    main, plugin, saves = _plugin(tmp_path)
    os.makedirs(os.path.dirname(saves.lock_path), exist_ok=True)
    other = subprocess.Popen(["sleep", "30"])
    try:
        with open(saves.lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d 2026-08-20 10:00:00" % other.pid)
        result = asyncio.run(plugin.resolve_conflict("animal-well", "cloud"))
    finally:
        other.terminate()
        other.wait()
    assert result["ok"] is False
    assert result["error"]["code"] == "sync_lock_held", result
    assert "already running" in result["error"]["message"].lower(), result
    assert saves.calls == [], saves.calls


def test_konflikt_lokalny_wysyla_do_chmury_tylko_te_gre(tmp_path):
    """Bez filtra gry cloud_upload() przepisuje CAŁĄ chmurę stanem lokalnym."""
    _, plugin, saves = _plugin(tmp_path)
    result = asyncio.run(plugin.resolve_conflict("animal-well", "local"))
    assert result["ok"] is True, result
    assert saves.cloud_args["upload"] == ["Animal Well"], saves.cloud_args


def test_konflikt_z_chmury_pobiera_tylko_te_gre(tmp_path):
    _, plugin, saves = _plugin(tmp_path)
    result = asyncio.run(plugin.resolve_conflict("animal-well", "cloud"))
    assert result["ok"] is True, result
    assert saves.cloud_args["download"] == ["Animal Well"], saves.cloud_args


def test_konflikt_gry_bez_lokalnych_zapisow_da_sie_rozstrzygnac(tmp_path):
    """safety_backup() = None znaczy „nie ma czego chronić" (zmierzone: gra bez
    zapisów). `not` zamiast `is False` odmawiał tu rozstrzygnięcia."""
    _, plugin, saves = _plugin(tmp_path, safety=None)
    result = asyncio.run(plugin.resolve_conflict("animal-well", "cloud"))
    assert result["ok"] is True, result
    assert "restore" in saves.calls


def test_konflikt_przy_zepsutej_kopii_nic_nie_rusza(tmp_path):
    _, plugin, saves = _plugin(tmp_path, safety=False)
    result = asyncio.run(plugin.resolve_conflict("animal-well", "cloud"))
    assert result["ok"] is False
    assert result["error"]["code"] == "conflict_safety_backup_failed", result
    assert "restore" not in saves.calls and "cloud_download" not in saves.calls


def test_log_tail_z_nieliczbowym_argumentem_mowi_o_tym(tmp_path):
    """RPC dostaje z frontendu, co przyjdzie. Log to jedyne okno diagnostyczne —
    ani traceback, ani cicha pusta lista."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    plugin._log().add("scan", "wpis który MUSI być widoczny")
    out = asyncio.run(plugin.log_tail("nie-liczba"))
    assert any("widoczny" in e["message"] for e in out), out
    assert any(e.get("code") == "log_tail_bad_count" for e in out), out
    assert all(set(e) >= {"ts", "kind", "message"} for e in out), out


def test_main_nie_ma_wlasnej_kopii_event_loga(tmp_path):
    """Naprawiony błąd w module nie może żyć dalej w kopii wewnątrz main.py."""
    main = _load_main(tmp_path)
    from sdsync.log import EventLog
    assert main.EventLog is EventLog
    source = open(_MAIN, encoding="utf-8").read()
    assert "class EventLog" not in source


# --- ustawienia gry z ekranu i czas gry ---

def test_set_flag_never_lets_the_frontend_touch_identity_fields(tmp_path):
    """Biała lista jest zabezpieczeniem, nie kosmetyką: appid wiąże rejestr z
    kafelkiem Steama, a `conflict` chroni zapisy. Ani jednego, ani drugiego
    nie wolno przestawić kliknięciem z interfejsu."""
    _, plugin, _ = _plugin(tmp_path)
    for field in ("appid", "conflict", "title", "playtime_seconds", "exe_abs"):
        result = asyncio.run(plugin.set_flag("animal-well", field, True))
        # kod komunikatu jest tu istotny: samo "error" dostajemy także z wyjątku
        # złapanego przez @guarded, więc taki test przechodził przy WYŁĄCZONEJ
        # białej liście — czyli nie sprawdzał reguły, tylko przypadek
        error = result.get("error") or {}
        assert error.get("code") == "field_not_editable", (field, result)
        assert error.get("params", {}).get("field") == field, (field, result)
    assert asyncio.run(plugin.games())[0]["appid"] == 4242


def test_set_flag_switches_exclusion(tmp_path):
    _, plugin, _ = _plugin(tmp_path)
    assert asyncio.run(plugin.set_flag("animal-well", "excluded", True))["excluded"] is True
    assert asyncio.run(plugin.games())[0]["excluded"] is True


def test_set_flag_of_unknown_game_is_a_message_not_a_crash(tmp_path):
    _, plugin, _ = _plugin(tmp_path)
    result = asyncio.run(plugin.set_flag("nie-ma", "excluded", True))
    # samo "error" dostajemy także z wyjątku złapanego przez @guarded — sprawdzamy REGUŁĘ
    error = result.get("error") or {}
    assert error.get("code") == "record_not_found_refresh", result
    assert error.get("params", {}).get("title_key") == "nie-ma", result


def test_playtime_accumulates_across_sessions(tmp_path):
    _, plugin, _ = _plugin(tmp_path)
    asyncio.run(plugin.add_playtime(4242, 600))
    asyncio.run(plugin.add_playtime(4242, 300))
    game = asyncio.run(plugin.games())[0]
    assert game["playtime_seconds"] == 900
    assert game["playtime_total"] == 900


def test_playtime_never_goes_backwards(tmp_path):
    """Zegar cofnięty przez NTP po starcie sesji daje ujemny przyrost. Odjęcie go
    skasowałoby użytkownikowi czas, który naprawdę przegrał."""
    _, plugin, _ = _plugin(tmp_path)
    asyncio.run(plugin.add_playtime(4242, 600))
    for bad in (-60, 0, "sporo", None):
        asyncio.run(plugin.add_playtime(4242, bad))
    assert asyncio.run(plugin.games())[0]["playtime_seconds"] == 600


def test_playtime_of_foreign_appid_is_refused(tmp_path):
    """Wtyczka rusza wyłącznie wpisy z własnego rejestru — także licząc czas."""
    _, plugin, _ = _plugin(tmp_path)
    result = asyncio.run(plugin.add_playtime(999999, 600))
    assert result["ok"] is False and "error" in result


def test_playtime_by_appid_feeds_the_steam_tile(tmp_path):
    _, plugin, _ = _plugin(tmp_path)
    asyncio.run(plugin.add_playtime(4242, 3600))
    assert asyncio.run(plugin.playtime_by_appid()) == {"4242": 3600}


def test_sync_stage_is_readable_while_the_run_is_still_going(tmp_path):
    """ZMIERZONE: przebieg trwa 20–120 s i to czekanie na rclone. Skoro nie da się
    go skrócić, frontend musi móc powiedzieć, NA CO czekamy — inaczej przycisk
    mówi „Pracuję…" przez dwie minuty i wygląda na zawieszony."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        task = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)
        w_trakcie = await plugin.sync_stage()
        await task
        return w_trakcie, await plugin.sync_stage()

    w_trakcie, po = asyncio.run(scenario())
    # pierwszy etap to podgląd lokalny: karmi fazę karty, która idzie przed chmurą
    assert w_trakcie == "podglad_lokalny", w_trakcie
    assert po == "", "etap musi zgasnąć po przebiegu"


def test_sync_stage_is_cleared_even_when_the_run_explodes(tmp_path):
    """Etap, który nie gaśnie po awarii, pokazuje w nieskończoność robotę,
    której nikt już nie robi."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)

        def boom():
            raise RuntimeError("chmura padła")

        saves.cloud_state = boom
        await plugin.sync_all()
        return await plugin.sync_stage()

    assert asyncio.run(scenario()) == ""


def test_push_after_game_never_runs_while_a_sync_holds_the_lock(tmp_path):
    """Regresja: wysyłka po grze woła backup(cloud=True), czyli rclone W GÓRĘ na tym
    samym katalogu kopii, po którym trwający przebieg jedzie W DÓŁ. Zamek plikowy
    istnieje właśnie po to; wysyłka jako jedyna go nie brała."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        task = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)          # przebieg trwa i trzyma zamek
        result = await plugin.push_after_game(4242)
        await task
        return result, saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is False, "wysyłka weszła w katalog kopii w trakcie przebiegu"
    assert "error" in result


def test_deferred_push_is_recorded_so_the_next_run_can_finish_it(tmp_path):
    """Nieudana wysyłka bez śladu w rejestrze = po przełożeniu karty drugie urządzenie
    gra ze STARSZEGO zapisu z chmury. Zasada 1: awaria nie może zniknąć."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        task = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)
        await plugin.push_after_game(4242)
        await task
        return main

    main = asyncio.run(scenario())
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    assert registry.get("animal-well")["pending_push"] is True


def test_playtime_from_the_card_is_never_lowered_by_an_empty_registry(tmp_path):
    """Przeinstalowanie wtyczki zeruje playtime_seconds w rejestrze. Gdyby to zero
    biło liczbę z karty, pierwsza sesja opublikowałaby „30 minut" w miejsce 40 godzin —
    a karta jest JEDYNYM nośnikiem tej historii."""
    main, plugin, _ = _plugin(tmp_path)
    mount = tmp_path / "SD256"
    (mount / "Games").mkdir(parents=True)
    plugin._cards = lambda: [{"label": "SD256", "mount": str(mount),
                              "games_dir": str(mount / "Games")}]
    device = main.playtime.device_id()
    main.playtime.publish(str(mount), device, {"animal-well": 144000})  # 40 h

    asyncio.run(plugin.add_playtime(4242, 1800))  # pół godziny

    card = main.playtime.read(str(mount))
    assert card["animal-well"][device] == 144000 + 1800


def test_games_does_not_block_the_event_loop(tmp_path):
    """Zasada 6. games() sięga na kartę SD; gdy karta odpowiada wolno, pętla zdarzeń
    musi zdążyć obsłużyć mark_running — inaczej wraca zasada 4."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        original = plugin._cards

        def slow_cards():
            time.sleep(SLOW)
            return original()

        plugin._cards = slow_cards
        task = asyncio.create_task(plugin.games())
        await asyncio.sleep(SLOW / 2)
        started = time.monotonic()
        await plugin.ping()
        latency = time.monotonic() - started
        # odczyt karty musi jeszcze trwać — inaczej „szybki ping" tylko dlatego, że
        # zablokowana pętla oddała sterowanie już PO całym games()
        in_flight = not task.done()
        await task
        return latency, in_flight

    latency, in_flight = asyncio.run(scenario())
    assert in_flight, "pętla zdarzeń oddała sterowanie dopiero po całym games()"
    assert latency < SLOW, "games() zatrzymało pętlę zdarzeń na %.2fs" % latency


def test_missing_appid_never_matches_an_unregistered_game(tmp_path):
    """Swiezo zarejestrowana gra ma appid=None. Wywolanie z null trafiloby w nia
    i zrobilo kopie CUDZEJ gry, czyszczac przy okazji jej flage konfliktu."""
    main, plugin, _ = _plugin(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": "Gothic 1 Remake"})  # bez appid
    for bad in (None, 0):
        assert plugin._record_by_appid(bad) is None, bad


def test_successful_push_records_the_backup_markers(tmp_path):
    _, plugin, _ = _plugin(tmp_path)
    result = asyncio.run(plugin.push_after_game(4242))
    assert result == {"title": "Animal Well", "ok": True, "conflict": False}
    record = asyncio.run(plugin.games())[0]
    assert record["last_push_ts"] and record["last_backup_ts"]


def test_failed_push_never_records_a_backup_marker(tmp_path):
    """Reguła 1: rejestr nie może meldować „wysłano", gdy w chmurze nic nie ma —
    po przełożeniu karty drugie urządzenie zagrałoby ze starszego zapisu."""
    _, plugin, saves = _plugin(tmp_path)
    saves.backup = lambda title, cloud=True: {"ok": False, "changed_bytes": 0,
                                              "conflict": False}
    result = asyncio.run(plugin.push_after_game(4242))
    assert result["ok"] is False
    record = asyncio.run(plugin.games())[0]
    assert record["last_push_ts"] is None and record["last_backup_ts"] is None


def test_guarded_puts_the_reason_into_the_result(tmp_path):
    """Frontend nie ma konsoli: jedynym nośnikiem przyczyny jest pole `error`
    w wyniku i wpis w logu zdarzeń. Bez tego awaria wygląda jak pusty wynik."""
    _, plugin, saves = _plugin(tmp_path)

    def boom():
        raise RuntimeError("chmura padła")

    saves.cloud_state = boom
    result = asyncio.run(plugin.sync_all())
    assert result["error"]["code"] == "internal_error"
    assert "chmura padła" in result["error"]["params"]["detail"]
    assert any(e["code"] == "internal_error" for e in result["errors"])


def test_resolve_exe_finds_the_game_after_the_card_moved(tmp_path):
    """Przesłanka całego projektu: karta jeździ między urządzeniami i montuje się pod
    INNĄ ścieżką, więc zapisane exe_abs bywa cudze. exe_rel + aktualny punkt
    montowania jest jedynym sposobem, żeby gra nie wypadła jako „brak karty"."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    mount = tmp_path / "SD256"
    (mount / "Games" / "AnimalWell").mkdir(parents=True)
    exe = mount / "Games" / "AnimalWell" / "AnimalWell.exe"
    exe.write_text("x")
    plugin._cards = lambda: [{"label": "SD256", "mount": str(mount),
                              "games_dir": str(mount / "Games")}]

    record = {"exe_abs": "/run/media/cudze/SD256/Games/AnimalWell/AnimalWell.exe",
              "exe_rel": "Games/AnimalWell/AnimalWell.exe",
              "card_label": "SD256"}
    assert plugin._resolve_exe(record) == str(exe)


def test_card_mount_respects_the_card_label(tmp_path):
    """Przy dwóch kartach czas gry i rozwiązanie ścieżki nie mogą iść z pierwszej lepszej."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    plugin._cards = lambda: [{"label": "INNA", "mount": "/run/media/INNA", "games_dir": "x"},
                             {"label": "SD256", "mount": "/run/media/SD256", "games_dir": "y"}]
    assert plugin._card_mount({"card_label": "SD256"}) == "/run/media/SD256"


def test_playtime_by_appid_skips_games_without_a_tile(tmp_path):
    """Bez filtra powstaje klucz „None", który frontend próbowałby wciskać w kafelek."""
    main, plugin, _ = _plugin(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": "Gothic 1 Remake"})   # bez appid
    assert "None" not in asyncio.run(plugin.playtime_by_appid())
def test_sync_game_rusza_tylko_wskazana_gre(tmp_path):
    main, plugin, saves = _plugin(tmp_path)
    result = asyncio.run(plugin.sync_game("animal-well"))
    assert result["restored"] == ["Animal Well"], result
    assert saves.cloud_args["download"] == ["Animal Well"]


def test_sync_game_w_trakcie_sync_all_odrzucone(tmp_path):
    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        first = asyncio.create_task(plugin.sync_all())
        await asyncio.sleep(SLOW / 2)
        started = time.monotonic()
        second = await plugin.sync_game("animal-well")
        latency = time.monotonic() - started
        return second, latency, await first

    second, latency, first = asyncio.run(scenario())
    assert latency < SLOW, "odrzucenie czekało %.2fs zamiast odrzucić" % latency
    assert second["error"] and second["error"]["code"] == "sync_already_running", second
    assert second["errors"], "odrzucenie musi być widoczne w wyniku"
    assert first["restored"] == ["Animal Well"], first


def test_set_excluded_wylacza_gre_z_synchronizacji(tmp_path):
    main, plugin, saves = _plugin(tmp_path)
    record = asyncio.run(plugin.set_excluded("animal-well", True))
    assert record.get("excluded") is True, record
    result = asyncio.run(plugin.sync_all())
    assert result["restored"] == [], "pominięta gra została zsynchronizowana"
    assert "cloud_state" not in saves.calls, "Ludusavi wołane bez ani jednej gry"


def test_set_excluded_nieznanej_gry_bez_surowego_wyjatku(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    result = asyncio.run(plugin.set_excluded("nie-ma", True))
    assert result.get("error") and result["error"]["code"] == "record_not_found_refresh", result


def test_cloud_configured_czyta_konfiguracje_ludusavi(tmp_path):
    main = _load_main(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("cloud:\n  remote:\n    GoogleDrive:\n      id: x\n", encoding="utf-8")
    plugin = main.Plugin()
    plugin._saves = lambda: types.SimpleNamespace(config_path=str(config))
    assert asyncio.run(plugin.cloud_configured())["configured"] is True


def test_cloud_configured_nieznany_stan_nie_udaje_wiedzy(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    plugin._saves = lambda: types.SimpleNamespace(config_path=str(tmp_path / "nie-ma.yaml"))
    result = asyncio.run(plugin.cloud_configured())
    assert result["configured"] is None, "brak pliku znaczy nie wiem, nie nieskonfigurowana"


def test_set_artwork_done_odnotowuje_grafiki(tmp_path):
    main, plugin, _ = _plugin(tmp_path)
    record = asyncio.run(plugin.set_artwork_done("animal-well", True))
    assert record.get("artwork_done") is True, record


def test_log_add_zapisuje_wpis_widoczny_w_logu(tmp_path):
    """Frontend musi mieć jak zgłosić awarię, której backend nie widzi —
    nieudane wstrzyknięcie sekcji w ekran gry (ustalenie 11 etapu 5)."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    asyncio.run(plugin.log_add("error", "sekcja na ekranie gry: nie znaleziono miejsca"))
    entries = asyncio.run(plugin.log_tail(5))
    assert entries, "wpis z frontendu nie dotarł do logu"
    assert entries[0]["kind"] == "error"
    assert "nie znaleziono miejsca" in entries[0]["message"]


def test_ustawienia_ui_zapisuja_sie_i_wracaja(tmp_path):
    """Pozycja panelu na ekranie gry musi przeżyć restart wtyczki — bez tego
    użytkownik ustawia ją po każdym uruchomieniu Decka."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    assert asyncio.run(plugin.get_ui_settings())["game_page"] == "left", "domyślnie lewa strona (prawą zajmuje hltb-for-deck)"
    asyncio.run(plugin.set_ui_setting("game_page", "bar"))
    assert asyncio.run(plugin.get_ui_settings())["game_page"] == "bar"
    # nowa instancja czyta z dysku, nie z pamięci
    assert asyncio.run(main.Plugin().get_ui_settings())["game_page"] == "bar"


def test_ustawienia_ui_odrzucaja_nieznana_wartosc(tmp_path):
    """Nieznana pozycja rozjechałaby układ ekranu gry — lepiej błąd niż cisza."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    result = asyncio.run(plugin.set_ui_setting("game_page", "gdziekolwiek"))
    assert result.get("error"), result
    assert asyncio.run(plugin.get_ui_settings())["game_page"] == "left", "zła wartość została zapisana"


# --- wyjście z gry: karta przed chmurą ---

def test_wyjscie_z_gry_zapisuje_najpierw_na_karte(tmp_path):
    """Karta jest transportem zapisów, chmura kopią. Kolejność nie jest kosmetyczna:
    gdyby chmura dostała stan, którego nie ma karta, drugie urządzenie widziałoby
    „karta bez zmian" i grało od starszego zapisu — mając nowszy w chmurze."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        await plugin.push_after_game(4242)
        return saves

    saves = asyncio.run(scenario())
    assert "card_backup" in saves.calls, saves.calls
    assert saves.calls.index("card_backup") < saves.calls.index("backup"), saves.calls
    assert saves.card_args and saves.card_args[0].endswith("/SD256/.sdsync/saves")


def test_nieudana_kopia_na_karte_nie_wysyla_do_chmury(tmp_path):
    """Ten sam niezmiennik po stronie RPC: chmura nie może wyprzedzić karty."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        saves.card_ok = False
        plugin._card_mount = lambda record: str(tmp_path / "SD256")
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert "backup" not in saves.calls, saves.calls
    assert result["ok"] is False and result.get("error")


def test_bez_karty_w_czytniku_wysylka_do_chmury_dziala_jak_dawniej(tmp_path):
    """Karta wyjęta między wyjściem z gry a wysyłką — zapis nadal ma gdzie trafić."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is True
    assert "backup" in saves.calls and "card_backup" not in saves.calls


def test_wyjscie_z_gry_zapamietuje_tozsamosc_kopii_na_karcie(tmp_path):
    """Bez tego następny przebieg widzi na karcie „nieznaną" kopię (własną!) i
    przywraca ją bez powodu — a gdyby przy okazji istniał lokalny postęp, zgłosiłby
    rozjazd, którego nie ma. ZMIERZONE na Machine: po wysyłce w rejestrze nie było
    ani jednego wpisu `card_seen`."""

    async def scenario():
        main, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_when_value = "2026-08-20T20:56:09.1Z"
        await plugin.push_after_game(4242)
        return Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json")).get("animal-well")

    record = asyncio.run(scenario())
    assert record["card_seen"] == {"SD256": "2026-08-20T20:56:09.1Z"}, record["card_seen"]


def test_bez_chmury_zapis_na_karte_wystarcza_do_sukcesu(tmp_path):
    """Chmura jest KOPIĄ, nie transportem. Kto jej nie skonfigurował, ma dostać
    działającą wtyczkę, a nie „NIEUDANE" po każdej sesji."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_when_value = "2026-08-20T21:00:00Z"
        saves.cloud_ready = False
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is True, result
    assert "card_backup" in saves.calls
    assert "backup" not in saves.calls, "nieskonfigurowana chmura nie ma być wołana"


def test_bez_chmury_i_bez_karty_wysylka_nie_udaje_sukcesu(tmp_path):
    """Nie ma gdzie zapisać = nie ma sukcesu. Cisza tutaj znaczyłaby, że zapis
    użytkownika nie istnieje nigdzie poza żywym prefiksem, a wtyczka mówi „OK"."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        saves.cloud_ready = False
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is False and result.get("error")
    assert "backup" not in saves.calls


def test_suma_czasu_gry_przezywa_wyjecie_karty(tmp_path):
    """ZMIERZONE na Decku: po wyjęciu karty kafelek spadł z 7,1 min na 5,4 — plik
    wymiany leży na karcie, więc bez niej znamy tylko własne sekundy. Ostatnia znana
    suma musi zostać, inaczej liczba na kafelku maleje i wygląda to jak zgubiony czas."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        registry.set_fields("animal-well", playtime_seconds=322)
        karta = tmp_path / "SD256"
        (karta / ".sdsync").mkdir(parents=True)
        (karta / ".sdsync" / "playtime.json").write_text(
            '{"animal-well": {"SteamMachine-df725c00": 106}}', encoding="utf-8")

        plugin._card_mount = lambda record: str(karta)
        z_karta = await plugin.playtime_by_appid()
        plugin._card_mount = lambda record: None      # karta wyjęta
        bez_karty = await plugin.playtime_by_appid()
        return z_karta, bez_karty

    z_karta, bez_karty = asyncio.run(scenario())
    assert z_karta["4242"] == 428, z_karta       # 322 (Deck) + 106 (Machine)
    assert bez_karty["4242"] == 428, bez_karty   # suma nie może zmaleć do 322


def test_przejeta_historia_czasu_gry_nie_dubluje_sie(tmp_path):
    """Przejmując kafelek dodany ręcznie bierzemy też czas, który naliczył mu Steam —
    to jedyny ślad sesji sprzed wtyczki (jego licznik żyje w localconfig.vdf, nie
    w prefiksie). Ale MUSI to być ustawienie, nie dodawanie: powtórzone przejęcie
    (drugi skan, ponowna rejestracja gry) podwoiłoby użytkownikowi historię."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        pierwsze = await plugin.seed_playtime(4242, 5718)     # 95,3 min ze Steama
        drugie = await plugin.seed_playtime(4242, 5718)       # to samo jeszcze raz
        mniejsze = await plugin.seed_playtime(4242, 60)       # nie może cofnąć
        return pierwsze, drugie, mniejsze, Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json")).get("animal-well")

    pierwsze, drugie, mniejsze, record = asyncio.run(scenario())
    assert pierwsze["total"] == 5718, pierwsze
    assert drugie["total"] == 5718, "druga próba podwoiła historię"
    assert mniejsze["total"] == 5718, "mniejsza liczba cofnęła licznik"
    assert record["playtime_seconds"] == 5718


def test_przejeta_historia_nie_zjada_naszych_sesji(tmp_path):
    """Gdy już coś nagraliśmy sami, przejęta liczba Steama nie może tego zmniejszyć."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        await plugin.add_playtime(4242, 9000)          # nasza własna sesja
        return await plugin.seed_playtime(4242, 60)

    assert asyncio.run(scenario())["total"] == 9000


# --- gra zniknęła z karty: usuwamy ze Steama, ale NIE zapis ---

def test_archiwizacja_na_karte_przed_usunieciem_gry(tmp_path):
    """Gdy gra znika z karty, kafelek ma zniknąć ze Steama — ale zapis i czas gry
    MUSZĄ zostać na karcie, bo to jedyny nośnik, który wróci razem z grą.
    Kolejność jest wiążąca: najpierw kopia na kartę (Ludusavi znajduje prefiks przez
    kafelek, więc musi on jeszcze istnieć), potem zdjęcie kafelka, na końcu rejestr."""

    async def scenario():
        main, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_when_value = "2026-08-21T10:00:00Z"
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        registry.set_fields("animal-well", playtime_seconds=4242)
        wynik = await plugin.archive_to_card("animal-well")
        czas = json.loads((karta / ".sdsync" / "playtime.json").read_text("utf-8"))
        return wynik, saves, czas

    wynik, saves, czas = asyncio.run(scenario())
    assert wynik["ok"] is True and wynik["appid"] == 4242, wynik
    assert "card_backup" in saves.calls, saves.calls
    assert any(4242 == v for dev in czas.values() for v in dev.values()), czas


def test_bez_karty_nie_udajemy_ze_zapis_jest_bezpieczny(tmp_path):
    """Karty nie ma w czytniku → nie ma gdzie odłożyć zapisu, więc odmawiamy.
    Zgoda tutaj znaczyłaby usunięcie kafelka i prefiksu z zapisem, którego nikt
    nie skopiował."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        return await plugin.archive_to_card("animal-well")

    wynik = asyncio.run(scenario())
    assert wynik["ok"] is False and (wynik.get("error") or {}).get("code") == "archive_no_card"


def test_nieudana_kopia_na_karte_blokuje_usuniecie(tmp_path):
    """Kopia zawiodła = zapis jest tylko w prefiksie. Usunięcie kafelka po takim
    wyniku to utrata postępu."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_ok = False
        return await plugin.archive_to_card("animal-well")

    wynik = asyncio.run(scenario())
    assert wynik["ok"] is False and wynik.get("error")


def test_gra_bez_zapisow_da_sie_usunac(tmp_path):
    """Kod 0 i puste `games` to „ta gra nie ma zapisów" (G2), nie awaria — inaczej
    gry, w które nikt nie zagrał, zostałyby w bibliotece na zawsze."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_ok = None          # brak zapisów tej gry
        return await plugin.archive_to_card("animal-well")

    assert asyncio.run(scenario())["ok"] is True


def test_gra_zniknieta_z_karty_jest_do_odroznienia_od_wyjetej_karty(tmp_path):
    """Dwa różne stany, które wyglądają tak samo („nie ma pliku gry"):
    karty NIE MA w czytniku → czekamy, nic nie robimy;
    karta JEST, a gry na niej nie ma → gra została usunięta i kafelek może zniknąć.
    Bez tego rozróżnienia interfejsu nie da się zapytać „usunąć ze Steama?", bo przy
    wyjętej karcie zaproponowałby usunięcie całej biblioteki."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)

        plugin._card_mount = lambda record: None
        bez_karty = (await plugin.games())[0]
        plugin._card_mount = lambda record: str(karta)
        z_karta_bez_gry = (await plugin.games())[0]
        return bez_karty, z_karta_bez_gry

    bez_karty, z_karta_bez_gry = asyncio.run(scenario())
    assert bez_karty["available"] is False and bez_karty["card_present"] is False
    assert z_karta_bez_gry["available"] is False
    assert z_karta_bez_gry["card_present"] is True, "karta jest, więc gra zniknęła z niej"


def test_reczny_tytul_jest_rozpoznawany_przez_ludusavi(tmp_path):
    """ZMIERZONE na Decku: użytkownik wpisał „Baba is You", a baza Ludusavi zna
    „Baba Is You" — i przez tę jedną literę Ludusavi odpowiadał „Brak informacji dla
    tych gier", czyli gra nie miała obsługi zapisów wcale. To samo z „The Binding of
    Isaac Rebirth" (kanoniczne: „…Isaac: Rebirth", z dwukropkiem). Tytuł jest w tym
    projekcie tożsamością gry, więc musi być tą nazwą, którą zna Ludusavi."""
    # atrapa odwzorowuje ZMIERZONE odpowiedzi `find --api --normalized` z urządzenia
    zmierzone = {"Baba is You": "Baba Is You",
                 "The Binding of Isaac Rebirth": "The Binding of Isaac: Rebirth"}

    def runner(argv):
        trafienie = zmierzone.get(argv[-1])
        return 0, json.dumps({"games": {trafienie: {"score": 1.0}} if trafienie else {}})

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        plugin.canonical_title_runner = runner
        return (await plugin.resolve_title("Baba is You"),
                await plugin.resolve_title("The Binding of Isaac Rebirth"),
                await plugin.resolve_title(""),
                await plugin.resolve_title("Gra ktorej nie ma w bazie 12345"))

    baba, isaac, puste, nieznane = asyncio.run(scenario())
    assert baba["title"] == "Baba Is You", baba
    assert isaac["title"] == "The Binding of Isaac: Rebirth", isaac
    assert puste["title"] is None
    assert nieznane["title"] is None, "nieznany tytuł nie może udawać rozpoznanego"


def test_szukanie_tytulu_po_fragmencie_omija_ludusavi(tmp_path):
    """ZMIERZONE na Decku 2026-08-24: `find --normalized "Marvel Tokon"` odpowiada
    `unknownGames` (nie zdejmuje makronu z „Tōkon"), a `--fuzzy --multiple` na to samo
    zwraca „Mall Tycoon" i „Marco Polo". Wyszukiwarka musi więc czytać manifest, a nie
    wołać Ludusaviego — i ten test pilnuje, że RPC rzeczywiście tam sięga."""
    manifest = os.path.join(os.path.dirname(__file__), "fixtures",
                            "ludusavi_manifest_WYCINEK.yaml")

    def runner(argv):
        raise AssertionError("szukanie po fragmencie NIE MOŻE wołać Ludusaviego")

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        plugin.canonical_title_runner = runner
        plugin.manifest_override = manifest
        return (await plugin.search_titles("marvel tokon"),
                await plugin.search_titles("an", 2),
                await plugin.search_titles(""))

    tokon, dwa, puste = asyncio.run(scenario())
    assert tokon == ["Marvel Tōkon: Fighting Souls"], tokon
    assert len(dwa) == 2, "limit ma obowiązywać"
    assert puste == [], "pusty fragment nie może wysypać całej bazy na ekran"


def test_skan_poprawia_sciezke_gry_takze_bez_rozpoznanego_tytulu(tmp_path):
    """ZGŁOSZONE i ZMIERZONE na Decku: po przezwaniu karty na „Karta 1" trzy gry
    przestały się uruchamiać. Skan poprawił ścieżki tylko tym, którym Ludusavi
    potrafi nadać tytuł po nazwie folderu — gry dodane z ręcznie podanym tytułem
    zostały ze starą ścieżką w rejestrze (a więc i w kafelku Steama).

    Skan musi naprawiać ścieżkę KAŻDEJ znanej gry: dopasowanie idzie po ścieżce
    względnej na karcie, która przy przezwaniu się nie zmienia."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path, title="Baba Is You", appid=4242)
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        # folder i ścieżka względna to jedyne rzeczy, które przy przezwaniu karty
        # zostają takie same — na nich stoi dopasowanie
        registry.set_fields("baba-is-you", card_label="STARA-ETYKIETA", folder="Baba",
                            exe_rel="Games/Baba/Baba.exe",
                            exe_abs="/run/media/deck/STARA-ETYKIETA/Games/Baba/Baba.exe")
        karta = tmp_path / "Karta 1"
        (karta / "Games" / "Baba").mkdir(parents=True)
        (karta / "Games" / "Baba" / "Baba.exe").write_text("", encoding="utf-8")
        plugin._cards = lambda: [{"label": "Karta 1", "mount": str(karta),
                                  "games_dir": str(karta / "Games")}]
        await plugin.scan()
        return registry.get("baba-is-you")

    record = asyncio.run(scenario())
    assert record["card_label"] == "Karta 1", record
    assert record["exe_abs"].endswith("/Karta 1/Games/Baba/Baba.exe"), record


# --- ręczne wskazanie pliku .exe ---

def test_wskazanie_exe_zapisuje_sciezke_wzgledna_karty(tmp_path):
    """ZGŁOSZONE z urządzenia: dla „Invincible VS" automat wybrał nie ten plik.
    Ścieżkę WZGLĘDNĄ liczymy od punktu montowania karty — bezwzględna jest inna na
    każdym urządzeniu i po każdym przezwaniu karty, więc sama nie wystarczy."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "Karta 1"
        gra = karta / "Games" / "Invincible"
        gra.mkdir(parents=True)
        (gra / "zly.exe").write_text("", encoding="utf-8")
        (gra / "wlasciwy.exe").write_text("", encoding="utf-8")
        plugin._cards = lambda: [{"label": "Karta 1", "mount": str(karta),
                                  "games_dir": str(karta / "Games")}]
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        registry.set_fields("animal-well", card_label="Karta 1",
                            exe_abs=str(gra / "zly.exe"),
                            exe_rel="Games/Invincible/zly.exe")
        wynik = await plugin.set_exe("animal-well", str(gra / "wlasciwy.exe"))
        return wynik, registry.get("animal-well")

    wynik, record = asyncio.run(scenario())
    assert wynik["ok"] is True, wynik
    assert record["exe_rel"] == "Games/Invincible/wlasciwy.exe", record
    assert record["exe_abs"].endswith("/Karta 1/Games/Invincible/wlasciwy.exe")
    assert wynik["appid"] == 4242, "front musi dostać appid, żeby przestawić kafelek"


def test_wskazanie_pliku_poza_karta_jest_odrzucone(tmp_path):
    """Plik z dysku wewnętrznego nie pojedzie z kartą do drugiego urządzenia —
    kafelek wskazywałby tam w pustkę. Lepsza odmowa niż gra, która wstaje na
    jednym urządzeniu i nie wstaje na drugim."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "Karta 1"
        (karta / "Games").mkdir(parents=True)
        obcy = tmp_path / "gdzies-indziej.exe"
        obcy.write_text("", encoding="utf-8")
        plugin._cards = lambda: [{"label": "Karta 1", "mount": str(karta),
                                  "games_dir": str(karta / "Games")}]
        return await plugin.set_exe("animal-well", str(obcy))

    wynik = asyncio.run(scenario())
    assert wynik["ok"] is False and (wynik.get("error") or {}).get("code") == "file_not_on_card"


def test_wskazanie_nieistniejacego_pliku_jest_odrzucone(tmp_path):
    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        return await plugin.set_exe("animal-well", "/nie/ma/mnie.exe")

    wynik = asyncio.run(scenario())
    assert wynik["ok"] is False and wynik.get("error")


def test_stan_kropek_jest_tani_i_zalezy_od_karty(tmp_path):
    """Kropki muszą się przemalowywać po wyjęciu karty, więc front pyta o ten stan
    często — RPC nie może więc czytać czasu gry z karty ani niczego liczyć.
    Zielona = karta w czytniku, biała = grę obsługujemy, ale karty nie ma."""

    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "Karta 1"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        z_karta = await plugin.card_badges()
        plugin._card_mount = lambda record: None
        bez_karty = await plugin.card_badges()
        return z_karta, bez_karty

    z_karta, bez_karty = asyncio.run(scenario())
    assert z_karta == {"4242": "white"}, z_karta   # karta jest, ale pliku gry nie ma
    assert bez_karty == {"4242": "white"}, bez_karty


def test_kropka_zielona_gdy_plik_gry_jest_na_miejscu(tmp_path):
    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "Karta 1"
        gra = karta / "Games" / "AnimalWell"
        gra.mkdir(parents=True)
        (gra / "Animal Well.exe").write_text("", encoding="utf-8")
        Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json")
                 ).set_fields("animal-well", exe_abs=str(gra / "Animal Well.exe"),
                              exe_rel="Games/AnimalWell/Animal Well.exe")
        plugin._card_mount = lambda record: str(karta)
        return await plugin.card_badges()

    assert asyncio.run(scenario()) == {"4242": "green"}


def test_tylko_karta_konczy_wysylke_na_karcie(tmp_path):
    """Przy wyborze „tylko karta" wyjście z gry kończy się kopią na kartę i melduje
    sukces — bez wywołania chmurowego. Chmura jest kopią zapasową, a nie warunkiem."""

    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        karta = tmp_path / "SD256"
        (karta / "Games").mkdir(parents=True)
        plugin._card_mount = lambda record: str(karta)
        saves.card_when_value = "2026-08-21T12:00:00Z"
        await plugin.set_ui_setting("sync_cloud", "off")
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is True, result
    assert "card_backup" in saves.calls, saves.calls
    assert "backup" not in saves.calls, saves.calls


def test_tylko_karta_bez_karty_nie_udaje_sukcesu(tmp_path):
    async def scenario():
        _, plugin, saves = _plugin(tmp_path)
        plugin._card_mount = lambda record: None
        await plugin.set_ui_setting("sync_cloud", "off")
        return await plugin.push_after_game(4242), saves

    result, saves = asyncio.run(scenario())
    assert result["ok"] is False and result.get("error")
    assert "backup" not in saves.calls


# --- gry z dysku wewnętrznego (spec: 2026-08-22-gry-z-dysku-design.md) ---

def test_nosnikiem_gry_z_dysku_jest_katalog_na_dysku(tmp_path):
    """Kopia, tożsamość i czas gry mają działać bez zmian w logice — zmienia się
    tylko odpowiedź na pytanie „gdzie jest nośnik tej gry”."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        registry.set_fields("animal-well", carrier="disk", exe_rel="",
                            exe_abs=str(tmp_path / "gra" / "gra.exe"))
        record = registry.get("animal-well")
        return plugin._card_mount(record), plugin._card_saves_dir(record)

    mount, saves = asyncio.run(scenario())
    assert mount and mount.endswith("/dysk"), mount
    assert saves.endswith("/dysk/.sdsync/saves"), saves


def test_dodanie_gry_z_dysku_odrzuca_nieistniejacy_plik(tmp_path):
    async def scenario():
        _, plugin, _ = _plugin(tmp_path)
        return await plugin.add_disk_game(str(tmp_path / "nie-ma.exe"), "Hades")

    wynik = asyncio.run(scenario())
    assert wynik.get("error"), wynik


def test_plik_z_KARTY_zostaje_gra_kartowa(tmp_path):
    """Inaczej ta sama gra miałaby dwa nośniki i dwie tożsamości kopii."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        karta = tmp_path / "Karta 9"
        gra = karta / "Games" / "Hades"
        gra.mkdir(parents=True)
        exe = gra / "Hades.exe"
        exe.write_text("", encoding="utf-8")
        plugin._cards = lambda: [{"label": "Karta 9", "mount": str(karta),
                                  "games_dir": str(karta / "Games")}]
        wynik = await plugin.add_disk_game(str(exe), "Hades")
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        return wynik, registry.get("hades")

    wynik, record = asyncio.run(scenario())
    assert not wynik.get("error"), wynik
    assert record["carrier"] == "card", record
    assert record["card_label"] == "Karta 9", record
    assert record["exe_rel"] == "Games/Hades/Hades.exe", record


def test_gra_z_dysku_ma_pusta_sciezke_wzgledna(tmp_path):
    """Naprawa ścieżek po przezwaniu karty dopasowuje po `exe_rel` + folderze —
    pusty `exe_rel` trzyma gry dyskowe poza tym mechanizmem."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        gra = tmp_path / "Games" / "Hades"
        gra.mkdir(parents=True)
        exe = gra / "Hades.exe"
        exe.write_text("", encoding="utf-8")
        plugin._cards = lambda: []
        await plugin.add_disk_game(str(exe), "Hades")
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        return registry.get("hades")

    record = asyncio.run(scenario())
    assert record["carrier"] == "disk", record
    assert record["exe_rel"] == "", record
    assert record["folder"] == "Hades", record


def test_badge_karty_nie_dotyczy_gier_z_dysku(tmp_path):
    """Ikonka odpowiada na pytanie „czy karta jest w czytniku" — dla gry z dysku to
    pytanie nie ma sensu, więc nie rysujemy jej wcale."""

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        registry = Registry(os.path.join(
            main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
        registry.set_fields("animal-well", carrier="disk")
        return await plugin.card_badges()

    assert asyncio.run(scenario()) == {}


def test_sync_locked_unpacks_error_message_dicts_not_joins_them(tmp_path):
    """Regression, zmierzone na Decku 2026-08-22 16:48: `sync.py` zwraca
    `result["errors"]` jako listę SŁOWNIKÓW `msg()`, a `_sync_locked`
    (`plugin/main.py:753`) składał je przez `"; ".join(result["errors"])` —
    łączenie słowników jak napisów rzuca `TypeError`, `@guarded` je łapał i
    użytkownik dostawał `internal_error` zamiast wyniku synchronizacji.

    Podmieniamy tylko `SyncService` pod `_sync_locked` (prawdziwą funkcję, w
    której siedzi naprawiona linia) — logika łączenia komunikatów musi
    przelecieć NAPRAWDĘ, nie przez atrapę, która ją omija."""

    class StubSyncService:
        def __init__(self, *_args, **_kwargs):
            self.last_seconds = {}

        def sync_all(self, _title_keys=None):
            return {
                "restored": [], "conflicts": [], "skipped": [], "blocked": [],
                "errors": [
                    msg("restore_failed", title="Animal Well"),
                    msg("cloud_backup_failed", title="Other Game"),
                ],
            }

    async def scenario():
        main, plugin, _ = _plugin(tmp_path)
        main.SyncService = StubSyncService
        result = await plugin.sync_all()
        entries = plugin._log().tail(10)
        return result, entries

    result, entries = asyncio.run(scenario())

    # brak internal_error — czyli nic nie rzuciło w trakcie łączenia komunikatów
    assert not result.get("error"), \
        "sync_all zgłosił internal_error zamiast wyniku: %r" % (result,)

    problems = [e for e in entries if e.get("code") == "sync_problems"]
    assert problems, "brak wpisu sync_problems w logu: %r" % (entries,)
    detail = problems[0]["params"]["detail"]

    assert "Animal Well" in detail and "restoring failed" in detail, detail
    assert "Other Game" in detail and "cloud backup did not go through" in detail, detail
    # zepsuta wersja (str() na słowniku zamiast ["message"]) dawałaby tu tekst
    # w rodzaju "{'code': 'restore_failed', ...}" — tego pilnujemy wprost
    assert "'code'" not in detail and "{" not in detail, detail



def test_language_setting_overrides_the_language_reported_by_steam(tmp_path):
    """Metadane ze sklepu przychodzą PRZETŁUMACZONE, więc wybór języka w ustawieniach
    musi na nie wpływać — inaczej człowiek z angielskim interfejsem czyta polski opis."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    os.makedirs(main.decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)

    assert plugin._effective_lang('pl') == 'pl', 'domyślnie (auto) wygrywa język Steama'
    assert plugin._effective_lang("") == "en", "bez języka od frontendu zostaje angielski"

    with open(plugin._ui_file(), "w", encoding="utf-8") as handle:
        json.dump({"lang": "en"}, handle)
    assert plugin._effective_lang("pl") == "en", "wybór człowieka bije Steama"

    with open(plugin._ui_file(), "w", encoding="utf-8") as handle:
        json.dump({"lang": "auto"}, handle)
    assert plugin._effective_lang("pl") == "pl"


def test_wyjatek_w_rpc_wraca_jako_kod_a_nie_zdanie(tmp_path):
    """`guarded` opakowuje WSZYSTKIE metody RPC — dopóki produkuje napis, nowy
    kształt pola `error` nie jest prawdą dla żadnej trasy."""
    main = _load_main(tmp_path)

    class Wybuchowy(main.Plugin):
        @main.guarded(dict)
        async def boom(self) -> dict:
            raise RuntimeError("cos padlo")

    result = asyncio.run(Wybuchowy().boom())
    assert result["error"]["code"] == "internal_error", result
    params = result["error"]["params"]
    assert params["method"] == "boom"
    assert params["type"] == "RuntimeError"
    assert "cos padlo" in params["detail"], "szczegół techniczny NIE MOŻE zniknąć"


def test_wyjatek_trafia_do_logu_z_kodem(tmp_path):
    main = _load_main(tmp_path)

    class Wybuchowy(main.Plugin):
        @main.guarded(dict)
        async def boom(self) -> dict:
            raise RuntimeError("cos padlo")

    plugin = Wybuchowy()
    asyncio.run(plugin.boom())
    entry = plugin._log().tail(1)[0]
    assert entry["kind"] == "error" and entry["code"] == "internal_error", entry


def test_wyjatek_dokladany_jest_takze_do_listy_errors(tmp_path):
    """Frontend pokazuje `errors`; cisza tam wygląda jak czysty przebieg."""
    main = _load_main(tmp_path)

    class Wybuchowy(main.Plugin):
        @main.guarded(main.empty_sync)
        async def boom(self) -> dict:
            raise RuntimeError("cos padlo")

    result = asyncio.run(Wybuchowy().boom())
    assert [e["code"] for e in result["errors"]] == ["internal_error"], result


def test_jezyk_ma_domyslnie_auto(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    ui = asyncio.run(plugin.get_ui_settings())
    assert ui["lang"] == "auto", ui


def test_jezyk_da_sie_ustawic_i_zapisuje_sie_na_dysku(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    saved = asyncio.run(plugin.set_ui_setting("lang", "en"))
    assert saved["lang"] == "en" and not saved.get("error"), saved
    # nowa instancja czyta z pliku, nie z pamięci
    assert asyncio.run(main.Plugin().get_ui_settings())["lang"] == "en"


def test_nieznany_jezyk_jest_odrzucony_widocznie(tmp_path):
    """Cicha akceptacja zostawiłaby w pliku wartość, dla której nie ma katalogu,
    a interfejs pokazałby surowe klucze."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    result = asyncio.run(plugin.set_ui_setting("lang", "klingon"))
    assert result.get("error"), result
    assert asyncio.run(main.Plugin().get_ui_settings())["lang"] == "auto"


def test_lista_jezykow_backendu_zgadza_sie_z_katalogami(tmp_path):
    """UI_ALLOWED i CATALOGS to dwa miejsca z tą samą listą. Rozjazd znaczy, że
    `set_ui_setting` odrzuci język, który selektor pokazał jako dostępny."""
    main = _load_main(tmp_path)
    i18n_dir = os.path.join(os.path.dirname(__file__), "..", "src", "i18n")
    katalogi = {name[:-len(".json")] for name in os.listdir(i18n_dir)
                if name.endswith(".json")}
    assert set(main.Plugin.UI_ALLOWED["lang"]) == katalogi | {"auto"}


def test_odrzucone_wywolanie_ma_kod(tmp_path):
    """Drugie wywołanie w trakcie przebiegu musi być WIDOCZNE i przetłumaczalne."""
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    plugin._sync_lock.acquire()
    try:
        result = asyncio.run(plugin.sync_all())
    finally:
        plugin._sync_lock.release()
    assert result["error"]["code"] == "sync_already_running", result
    assert [e["code"] for e in result["errors"]] == ["sync_already_running"], result


def test_podsumowanie_przebiegu_idzie_do_logu_jako_kod(tmp_path):
    main = _load_main(tmp_path)
    plugin = main.Plugin()
    asyncio.run(plugin.sync_all())
    kody = [e.get("code") for e in plugin._log().tail(10)]
    assert "sync_summary" in kody, plugin._log().tail(10)


# ---------- zmiana tytułu już dodanej gry ----------
#
# Tytuł jest w tym projekcie TOŻSAMOŚCIĄ gry, więc jego zmiana nie jest kosmetyką:
# przenosi klucz rejestru, nazwę katalogu kopii na karcie i klucz w pliku wymiany czasu
# gry. Zostawienie któregokolwiek z nich pod starą nazwą znaczy grę, która „zgubiła"
# swoje zapisy albo swoje godziny — a wygląda to na sukces, bo RPC odpowiada bez błędu.

def _plugin_z_karta(tmp_path, title="GTA V Enhanced"):
    main = _load_main(tmp_path)
    karta = tmp_path / "SD256"
    (karta / ".sdsync" / "saves").mkdir(parents=True)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": title, "appid": 4242, "folder": "GTA",
                     "card_label": "SD256"})
    registry.set_fields(_klucz(title), playtime_seconds=600, ludusavi_unknown=True)
    plugin = main.Plugin()
    plugin._cards = lambda: [{"label": "SD256", "mount": str(karta), "games_dir": "Games"}]
    return main, plugin, registry, karta


def _klucz(title):
    from sdsync.registry import title_key
    return title_key(title)


def test_zmiana_tytulu_zabiera_zapisy_i_czas_gry_pod_nowy_klucz(tmp_path):
    main, plugin, registry, karta = _plugin_z_karta(tmp_path)
    stary, nowy = _klucz("GTA V Enhanced"), _klucz("Grand Theft Auto V")
    # kopia Ludusaviego i plik wymiany czasu gry — oba nazwane STARYM tytułem/kluczem
    (karta / ".sdsync" / "saves" / "GTA V Enhanced").mkdir()
    (karta / ".sdsync" / "saves" / "GTA V Enhanced" / "mapping.yaml").write_text("when: []\n")
    from sdsync import playtime
    with open(playtime.file_path(str(karta)), "w") as fh:
        json.dump({stary: {"deck": 600, "machine": 120}}, fh)

    wynik = asyncio.run(plugin.retitle(stary, "Grand Theft Auto V"))

    assert wynik["ok"] is True, wynik
    assert wynik["title_key"] == nowy
    assert registry.get(stary) is None, "stary wpis musi zniknąć, inaczej skan zrobi drugi kafelek"
    rekord = registry.get(nowy)
    assert rekord["title"] == "Grand Theft Auto V"
    assert rekord["appid"] == 4242, "kafelek zostaje ten sam — inaczej gubimy prefiks"
    assert rekord["playtime_seconds"] == 600, "czas gry na tym urządzeniu ma przeżyć"
    assert rekord["ludusavi_unknown"] is False, "nowy tytuł to nowa szansa dla Ludusaviego"
    assert (karta / ".sdsync" / "saves" / "Grand Theft Auto V" / "mapping.yaml").exists(), \
        "kopia na karcie musi jechać ze zmianą tytułu"
    assert not (karta / ".sdsync" / "saves" / "GTA V Enhanced").exists()
    with open(playtime.file_path(str(karta))) as fh:
        wozony = json.load(fh)
    assert wozony == {nowy: {"deck": 600, "machine": 120}}, \
        "godziny z drugiego urządzenia zostałyby sierotą pod starym kluczem"


def test_zmiana_tytulu_uruchomionej_gry_jest_odrzucana(tmp_path):
    """Zasada 4: zapisy uruchomionej gry są nietykalne, a przemianowanie katalogu kopii
    w trakcie sesji to dokładnie ruszanie ich spod ręki."""
    main, plugin, registry, karta = _plugin_z_karta(tmp_path)
    stary = _klucz("GTA V Enhanced")

    async def scenario():
        await plugin.mark_running(4242, True)
        return await plugin.retitle(stary, "Grand Theft Auto V")

    wynik = asyncio.run(scenario())
    assert wynik["ok"] is False
    assert registry.get(stary) is not None, "wpis musi zostać nietknięty"


def test_zmiana_tytulu_na_zajety_nie_zlewa_dwoch_gier(tmp_path):
    """Dwa wpisy pod jednym kluczem to jeden wpis — a razem z nim jeden katalog kopii
    dla dwóch gier, czyli cudze zapisy przywracane do cudzego prefiksu."""
    main, plugin, registry, karta = _plugin_z_karta(tmp_path)
    registry.upsert({"title": "Grand Theft Auto V", "appid": 777, "folder": "GTA2",
                     "card_label": "SD256"})
    wynik = asyncio.run(plugin.retitle(_klucz("GTA V Enhanced"), "Grand Theft Auto V"))
    assert wynik["ok"] is False
    assert registry.get(_klucz("Grand Theft Auto V"))["appid"] == 777, "cudzy wpis nadpisany"
    assert registry.get(_klucz("GTA V Enhanced")) is not None


def test_zmiana_tytulu_na_ten_sam_nic_nie_psuje(tmp_path):
    main, plugin, registry, karta = _plugin_z_karta(tmp_path)
    stary = _klucz("GTA V Enhanced")
    wynik = asyncio.run(plugin.retitle(stary, "GTA V Enhanced"))
    assert wynik["ok"] is True, wynik
    assert registry.get(stary)["appid"] == 4242


def test_zmiana_tytulu_odrzuca_pusty_tytul(tmp_path):
    main, plugin, registry, karta = _plugin_z_karta(tmp_path)
    wynik = asyncio.run(plugin.retitle(_klucz("GTA V Enhanced"), "   "))
    assert wynik["ok"] is False
    assert registry.get(_klucz("GTA V Enhanced")) is not None


def test_wskazany_recznie_appid_bije_baze_ludusaviego(tmp_path):
    """ZMIERZONE: baza Ludusaviego wiąże „Grand Theft Auto V" z appidem 271590, czyli
    wydaniem Legacy — a na karcie użytkownika leży Enhanced (3240220). Bez ręcznego
    wskazania gra ma na ekranie opis i ocenę CUDZEGO wydania i nie da się tego cofnąć."""
    main = _load_main(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": "Grand Theft Auto V", "appid": 4242, "folder": "GTA"})
    pytane = []

    class FakeMeta:
        def __init__(self, *a, **kw):
            pass

        def search(self, text, lang="en"):
            return [{"appid": 3240220, "name": "Grand Theft Auto V Enhanced"}]

        def forget(self, key):
            pytane.append(("forget", key))
            return True

        def fetch(self, key, title, lang="en", appid=None):
            pytane.append(("fetch", key, title, appid))
            return {"steam_appid": appid, "name": "Grand Theft Auto V Enhanced"}

        def get(self, key, lang=""):
            return None

    plugin = main.Plugin()
    plugin._metadata = lambda: FakeMeta()

    async def scenario():
        znalezione = await plugin.store_search("gta v enhanced")
        zapis = await plugin.set_store_appid("grand-theft-auto-v", 3240220)
        # ekran gry pyta zwykłą drogą — i musi dostać wydanie wskazane przez człowieka
        ponownie = await plugin.fetch_metadata("grand-theft-auto-v", "pl")
        return znalezione, zapis, ponownie

    znalezione, zapis, ponownie = asyncio.run(scenario())
    assert znalezione == [{"appid": 3240220, "name": "Grand Theft Auto V Enhanced"}]
    assert zapis["ok"] is True and zapis["steam_appid"] == 3240220, zapis
    assert registry.get("grand-theft-auto-v")["steam_appid"] == 3240220
    assert ("forget", "grand-theft-auto-v") in pytane, "stary opis musi zniknąć z pamięci"
    assert pytane[-1] == ("fetch", "grand-theft-auto-v", "Grand Theft Auto V", 3240220), \
        "fetch_metadata poszło do bazy Ludusaviego zamiast do wskazanego appidu"

    # i da się to cofnąć — zero znaczy „wróć do bazy Ludusaviego"
    asyncio.run(plugin.set_store_appid("grand-theft-auto-v", 0))
    assert registry.get("grand-theft-auto-v")["steam_appid"] is None


def test_zmiana_tytulu_bez_karty_jest_odrzucana(tmp_path):
    """ZMIERZONE na Decku (przez wypadek przy testowaniu na żywym urządzeniu): przy
    wyjętej karcie zmiana tytułu PRZECHODZIŁA — rejestr, kafelek i log meldowały sukces,
    a katalog kopii na karcie zostawał pod STARĄ nazwą. Gra traciła w ten sposób swoje
    zapisy przy pierwszym włożeniu karty, przy odpowiedzi RPC bez błędu (zasada 1).

    Karty nie da się zapytać, gdy jej nie ma, więc jedyną bezpieczną odpowiedzią jest
    odmowa — tak samo jak przy ręcznym wskazaniu pliku .exe."""
    main = _load_main(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": "GTA V Enhanced", "appid": 4242, "card_label": "SD256"})
    plugin = main.Plugin()
    plugin._cards = lambda: []          # czytnik pusty

    wynik = asyncio.run(plugin.retitle(_klucz("GTA V Enhanced"), "Grand Theft Auto V"))

    assert wynik["ok"] is False, wynik
    assert registry.get(_klucz("GTA V Enhanced")) is not None, "tytuł zmieniony mimo braku karty"
    assert registry.get(_klucz("Grand Theft Auto V")) is None


def test_zmiana_tytulu_gry_z_dysku_nie_potrzebuje_karty(tmp_path):
    """Gra z dysku konsoli ma nośnik ZAWSZE (katalog lokalny), więc bramka na kartę
    nie może jej blokować — inaczej takiej gry nie dałoby się poprawić nigdy."""
    main = _load_main(tmp_path)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": "GTA V Enhanced", "appid": 4242, "carrier": "disk"})
    plugin = main.Plugin()
    plugin._cards = lambda: []

    wynik = asyncio.run(plugin.retitle(_klucz("GTA V Enhanced"), "Grand Theft Auto V"))

    assert wynik["ok"] is True, wynik
    assert registry.get(_klucz("grand theft auto v".title())) is not None


# ---------- rozstrzyganie konfliktu: trzy kopie, trzy daty ----------

class KonfliktSaves:
    """Atrapa warstwy zapisów odwzorowująca ZMIERZONE zachowanie trzech źródeł."""

    def __init__(self, tmp_path):
        self.calls = []
        self.lock_path = str(tmp_path / "sdsync-sync.lock")
        self.live = "2026-08-24T10:00:00Z"
        self.card = "2026-08-22T09:00:00Z"
        self.cloud = "2026-08-23T08:00:00Z"
        self.card_ok = True
        self.last_stderr = ""

    def lock(self):
        from sdsync.saves import sync_lock
        return sync_lock(self.lock_path)

    def live_save_when(self, title):
        self.calls.append("live_save_when")
        return self.live

    def card_when_many(self, titles, path):
        self.calls.append(("card_when_many", path))
        return {t: self.card for t in titles}

    def cloud_when(self, title):
        self.calls.append("cloud_when")
        return self.cloud

    def safety_backup(self, title):
        self.calls.append("safety_backup")
        return True

    def card_backup_many(self, titles, path):
        self.calls.append(("card_backup_many", path))
        return {t: self.card_ok for t in titles}

    def card_restore(self, title, path):
        self.calls.append(("card_restore", path))
        return True

    def restore(self, title, path=None):
        self.calls.append("restore")
        return True

    def backup(self, title, cloud=True):
        self.calls.append("backup_cloud" if cloud else "backup_local")
        return {"ok": True}

    def cloud_upload(self, games=None):
        self.calls.append("cloud_upload")
        return True

    def cloud_download(self, games=None):
        self.calls.append("cloud_download")
        return True


def _plugin_konflikt(tmp_path, title="Animal Well"):
    main = _load_main(tmp_path)
    karta = tmp_path / "SD256"
    (karta / ".sdsync" / "saves").mkdir(parents=True)
    registry = Registry(os.path.join(main.decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json"))
    registry.upsert({"title": title, "appid": 4242, "card_label": "SD256"})
    registry.set_fields(_klucz(title), conflict=True)
    saves = KonfliktSaves(tmp_path)
    plugin = main.Plugin()
    plugin._saves = lambda: saves
    plugin._cards = lambda: [{"label": "SD256", "mount": str(karta), "games_dir": "Games"}]
    return main, plugin, registry, saves, karta


def test_konflikt_podaje_date_wszystkich_trzech_kopii(tmp_path):
    """ZGŁOSZONE: „nie wiem, który jest nowszy". Trzy kopie mają trzy różne źródła daty
    i żadna nie wynika z pozostałych, więc każdą trzeba podać osobno."""
    _, plugin, _, saves, _ = _plugin_konflikt(tmp_path)
    out = asyncio.run(plugin.conflict_options(_klucz("Animal Well")))
    assert out["local"]["when"] == "2026-08-24T10:00:00Z", out
    assert out["card"]["when"] == "2026-08-22T09:00:00Z", out
    assert out["cloud"]["when"] == "2026-08-23T08:00:00Z", out
    assert out["card"]["label"] == "SD256"
    assert out["newest"] == "local", "najnowszą trzeba wskazać, nie kazać porównywać tekstów"


def test_konflikt_bez_karty_w_czytniku_nie_udaje_ze_karta_jest_pusta(tmp_path):
    """„Karty nie ma" i „karta nie ma kopii" to dwie różne rzeczy: przy pierwszej
    wybranie chmury byłoby decyzją podjętą bez jednej z trzech informacji."""
    _, plugin, _, _, _ = _plugin_konflikt(tmp_path)
    plugin._cards = lambda: []
    out = asyncio.run(plugin.conflict_options(_klucz("Animal Well")))
    assert out["card"]["when"] is None
    assert out["card"]["present"] is False
    assert out["newest"] != "card"


def test_konflikt_nieznana_data_nie_wygrywa_najnowszego(tmp_path):
    _, plugin, _, saves, _ = _plugin_konflikt(tmp_path)
    saves.cloud = None      # nie wiem
    saves.live = ""         # gra nie ma zapisów
    out = asyncio.run(plugin.conflict_options(_klucz("Animal Well")))
    assert out["newest"] == "card", out


def test_wyslanie_mojego_zapisu_idzie_NAJPIERW_na_karte(tmp_path):
    """NIEZMIENNIK projektu: chmura nigdy nie dostaje stanu, którego nie ma karta.
    Wcześniej „wyślij mój" robiło kopię lokalną i od razu wysyłkę — z pominięciem
    karty. Drugie urządzenie widziałoby wtedy „karta bez zmian" i grało od starszego
    zapisu, nie mając jak dowiedzieć się o nowszym."""
    _, plugin, registry, saves, karta = _plugin_konflikt(tmp_path)
    out = asyncio.run(plugin.resolve_conflict(_klucz("Animal Well"), "local"))
    assert out["ok"] is True, out
    nazwy = [c[0] if isinstance(c, tuple) else c for c in saves.calls]
    assert "card_backup_many" in nazwy, saves.calls
    assert nazwy.index("card_backup_many") < nazwy.index("cloud_upload"), saves.calls
    assert registry.get(_klucz("Animal Well"))["conflict"] is False


def test_wyslanie_mojego_zapisu_bez_udanej_kopii_na_karte_NIE_idzie_do_chmury(tmp_path):
    _, plugin, registry, saves, _ = _plugin_konflikt(tmp_path)
    saves.card_ok = False
    out = asyncio.run(plugin.resolve_conflict(_klucz("Animal Well"), "local"))
    assert out["ok"] is False, out
    assert "cloud_upload" not in saves.calls, saves.calls
    assert registry.get(_klucz("Animal Well"))["conflict"] is True, "konflikt zostaje"


def test_wziecie_zapisu_z_karty_jest_trzecia_mozliwoscia(tmp_path):
    """ZGŁOSZONE: „save może jeszcze nie być wgrany do gry, ale leżeć na karcie SD".
    Do tej pory dało się wybrać tylko między swoim zapisem a chmurą — kopia z karty,
    czyli ta, którą przywiozło drugie urządzenie, nie była do wybrania wcale."""
    _, plugin, registry, saves, karta = _plugin_konflikt(tmp_path)
    out = asyncio.run(plugin.resolve_conflict(_klucz("Animal Well"), "card"))
    assert out["ok"] is True, out
    nazwy = [c[0] if isinstance(c, tuple) else c for c in saves.calls]
    assert nazwy.index("safety_backup") < nazwy.index("card_restore"), \
        "przywracanie bez kopii bezpieczeństwa zamazuje zapis bez odwrotu"
    rekord = registry.get(_klucz("Animal Well"))
    assert rekord["conflict"] is False
    assert rekord["card_seen"] == {"SD256": "2026-08-22T09:00:00Z"}, \
        "bez zapamiętania tożsamości kopii następny przebieg zgłosi ten sam konflikt"


def test_wziecie_z_karty_bez_karty_w_czytniku_jest_odrzucane(tmp_path):
    _, plugin, registry, saves, _ = _plugin_konflikt(tmp_path)
    plugin._cards = lambda: []
    out = asyncio.run(plugin.resolve_conflict(_klucz("Animal Well"), "card"))
    assert out["ok"] is False
    assert "card_restore" not in [c[0] if isinstance(c, tuple) else c for c in saves.calls]
    assert registry.get(_klucz("Animal Well"))["conflict"] is True
