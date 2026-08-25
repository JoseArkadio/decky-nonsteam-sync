import functools
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.registry import Registry
from sdsync.saves import (CLOUD_TIMEOUT, LOCAL_TIMEOUT,
                          Saves, SyncLocked, _config_path, _default_runner,
                          CARD_FULL_LIMIT, backup_dir_name, cloud_remote_set,
                          cloud_target, sync_lock)
from sdsync.titles import _default_runner as _titles_runner
from sdsync.sync import SyncService

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ludusavi-real-output.txt")


def _real(section: str) -> str:
    """Dosłowne wyjście zmierzone na urządzeniu — formatu nie przepisujemy z pamięci."""
    chunks, name = {}, None
    with open(_FIXTURE, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("===") and line.strip().endswith("==="):
                name = line.strip().strip("=")
                chunks[name] = ""
            elif name is not None:
                chunks[name] += line
    return chunks[section]


class FakeRunner:
    """Odpowiedzi to (code, stdout, stderr) — dokładnie tak jak _default_runner."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.timeouts = []

    def __call__(self, argv, timeout=None):
        self.calls.append(argv)
        self.timeouts.append(timeout)
        for needle, reply in self.replies.items():
            if needle in " ".join(argv):
                return reply
        return 0, "{}", ""


@functools.lru_cache(maxsize=1)
def _default_config() -> str:
    """Konfiguracja z USTAWIONĄ chmurą, w pliku tymczasowym: wynik testu nie może
    zależeć od tego, czy maszyna testująca ma skonfigurowane Ludusavi.

    Nieistniejący plik nie nadaje się już na wartość domyślną — „nie da się
    przeczytać konfiguracji" jest od teraz osobną awarią cloud_state, a nie
    przepustką dalej."""
    path = os.path.join(tempfile.mkdtemp(prefix="sdsync-config-"), "config.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_CONFIG_SET)
    return path


def _saves(replies, config_path=None):
    runner = FakeRunner(replies)
    return Saves(["ludusavi"], "/tmp/safety", runner=runner,
                 config_path=config_path or _default_config()), runner


def _shell_saves(script: str) -> Saves:
    """Saves na prawdziwym runnerze; `sh -c` ignoruje dopisywane argumenty."""
    return Saves(["sh", "-c", script, "ludusavi"], "/tmp/safety",
                 config_path=_default_config())


def test_cloud_download_always_uses_force():
    saves, runner = _saves({"cloud download": (0, "", "")})
    assert saves.cloud_download() is True
    assert "--force" in runner.calls[0]


def test_cloud_upload_always_uses_force():
    saves, runner = _saves({"cloud upload": (0, "", "")})
    assert saves.cloud_upload() is True
    assert runner.calls[0] == ["ludusavi", "--try-manifest-update", "cloud", "upload", "--force"]


# --- G4: operacje chmurowe z filtrem gry ---

def test_cloud_upload_with_game_filter_passes_the_title():
    """Regresja G4: wysyłka bez filtra przepisuje CAŁĄ chmurę stanem lokalnym. Przy
    świeżo przełożonej karcie (lokalny katalog kopii uboższy od chmury) to utrata
    kopii pozostałych gier."""
    saves, runner = _saves({"cloud upload": (0, "", "")})
    assert saves.cloud_upload(["Animal Well"]) is True
    assert runner.calls[0] == ["ludusavi", "--try-manifest-update", "cloud", "upload", "--force", "Animal Well"]


def test_cloud_download_with_game_filter_passes_the_title():
    saves, runner = _saves({"cloud download": (0, "", "")})
    assert saves.cloud_download(["Animal Well"]) is True
    assert runner.calls[0] == ["ludusavi", "--try-manifest-update", "cloud", "download", "--force", "Animal Well"]


def test_cloud_filter_of_blank_titles_never_widens_to_whole_library():
    """Lista samych pustych tytułów to nie „cała biblioteka" — to błąd wywołującego."""
    for games in ([""], [" ", "\t"], [None]):
        saves, runner = _saves({"cloud": (0, "", "")})
        assert saves.cloud_upload(games) is False
        assert saves.cloud_download(games) is False
        assert runner.calls == [], runner.calls


def test_cloud_operations_without_filter_still_cover_the_library():
    saves, runner = _saves({"cloud": (0, "", "")})
    assert saves.cloud_download() is True
    assert saves.cloud_upload([]) is True
    assert runner.calls == [["ludusavi", "--try-manifest-update", "cloud", "download", "--force"],
                            ["ludusavi", "--try-manifest-update", "cloud", "upload", "--force"]]


# --- F1: JSON bywa na stdout, a bywa na stderr ---

# --- nieskonfigurowana chmura nie może wyglądać jak "wszystko zsynchronizowane" ---

_CONFIG_SET = """\
backup:
  path: /home/deck/ludusavi-backup
cloud:
  remote:
    GoogleDrive:
      id: ludusavi-1787098149
  path: ludusavi-backup
  synchronize: true
apps:
  rclone:
    path: /app/bin/rclone
"""

_CONFIG_UNSET = """\
backup:
  path: /home/deck/ludusavi-backup
cloud:
  remote: ~
  path: ludusavi-backup
  synchronize: true
apps:
  rclone:
    path: ~/sdsync/bin/rclone
"""


def _config(tmp_path, text, name="config.yaml") -> str:
    path = str(tmp_path / name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def test_cloud_remote_set_reads_both_real_config_shapes(tmp_path):
    """Oba układy zmierzone na urządzeniu: flatpakowy (GoogleDrive) i samodzielny (~)."""
    assert cloud_remote_set(_config(tmp_path, _CONFIG_SET, "set.yaml")) is True
    assert cloud_remote_set(_config(tmp_path, _CONFIG_UNSET, "unset.yaml")) is False
    assert cloud_remote_set(str(tmp_path / "nie-ma.yaml")) is None, \
        "brak pliku to 'nie wiem', nie 'nieskonfigurowana'"


def test_cloud_config_path_follows_the_command_actually_used():
    """Zmierzone: wtyczka woła flatpaka, a chmura jest ustawiona w JEGO konfiguracji;
    samodzielne ~/.config/ludusavi/config.yaml ma remote: ~ i jest nieużywane."""
    flat = _config_path(["flatpak", "run", "com.github.mtkennerly.ludusavi"])
    assert flat.endswith(
        "/.var/app/com.github.mtkennerly.ludusavi/config/ludusavi/config.yaml"), flat
    assert _config_path(["/home/deck/sdsync/bin/ludusavi"]).endswith(
        "/.config/ludusavi/config.yaml")


def test_unconfigured_cloud_is_an_error_not_a_clean_sync(tmp_path):
    """Regresja: bez cloud.remote podgląd kończy się kodem 0 i {"games": {}},
    więc synchronizacja meldowała czysty sukces, choć nic nigdy nie zostanie
    przeniesione."""
    saves, runner = _saves({
        "cloud download --preview": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_STDERR")),
        "cloud upload --preview": (0, "", _real("CLOUD_UPLOAD_PREVIEW_STDERR")),
    }, config_path=_config(tmp_path, _CONFIG_UNSET))
    assert saves.cloud_state() == (False, set(), set())
    assert saves.cloud_error["code"] == "cloud_not_configured"
    assert runner.calls == [], "nie ma po co pytać chmury, której nie ma"


def test_configured_cloud_still_reads_the_state(tmp_path):
    saves, _ = _saves({
        "cloud download --preview": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_STDERR")),
        "cloud upload --preview": (0, "", _real("CLOUD_UPLOAD_PREVIEW_STDERR")),
    }, config_path=_config(tmp_path, _CONFIG_SET))
    assert saves.cloud_state() == (True, set(), set())
    assert saves.cloud_error == ""


def test_cloud_state_reads_real_json_from_stderr():
    """Regresja F1: podglądy chmury piszą JSON na stderr, stdout jest pusty.
    Parsowanie 'wyłącznie stdout' dawało tu 'nie wiem' i blokowało synchronizację."""
    saves, _ = _saves({
        "cloud download --preview": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_STDERR")),
        "cloud upload --preview": (0, "", _real("CLOUD_UPLOAD_PREVIEW_STDERR")),
    })
    assert saves.cloud_state() == (True, set(), set()), \
        "poprawny JSON bez pola 'cloud' to 'brak zmian', nie awaria"


def test_local_changed_reads_real_json_from_stdout():
    saves, _ = _saves({"backup --preview": (0, _real("BACKUP_PREVIEW_STDOUT"), "")})
    assert saves.local_changed("Animal Well") is False


def test_backups_reads_real_json_from_stdout():
    saves, _ = _saves({"backups": (0, _real("BACKUPS_STDOUT"), "")})
    assert saves.backups("Animal Well") == [
        {"name": ".", "when": "2026-08-19T16:34:52.601297553Z"}]


def test_cloud_state_without_json_in_either_stream_is_unknown():
    saves, _ = _saves({
        "cloud download --preview": (0, "", "Nie ma zmian do synchronizacji"),
        "cloud upload --preview": (0, "", _real("CLOUD_UPLOAD_PREVIEW_STDERR")),
    })
    assert saves.cloud_state() == (False, set(), set()), "brak JSON-a to 'nie wiem'"


def test_json_on_stderr_survives_the_real_runner():
    """Strumienie zostają rozdzielone: JSON tylko na stderr wciąż jest odczytany."""
    payload = _real("CLOUD_DOWNLOAD_PREVIEW_STDERR").replace("\n", "")
    saves = _shell_saves("printf '%%s' '%s' >&2" % payload)
    assert saves.cloud_state() == (True, set(), set())
    assert payload in saves.last_stderr, "stderr nadal trafia do logu"


# --- F2/F3: dwa kierunki, mapa ścieżek w polu "cloud" ---

# Kształt pola "cloud" wg F2 planu (urządzenie nie miało zmian, więc próbka
# w fixture jest pusta): mapa ścieżek względem folderu chmury.
# Jedna odpowiedź na jedno wywołanie: "New"/"Different" = przyjdzie z chmury,
# "Removed" = mamy to tylko my (patrz fixtures *_BOTH_WAYS_*).
_CLOUD_DOWN = json.dumps({"cloud": {
    "Hades II/backup-20260819T120000Z/save.dat": {"change": "New"},
    "Hades II/backup-20260819T120000Z/mapping.yaml": {"change": "New"},
    "Animal Well/backup-20260819T163452Z/AnimalWell.sav": {"change": "Removed"},
}})


def test_cloud_state_splits_both_directions():
    saves, runner = _saves({"cloud download --preview": (0, "", _CLOUD_DOWN)})
    assert saves.cloud_state() == (True, {"Hades II"}, {"Animal Well"})
    assert all("--api" in argv for argv in runner.calls)


def test_cloud_state_does_not_match_by_substring():
    saves, _ = _saves({"cloud download --preview": (0, "", _CLOUD_DOWN),
                       "cloud upload --preview": (0, "", "{}")})
    ok, cloud_ahead, _ = saves.cloud_state()
    assert ok is True
    assert cloud_ahead == {"Hades II"}, "'Hades' nie może łapać 'Hades II'"


def test_cloud_state_never_parses_human_readable_markers():
    """[Δ]/[+] to wyjście dla człowieka — nie wolno z niego czytać stanu."""
    text = "[Δ] Hades II/backup-1/save.dat\n[+] Animal Well/backup-2/x\n"
    saves, _ = _saves({"cloud download --preview": (0, text, text),
                       "cloud upload --preview": (0, "", "{}")})
    assert saves.cloud_state() == (False, set(), set())


def test_cloud_state_failure_is_not_an_empty_result():
    saves, _ = _saves({"cloud download --preview": (1, "", "rclone: brak sieci")})
    assert saves.cloud_state() == (False, set(), set())


def test_cloud_state_with_valid_json_but_nonzero_code_is_unknown():
    """Zerwana sieć w połowie podglądu: JSON jest poprawny, ale niepełny, a kod
    wyjścia to mówi. Czytanie samego JSON-a dawałoby cichą, obciętą listę."""
    partial = json.dumps({"cloud": {"Hades II/backup-1/save.dat": {"change": "New"}}})
    saves, _ = _saves({"cloud download --preview": (1, "", partial)})
    assert saves.cloud_state() == (False, set(), set()), \
        "niepełny podgląd nie może udawać pełnej listy"


def test_cloud_preview_reads_stderr_even_when_stdout_has_json():
    """Opakowanie flatpaka potrafi dorzucić własny JSON na stdout. Wybór
    strumienia jest świadomy, inaczej wychodzi ciche 'brak zmian'."""
    saves, _ = _saves({"cloud download --preview": (0, "{}", _CLOUD_DOWN)})
    assert saves.cloud_state() == (True, {"Hades II"}, {"Animal Well"})


def test_cloud_state_reads_real_cloud_field_from_stderr():
    """Kształt pola "cloud" zmierzony na urządzeniu, nie wymyślony."""
    saves, _ = _saves({
        "cloud download --preview": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_STDERR")),
    })
    assert saves.cloud_state() == (True, set(), set())


def test_cloud_preview_and_cloud_has_newer_are_gone():
    saves, _ = _saves({})
    assert not hasattr(saves, "cloud_preview")
    assert not hasattr(saves, "cloud_has_newer")


# --- F5: backup ---

def test_backup_reports_conflict_from_api_output():
    payload = json.dumps({
        "errors": {"cloudConflict": True},
        "games": {"Hades": {"decision": "Processed", "change": "Different", "files": {
            "/a/save1": {"change": "Different", "bytes": 10}}}},
    })
    saves, _ = _saves({"backup": (0, payload, "")})
    result = saves.backup("Hades")
    assert result["conflict"] is True
    assert result["ok"] is True


def test_backup_counts_changed_bytes():
    payload = json.dumps({"games": {"Hades": {
        "decision": "Processed", "change": "Different", "files": {
            "/a/save1": {"change": "Different", "bytes": 1000, "ignored": False},
            "/a/save2": {"change": "Same", "bytes": 4000, "ignored": False},
            "/a/save3": {"change": "New", "bytes": 500, "ignored": True},
        }}}})
    saves, _ = _saves({"backup": (0, payload, "")})
    assert saves.backup("Hades")["changed_bytes"] == 1000


def test_backup_with_empty_stdout_is_not_a_success():
    """Regresja F5: kod 0 i puste wyjście dawało 'sukces, 0 bajtów'."""
    saves, _ = _saves({"backup": (0, "", "")})
    assert saves.backup("Hades")["ok"] is False


def test_backup_without_games_key_is_not_a_success():
    saves, _ = _saves({"backup": (0, json.dumps({"overall": {"totalGames": 0}}), "")})
    assert saves.backup("Hades")["ok"] is False


def test_backup_of_game_without_saves_is_not_a_success():
    """Regresja G3: kod 0 i {"games": {}} dawało ok=True z zerem bajtów. Ścieżka szkody:
    fałszywy konflikt → „Wyślij moje" → flaga konfliktu wyczyszczona i świeży znacznik
    kopii, choć w chmurze nie ma nic."""
    for out in (json.dumps({"games": {}}),
                json.dumps({"games": {"Hades": {"decision": "Processed", "files": {}}}}),
                json.dumps({"games": {"Hades": {"decision": "Ignored", "files": {
                    "/a/save1": {"change": "New", "bytes": 10}}}}}),
                json.dumps({"games": {"Inna gra": {"decision": "Processed", "files": {
                    "/a/save1": {"change": "New", "bytes": 10}}}}})):
        saves, _ = _saves({"backup": (0, out, "")})
        assert saves.backup("Hades")["ok"] is False, out


def test_backup_of_unchanged_game_is_still_a_success():
    """Kopia bez zmian to nadal kopia: zmierzone wyjście ma decision=Processed i pliki."""
    saves, _ = _saves({"backup": (0, _real("BACKUP_PREVIEW_STDOUT"), "")})
    result = saves.backup("Animal Well")
    assert result["ok"] is True and result["changed_bytes"] == 0


def test_backup_with_cloud_sync_failed_is_not_a_success():
    """Payload odwzorowuje ZMIERZONE zachowanie: kopia LOKALNA powstała (Processed,
    niepuste files), tylko wysyłka do chmury padła. Wcześniejsza wersja podawała grę
    bez decision i bez plików — wtedy ok=False wypadało z innej bramki, a badana
    kontrola mogła zniknąć i test nadal przechodził."""
    payload = json.dumps({"errors": {"cloudSyncFailed": True},
                          "games": {"Hades": {"decision": "Processed", "files": {
                              "/a/save1": {"change": "New", "bytes": 10}}}}})
    saves, _ = _saves({"backup": (0, payload, "")})
    assert saves.backup("Hades")["ok"] is False, "kopia nie dotarła do chmury"


def test_backup_with_some_games_failed_is_not_a_success():
    payload = json.dumps({"errors": {"someGamesFailed": True},
                          "games": {"Hades": {"decision": "Processed", "files": {
                              "/a/save1": {"change": "New", "bytes": 10}}}}})
    saves, _ = _saves({"backup": (0, payload, "")})
    assert saves.backup("Hades")["ok"] is False


def test_backup_with_unparsable_output_is_not_a_success():
    saves, _ = _saves({"backup": (0, "Ludusavi 0.29 pomoc…", "")})
    result = saves.backup("Hades")
    assert result["ok"] is False, "nie wiemy, czy kopia powstała — to nie jest sukces"


def test_backup_with_nonzero_code_is_not_a_success():
    saves, _ = _saves({"backup": (1, "", "brak miejsca")})
    assert saves.backup("Hades")["ok"] is False


# --- F5: kopia bezpieczeństwa ---

_PROCESSED = json.dumps({"games": {"Hades": {
    "decision": "Processed",
    "files": {"/a/save1": {"change": "New", "bytes": 10}},
}}})


def test_safety_backup_uses_separate_path_and_never_touches_cloud():
    saves, runner = _saves({"backup": (0, _PROCESSED, "")})
    assert saves.safety_backup("Hades") is True
    argv = " ".join(runner.calls[0])
    assert "--path /tmp/safety" in argv
    assert "--no-cloud-sync" in argv
    assert "--force" in argv
    assert "--api" in argv
    assert runner.calls[0][-1] == "Hades"


def test_safety_backup_failure_is_reported():
    saves, _ = _saves({"backup": (1, "", "brak miejsca na karcie")})
    assert saves.safety_backup("Hades") is False


def test_safety_backup_failure_is_never_confused_with_nothing_to_protect():
    """Awaria (kod != 0) i "nie ma czego chronić" muszą być rozróżnialne, bo tylko
    pierwsza blokuje przywracanie."""
    saves, _ = _saves({"backup": (1, _real("BACKUP_PREVIEW_NO_SAVES_STDOUT"),
                                  "rclone: brak sieci")})
    assert saves.safety_backup("007 First Light") is False


def test_safety_backup_with_empty_answer_is_a_failure():
    """Regresja F5: odpowiedź, która nic nie mówi o grach, nie dowodzi niczego —
    ani kopii, ani braku zapisów."""
    for out in ("", "{}", json.dumps({"overall": {}})):
        saves, _ = _saves({"backup": (0, out, "")})
        assert saves.safety_backup("Hades") is False, out


def test_safety_backup_of_game_without_saves_is_nothing_not_a_failure():
    """Zmierzone dosłownie na urządzeniu (`backup --force --api --no-cloud-sync
    --path … "007 First Light"`): kod 0 i {"games": {}}. To znaczy "nie ma czego
    chronić", a nie "kopia zawiodła" — inaczej gra uruchamiana pierwszy raz na
    drugim urządzeniu nigdy nie dostanie zapisu z chmury."""
    saves, _ = _saves({
        "backup": (0, _real("BACKUP_PREVIEW_NO_SAVES_STDOUT"), "")})
    assert saves.safety_backup("007 First Light") is None


def test_safety_backup_without_files_is_a_failure():
    payload = json.dumps({"games": {"Hades": {"decision": "Processed", "files": {}}}})
    saves, _ = _saves({"backup": (0, payload, "")})
    assert saves.safety_backup("Hades") is False


def test_safety_backup_of_ignored_game_is_a_failure():
    payload = json.dumps({"games": {"Hades": {"decision": "Ignored", "files": {
        "/a/save1": {"change": "New", "bytes": 10}}}}})
    saves, _ = _saves({"backup": (0, payload, "")})
    assert saves.safety_backup("Hades") is False


_RESTORED = json.dumps({"games": {"Animal Well": {
    "decision": "Processed",
    "files": {"/a/AnimalWell.sav": {"change": "Different", "bytes": 479360}},
}}})


def test_restore_uses_force_and_game_name():
    saves, runner = _saves({"restore": (0, _RESTORED, "")})
    assert saves.restore("Animal Well") is True
    argv = runner.calls[0]
    assert "--force" in argv and "--api" in argv and argv[-1] == "Animal Well"


def test_restore_of_game_without_saves_is_not_a_success():
    """Regresja: Ludusavi kończy kodem 0 i pisze "No saves found for Hades",
    a synchronizacja meldowała {'restored': ['Hades']}."""
    for out in ("No saves found for Animal Well",
                "",
                json.dumps({"games": {}}),
                json.dumps({"games": {"Animal Well": {"decision": "Processed",
                                                      "files": {}}}}),
                json.dumps({"games": {"Animal Well": {"decision": "Ignored",
                                                      "files": {"/a/x": {}}}}}),
                json.dumps({"games": {"Inna gra": {"decision": "Processed",
                                                   "files": {"/a/x": {"bytes": 10}}}}})):
        saves, _ = _saves({"restore": (0, out, "")})
        assert saves.restore("Animal Well") is False, out


def test_restore_with_nonzero_code_is_not_a_success():
    saves, _ = _saves({"restore": (1, _RESTORED, "")})
    assert saves.restore("Animal Well") is False


# --- F5: strażnik pustego tytułu i limity czasu ---

def test_blank_title_never_reaches_ludusavi():
    """Bez filtra gry Ludusavi pracuje na całej bibliotece — takiego wywołania nie robimy."""
    for title in ("", " ", "\t\n"):
        saves, runner = _saves({})
        assert saves.backup(title)["ok"] is False
        assert saves.safety_backup(title) is False
        assert saves.restore(title) is False
        assert saves.local_changed(title) is None
        assert saves.backups(title) == []
        assert runner.calls == [], runner.calls


def test_cloud_operations_get_a_longer_timeout_than_local_ones():
    assert CLOUD_TIMEOUT > LOCAL_TIMEOUT
    saves, runner = _saves({"cloud": (0, "", "{}")})
    saves.cloud_state()
    saves.cloud_download()
    saves.cloud_upload()
    assert set(runner.timeouts) == {CLOUD_TIMEOUT}

    saves, runner = _saves({"backup --preview": (0, _real("BACKUP_PREVIEW_STDOUT"), "")})
    saves.local_changed("Animal Well")
    saves.safety_backup("Animal Well")
    assert set(runner.timeouts) == {LOCAL_TIMEOUT}


def test_ludusavi_calls_never_wait_for_standard_input():
    """Regresja: bez odcięcia wejścia polecenie pytające o potwierdzenie wisiało
    11 minut (limit: godzina), trzymając zamek "jedna synchronizacja naraz".

    Wejściem musi być POTOK, który się nie kończy. Wcześniejsza wersja testu czytała
    to, co pytest podstawia na deskryptor 0 — a to jest urządzenie puste, więc test
    przechodził identycznie po usunięciu stdin=DEVNULL i niczego nie chronił."""
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    try:
        os.dup2(read_end, 0)
        code, out, _ = _default_runner(["sh", "-c", "wc -c"], timeout=3)
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)
    assert (code, out.strip()) == (0, "0"), "polecenie zobaczyło cudze wejście i czekało"

    # Ta sama bramka dla wyszukiwania tytułu — trasa wołana przy KAŻDYM skanie karty.
    # Tu wejście jest potokiem z danymi i zamkniętym końcem do pisania, bo titles ma
    # sztywny limit 90 s: potok bez końca kazałby testowi wisieć półtorej minuty.
    read_end, write_end = os.pipe()
    os.write(write_end, b"xx")
    os.close(write_end)
    saved = os.dup(0)
    try:
        os.dup2(read_end, 0)
        title_code, title_out = _titles_runner(["sh", "-c", "wc -c"])
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end):
            os.close(fd)
    assert (title_code, title_out.strip()) == (0, "0"), "titles zobaczyło cudze wejście"


def test_cloud_preview_also_passes_force():
    """Bez tty pytanie o potwierdzenie nie ma komu odpowiedzieć."""
    saves, runner = _saves({"cloud": (0, "", "{}")})
    saves.cloud_state()
    for argv in runner.calls:
        assert "--preview" in argv and "--force" in argv, argv


def test_failed_command_is_reported_not_raised():
    saves, _ = _saves({"cloud download": (1, "", "błąd")})
    assert saves.cloud_download() is False


def test_local_changed_detects_difference_against_last_backup():
    changed = json.dumps({"games": {"Hades": {"change": "Different", "files": {
        "/a/save1": {"change": "Different", "bytes": 10, "ignored": False}}}}})
    saves, runner = _saves({"backup --preview": (0, changed, "")})
    assert saves.local_changed("Hades") is True
    argv = " ".join(runner.calls[0])
    assert "--preview" in argv and "--no-cloud-sync" in argv, "podgląd nie może ruszać chmury"


def test_local_changed_false_when_nothing_changed():
    same = json.dumps({"games": {"Hades": {"change": "Same", "files": {
        "/a/save1": {"change": "Same", "bytes": 10, "ignored": False}}}}})
    saves, _ = _saves({"backup --preview": (0, same, "")})
    assert saves.local_changed("Hades") is False


def test_local_changed_of_game_without_saves_is_false_not_unknown():
    """Regresja G2 — zmierzone na urządzeniu: `backup --preview --api --no-cloud-sync
    "007 First Light"` → kod 0 i {"games": {}}. Ludusavi jednoznacznie mówi „ta gra nie
    ma tu zapisów"; „nie wiem" robiło z tego konflikt i blokowało pierwsze uruchomienie
    gry na drugim urządzeniu."""
    saves, _ = _saves({
        "backup --preview": (0, _real("BACKUP_PREVIEW_NO_SAVES_STDOUT"), "")})
    assert saves.local_changed("007 First Light") is False


def test_local_changed_unknown_when_answer_has_no_games_key():
    """Odpowiedź bez klucza `games` niczego nie stwierdza — to nadal „nie wiem"."""
    saves, _ = _saves({"backup --preview": (0, json.dumps({"overall": {}}), "")})
    assert saves.local_changed("Hades") is None


def test_local_changed_unknown_when_command_fails():
    saves, _ = _saves({"backup --preview": (1, "", "ludusavi: nie ma manifestu")})
    assert saves.local_changed("Hades") is None, "awaria nie może udawać 'brak zmian'"


def test_local_changed_unknown_when_output_unparsable():
    saves, _ = _saves({"backup --preview": (0, "Ostrzeżenie: brak manifestu", "")})
    assert saves.local_changed("Hades") is None


def test_stderr_warning_does_not_break_api_parsing():
    """Prawdziwy runner: ostrzeżenie na stderr nie może wpaść do JSON-a."""
    payload = json.dumps({"games": {"Hades": {
        "decision": "Processed", "change": "Different", "files": {
            "/a/save1": {"change": "Different", "bytes": 7, "ignored": False}}}}})
    saves = _shell_saves("printf '%%s' '%s'; echo 'warning: manifest stary' >&2" % payload)
    assert saves.local_changed("Hades") is True
    assert saves.backup("Hades") == {"ok": True, "changed_bytes": 7, "conflict": False}
    assert "warning" in saves.last_stderr


def test_missing_binary_is_reported_not_raised():
    saves = Saves(["/nieistniejacy/ludusavi"], "/tmp/safety")
    assert saves.cloud_download() is False
    assert saves.cloud_state() == (False, set(), set())
    assert saves.local_changed("Hades") is None
    assert saves.backup("Hades")["ok"] is False
    assert saves.safety_backup("Hades") is False


def test_timeout_is_reported_not_raised():
    code, out, err = _default_runner(["sh", "-c", "sleep 5"], timeout=0.2)
    assert code != 0
    assert out == ""
    assert "timeout" in err


class FakeSaves:
    """Udaje Saves po zmianach z sekcji F planu. cloud_download() czyści różnice
    „chmura ma, lokalny nie" — tak jak prawdziwe pobranie — więc kod, który pobiera
    przed wykryciem stanu, nic nie widzi. `events` trzyma kolejność operacji."""

    def __init__(self, cloud_ahead=(), local_ahead=(), changed=(), unknown=(),
                 state_ok=True, download_ok=True, safety_ok=True, restore_ok=True,
                 safety_map=None):
        self.cloud_ahead = set(cloud_ahead)
        self.local_ahead = set(local_ahead)
        self.changed = set(changed)
        self.unknown = set(unknown)
        self.state_ok = state_ok
        self.download_ok = download_ok
        self.safety_ok = safety_ok
        # Prawdziwe safety_backup_many zwraca RÓŻNE wyniki w jednym wywołaniu.
        # Atrapa, która tego nie potrafi, ukrywa błąd „udana kopia jednej gry
        # odblokowuje przywracanie wszystkich".
        self.safety_map = dict(safety_map or {})
        self.restore_ok = restore_ok
        self.restored = []
        self.safety = []
        self.events = []
        self.states = 0
        self.downloads = 0
        self.download_args = []
        self.local_queries = []

    def cloud_state(self):
        self.states += 1
        self.events.append(("state",))
        return self.state_ok, set(self.cloud_ahead), set(self.local_ahead)

    def cloud_download(self, games=None):
        self.downloads += 1
        self.download_args.append(list(games) if games else None)
        self.events.append(("download",))
        if self.download_ok:
            self.cloud_ahead = set()
        return self.download_ok

    def local_changed_many(self, titles):
        return {t: self.local_changed(t) for t in titles}

    def safety_backup_many(self, titles):
        return {t: self.safety_backup(t) for t in titles}

    def local_changed(self, title):
        self.local_queries.append(title)
        if title in self.unknown:
            return None
        return title in self.changed

    def backup(self, title, cloud=True):
        self.events.append(("backup", title, cloud))
        return {"ok": True, "changed_bytes": 1, "conflict": False}

    def safety_backup(self, title):
        """Trójstan jak w Saves: True = skopiowano, None = nie ma czego chronić,
        False = awaria. `safety_map` bije `safety_ok` dla wskazanych tytułów."""
        outcome = self.safety_map.get(title, self.safety_ok)
        if outcome is False:
            return False
        self.safety.append(title)
        self.events.append(("safety", title))
        return outcome

    def restore(self, title):
        if not self.restore_ok:
            return False
        self.restored.append(title)
        self.events.append(("restore", title))
        return True


def _reg(tmp_path, *titles, **extra):
    reg = Registry(str(tmp_path / "games.json"))
    for title in titles:
        reg.upsert(dict({"title": title}, **extra))
    return reg


def test_sync_all_restores_only_untouched_games(tmp_path):
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well"}, changed={"Animal Well"})
    result = SyncService(reg, saves, is_running=lambda title: False).sync_all()

    assert result["restored"] == ["Hades"]
    assert result["conflicts"] == ["Animal Well"]
    assert saves.restored == ["Hades"]
    assert sorted(saves.safety) == ["Animal Well", "Hades"]
    assert saves.downloads == 1, "jedno przejście chmury na całą bibliotekę"
    assert saves.states == 1


def test_download_before_detection_would_hide_everything_to_do(tmp_path):
    """Regresja P1: wykrycie stanu chmury musi poprzedzać pobranie."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == ["Hades"], "pobranie zjadło wykrycie różnic"
    assert result["skipped"] == []


def test_unsent_local_backup_is_a_conflict_not_a_restore(tmp_path):
    """Regresja F3 — odtworzona utrata zapisu.

    Gra na Machine o 12:00, kopia lokalna powstała, wysyłka padła (offline).
    Chmura ma kopię Decka z 10:00, której lokalny katalog nie ma, więc podgląd
    pobrania pokazuje różnicę. Traktowanie tego jako "chmura nowsza" nadpisywało
    świeższy zapis starszym — bez błędu i bez konfliktu.
    """
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, local_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == [], "starszy zapis z chmury nadpisał świeższy lokalny"
    assert result["conflicts"] == ["Hades"]
    assert saves.restored == [] and saves.downloads == 0
    assert reg.get("hades")["conflict"] is True


def test_local_ahead_alone_is_only_skipped(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(local_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["skipped"] == ["Hades"]
    assert saves.downloads == 0 and saves.restored == [] and saves.safety == []


def test_conflicted_game_gets_its_safety_backup_before_download(tmp_path):
    """`cloud download` nadpisuje lokalny katalog kopii, więc kopia zapasowa gry
    w konflikcie musi powstać wcześniej — inaczej nie ma z czego wracać."""
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well"}, changed={"Animal Well"})
    SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert ("safety", "Animal Well") in saves.events
    assert saves.events.index(("safety", "Animal Well")) < saves.events.index(("download",))
    assert saves.events.index(("safety", "Hades")) < saves.events.index(("download",))


def test_failed_safety_backup_of_conflicted_game_is_an_error(tmp_path):
    reg = _reg(tmp_path, "Animal Well")
    saves = FakeSaves(cloud_ahead={"Animal Well"}, changed={"Animal Well"},
                      safety_ok=False)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["conflicts"] == ["Animal Well"]
    assert result["errors"], "brak zapasu przed rozstrzygnięciem konfliktu to błąd"


def test_record_without_title_key_is_an_error_not_an_exception(tmp_path):
    path = tmp_path / "games.json"
    path.write_text(json.dumps({"hades": {"title": "Hades"}}), encoding="utf-8")
    reg = Registry(str(path))
    saves = FakeSaves(cloud_ahead={"Hades"}, changed={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["errors"], "rekord bez title_key musi trafić do errors"
    assert result["conflicts"] == [] and result["restored"] == []
    assert saves.restored == []


def test_failed_cloud_state_touches_nothing(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, state_ok=False)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["errors"], "awaria nie może wyglądać jak brak roboty"
    assert result == {"restored": [], "conflicts": [], "skipped": [], "blocked": [],
                      "errors": result["errors"]}
    assert saves.downloads == 0 and saves.restored == [] and saves.safety == []


def test_failed_cloud_download_blocks_every_restore(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, download_ok=False)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert saves.restored == [], "nie przywracamy z niespójnego katalogu kopii"
    assert result["errors"] and result["restored"] == []


def test_failed_safety_backup_skips_the_game(tmp_path):
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well"}, safety_ok=False)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert saves.restored == [], "bez kopii bezpieczeństwa nie nadpisujemy zapisu"
    assert len(result["errors"]) == 2
    assert result["restored"] == []
    assert saves.downloads == 0


def test_failed_restore_is_an_error_not_a_skip(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, restore_ok=False)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["errors"] and result["restored"] == [] and result["skipped"] == []


def test_sync_all_blocks_running_game(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda title: True).sync_all()
    assert result["blocked"] == ["Hades"]
    assert result["skipped"] == []
    assert saves.restored == [] and saves.downloads == 0


def test_game_launched_after_measurement_is_blocked(tmp_path):
    """Gra wstaje między pomiarem a przywróceniem — sprawdzamy is_running dwa razy."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    seen = []

    def is_running(title):
        seen.append(title)
        return len(seen) > 1

    result = SyncService(reg, saves, is_running=is_running).sync_all()
    assert result["blocked"] == ["Hades"]
    assert saves.restored == []


def test_unknown_local_state_marks_conflict_instead_of_restoring(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, unknown={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["conflicts"] == ["Hades"]
    assert saves.restored == []
    assert reg.get("hades")["conflict"] is True


def test_record_with_empty_title_is_skipped(tmp_path):
    # Registry już odrzuca taki zapis, ale plik z wcześniejszej wersji wtyczki
    # (albo poprawiony ręcznie) wciąż może go zawierać.
    path = tmp_path / "games.json"
    path.write_text(json.dumps({"puste": {"title_key": "puste", "title": " "}}),
                    encoding="utf-8")
    reg = Registry(str(path))
    saves = FakeSaves()
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert saves.states == 0, "pusty tytuł to wywołanie Ludusavi bez filtra gry"
    assert saves.local_queries == []
    assert result["skipped"] == []


def test_sync_all_ignores_excluded_games(tmp_path):
    reg = _reg(tmp_path, "Hades", excluded=True)
    saves = FakeSaves(cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result == {"restored": [], "conflicts": [], "skipped": [], "blocked": [],
                      "errors": []}
    assert saves.states == 0


def test_sync_all_marks_conflict_in_registry(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, changed={"Hades"})
    SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert reg.get("hades")["conflict"] is True


def test_similar_cloud_title_never_restores_a_different_game(tmp_path):
    """„Hades II" w chmurze nie może przywrócić „Hadesa" — dopasowanie tytułu
    jest równością, nie zawieraniem. Podciąg nadpisałby żywy zapis innej gry."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades II"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["skipped"] == ["Hades"]
    assert result["restored"] == []
    assert result["conflicts"] == []
    assert saves.restored == []
    assert saves.safety == []
    assert saves.downloads == 0


def test_similar_local_title_is_not_a_conflict(tmp_path):
    """Odwrotny kierunek tej samej pomyłki: „Hades II" z niewysłaną kopią nie
    zamienia czystego przywrócenia „Hadesa" w konflikt."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, local_ahead={"Hades II"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["restored"] == ["Hades"]
    assert result["conflicts"] == []


def test_resolved_conflict_flag_is_cleared_in_registry(tmp_path):
    """Flaga konfliktu nie może zostać w rejestrze (i w interfejsie) na zawsze."""
    reg = _reg(tmp_path, "Hades", "Animal Well")
    reg.set_fields("hades", conflict=True)
    reg.set_fields("animal-well", conflict=True)
    saves = FakeSaves(cloud_ahead={"Hades"})   # Hades → restore, Animal Well → skip
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["restored"] == ["Hades"]
    assert result["skipped"] == ["Animal Well"]
    assert reg.get("hades")["conflict"] is False
    assert reg.get("animal-well")["conflict"] is False


def test_running_game_keeps_its_conflict_flag(tmp_path):
    """Gra uruchomiona = stanu nie zmierzyliśmy do końca, więc nie czyścimy flagi."""
    reg = _reg(tmp_path, "Hades")
    reg.set_fields("hades", conflict=True)
    saves = FakeSaves(cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: True).sync_all()

    assert result["blocked"] == ["Hades"]
    assert reg.get("hades")["conflict"] is True


def test_no_game_lands_in_two_result_lists(tmp_path):
    """Wynik jest podziałem biblioteki: jedna gra = jeden wyrok."""
    reg = _reg(tmp_path, "Hades", "Animal Well", "Celeste", "Tunic")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well", "Tunic"},
                      changed={"Animal Well"})
    service = SyncService(reg, saves,
                          is_running=lambda title: title == "Tunic")
    result = service.sync_all()

    listed = (result["restored"] + result["conflicts"]
              + result["skipped"] + result["blocked"])
    assert sorted(listed) == ["Animal Well", "Celeste", "Hades", "Tunic"]
    assert len(listed) == len(set(listed))


# --- G2 od końca do końca: pierwsze uruchomienie gry na drugim urządzeniu ---

def test_first_run_on_second_device_restores_from_cloud(tmp_path):
    """Regresja G2 na prawdziwym Saves: gra bez ŻADNYCH lokalnych zapisów, a w
    chmurze leży kopia. Atrapa odpowiada jak zmierzone Ludusavi — gra bez zapisów
    daje kod 0 i {"games": {}} w OBU wywołaniach: w podglądzie i w kopii
    bezpieczeństwa (nie da się skopiować pliku, którego nie ma). Wcześniej bramka
    wymagała udanej kopii, więc podstawowa funkcja projektu nigdy nie działała."""
    reg = _reg(tmp_path, "007 First Light")
    no_saves = _real("BACKUP_PREVIEW_NO_SAVES_STDOUT")
    saves, runner = _saves({
        "cloud download --preview": (0, "", json.dumps(
            {"cloud": {"007 First Light/backup-20260819T120000Z/save.dat":
                       {"change": "New"}}})),
        "cloud upload --preview": (0, "", json.dumps({"games": {}})),
        "backup --preview": (0, no_saves, ""),
        "--path /tmp/safety": (0, no_saves, ""),
        "cloud download --force": (0, "", ""),
        "restore": (0, json.dumps({"games": {"007 First Light": {
            "decision": "Processed",
            "files": {"/a/save.dat": {"change": "New", "bytes": 10}}}}}), ""),
    })
    result = SyncService(reg, saves, is_running=lambda title: False).sync_all()
    assert result["restored"] == ["007 First Light"], result
    assert result["conflicts"] == [] and result["errors"] == []
    assert any("restore" in " ".join(argv) for argv in runner.calls), \
        "przywracanie nie zostało nawet wywołane"


def test_nothing_to_protect_never_blocks_the_restore(tmp_path):
    """Ta sama bramka na atrapie Saves: "nie ma czego chronić" (None) przepuszcza."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, safety_ok=None)
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == ["Hades"], result
    assert result["errors"] == []


def test_broken_safety_backup_still_blocks_the_restore(tmp_path):
    """Awaria kopii (kod != 0) blokuje — na tym stoi ochrona zapisu użytkownika."""
    reg = _reg(tmp_path, "Hades")
    saves, _ = _saves({
        "cloud download --preview": (0, "", json.dumps(
            {"cloud": {"Hades/backup-20260819T120000Z/save.dat": {"change": "New"}}})),
        "cloud upload --preview": (0, "", json.dumps({"games": {}})),
        "backup --preview": (0, _real("BACKUP_PREVIEW_NO_SAVES_STDOUT"), ""),
        "--path /tmp/safety": (1, "", "brak miejsca na karcie"),
        "restore": (0, json.dumps({"games": {"Hades": {
            "decision": "Processed", "files": {"/a/x": {"bytes": 1}}}}}), ""),
    })
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == []
    assert result["errors"] and result["errors"][0]["code"] == "safety_backup_failed"
    assert result["errors"][0]["params"]["title"] == "Hades"


def test_unconfigured_cloud_stops_the_sync_with_a_readable_reason(tmp_path):
    """Bez skonfigurowanej chmury synchronizacja nie może zameldować "brak zmian".

    Odkąd zapisy jeżdżą na karcie, brak chmury NIE zatrzymuje całego przebiegu —
    faza karty działa bez sieci. Tu karty nie ma (brak `card_dir`), więc zostaje
    sama chmura i musi powiedzieć wprost, dlaczego nic nie zrobiła."""
    reg = _reg(tmp_path, "Hades")
    saves, _ = _saves({}, config_path=_config(tmp_path, _CONFIG_UNSET))
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    # `cloud_error` z saves.py przechodzi teraz przez BEZ owijania w
    # `cloud_state_unreadable` (ten pomost zniknął razem z etapem B) — więc kod
    # widoczny tu to kod SAVES, silniejszy dowód niż dawny substring w detail.
    assert "cloud_not_configured" in [e["code"] for e in result["errors"]], result
    assert result["restored"] == [] and result["skipped"] == []


# --- G5: blokada plikowa "jedna synchronizacja naraz" ---

def test_busy_lock_blocks_the_second_run_with_a_message(tmp_path):
    """Regresja G5: blokada w JavaScripcie nie chroni przed drugim przebiegiem po
    stronie Pythona ani przed równolegle działającą wtyczką decky-ludusavi."""
    path = str(tmp_path / "sync.lock")
    with sync_lock(path):
        assert os.path.exists(path)
        try:
            with sync_lock(path):
                raise AssertionError("drugi przebieg wszedł do zamkniętej sekcji")
        except SyncLocked as exc:
            assert exc.msg["code"] == "sync_lock_held", exc.msg
            assert "running" in str(exc).lower(), str(exc)
    assert not os.path.exists(path), "zamek musi zniknąć po zakończeniu"


def test_lock_is_released_even_when_the_run_fails(tmp_path):
    path = str(tmp_path / "sync.lock")
    try:
        with sync_lock(path):
            raise RuntimeError("ludusavi padło")
    except RuntimeError:
        pass
    with sync_lock(path):
        pass


def test_lock_left_by_a_killed_process_does_not_block_forever(tmp_path):
    path = str(tmp_path / "sync.lock")
    dead = 4000000       # ponad pid_max Linuksa — takiego procesu nie ma
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("%d 2026-08-19 10:00:00" % dead)
    with sync_lock(path):
        pass
    assert not os.path.exists(path)


def test_unreadable_lock_does_not_block_forever(tmp_path):
    path = str(tmp_path / "sync.lock")
    open(path, "w", encoding="utf-8").write("")
    with sync_lock(path):
        pass


def test_saves_exposes_the_lock_next_to_the_safety_copies(tmp_path):
    saves = Saves(["ludusavi"], str(tmp_path / "runtime" / "safety"))
    with saves.lock():
        held = os.path.join(str(tmp_path / "runtime"), "sdsync-sync.lock")
        assert os.path.exists(held), os.listdir(str(tmp_path / "runtime"))
        try:
            with saves.lock():
                raise AssertionError("drugi przebieg wszedł do zamkniętej sekcji")
        except SyncLocked:
            pass


def test_download_touches_only_the_games_being_restored(tmp_path):
    """Pobranie z chmury nadpisuje lokalny katalog kopii. Gra, która ma tylko
    NIEWYSŁANĄ kopię lokalną (verdict skip, więc bez kopii bezpieczeństwa), nie może
    stracić tej kopii przez pobranie zrobione dla zupełnie innej gry."""
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Hades"})          # chmura ma nowsze → przywracamy
    reg.upsert({"title": "Animal Well"})    # tylko lokalne nowsze → nie ruszamy

    saves = FakeSaves(cloud_ahead={"Hades"}, local_ahead={"Animal Well"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["restored"] == ["Hades"]
    assert saves.download_args == [["Hades"]], (
        "pobranie musi dotyczyć tylko gier przywracanych, inaczej zamaże "
        "niewysłaną kopię lokalną innej gry"
    )


# --- koszt: jedno wywołanie Ludusavi zamiast jednego na grę ---

def test_local_preview_asks_about_every_game_in_one_call():
    """Regresja wydajności: koszt Ludusavi to start procesu (u nas przez flatpak),
    więc jedno wywołanie na grę mnożyło czekanie przez wielkość biblioteki.
    Ten test pada, gdy ktoś wróci do pętli po tytułach."""
    saves, runner = _saves({"backup --preview": (0, json.dumps({"games": {
        "Hades": {"change": "Different", "files": {}},
        "Animal Well": {"change": "Same", "files": {}},
    }}), "")})
    assert saves.local_changed_many(["Hades", "Animal Well", "007 First Light"]) == {
        "Hades": True,
        "Animal Well": False,
        "007 First Light": False,  # brak wpisu przy kodzie 0 = ta gra nie ma tu zapisów
    }
    assert len(runner.calls) == 1, runner.calls
    assert runner.calls[0][-3:] == ["Hades", "Animal Well", "007 First Light"]


def test_failed_batch_preview_is_unknown_for_every_game():
    """Awaria wywołania zbiorczego nie może udawać 'brak zmian' dla żadnej gry —
    'nie wiem' jest konfliktem, a nie zgodą na nadpisanie zapisu."""
    saves, _ = _saves({"backup --preview": (1, "", "rclone: brak sieci")})
    assert saves.local_changed_many(["Hades", "Animal Well"]) == {
        "Hades": None, "Animal Well": None}


def test_safety_backup_of_many_games_is_one_call():
    saves, runner = _saves({"backup": (0, json.dumps({"games": {
        "Hades": {"decision": "Processed", "files": {"/a": {"bytes": 1}}},
        "Animal Well": {"decision": "Ignored", "files": {"/b": {"bytes": 1}}},
    }}), "")})
    assert saves.safety_backup_many(["Hades", "Animal Well", "007 First Light"]) == {
        "Hades": True,
        "Animal Well": False,        # nieprzetworzona = nie ma kopii
        "007 First Light": None,     # bez wpisu = nie ma czego chronić
    }
    assert len(runner.calls) == 1, runner.calls
    assert "--path /tmp/safety" in " ".join(runner.calls[0])


def test_failed_batch_safety_backup_blocks_every_game():
    """Nie wiadomo, której grze kopia powstała — przywracanie musi stanąć na
    wszystkich, inaczej jedna awaria kasuje cudzy zapis."""
    saves, _ = _saves({"backup": (1, "", "brak miejsca")})
    assert saves.safety_backup_many(["Hades", "Animal Well"]) == {
        "Hades": False, "Animal Well": False}


def test_batch_calls_never_run_without_a_game_filter():
    """Ludusavi bez listy gier pracuje na CAŁEJ bibliotece — pusta lista tytułów
    nie może wyjść poza tę granicę."""
    for titles in ([], ["", " "], None):
        saves, runner = _saves({})
        assert saves.local_changed_many(titles) == {}
        assert saves.safety_backup_many(titles) == {}
        assert runner.calls == [], runner.calls


# --- wybór gry do synchronizacji ---

def test_sync_of_one_game_leaves_the_rest_alone(tmp_path):
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all(["hades"])
    assert result["restored"] == ["Hades"]
    assert saves.local_queries == ["Hades"], "druga gra nie miała być nawet pytana"
    assert saves.download_args == [["Hades"]]


def test_named_game_is_synced_even_when_excluded_from_the_automatic_run(tmp_path):
    """Wskazanie gry z ekranu jest świadomą decyzją użytkownika. Gdyby wykluczenie
    ją biło, przycisk 'Synchronizuj' przy wykluczonej grze nie robiłby nic i nic
    by o tym nie powiedział."""
    reg = _reg(tmp_path, "Hades", excluded=True)
    saves = FakeSaves(cloud_ahead={"Hades"})
    service = SyncService(reg, saves, is_running=lambda t: False)
    assert service.sync_all()["restored"] == [], "automat pomija wykluczone"
    assert service.sync_all(["hades"])["restored"] == ["Hades"]


def test_unknown_title_key_syncs_nothing_at_all(tmp_path):
    """Literówka w kluczu nie może zamienić się w przebieg na całej bibliotece.
    Cisza wyglądałaby jak "zsynchronizowane" — użytkownik wskazał konkretną grę
    i ma prawo wiedzieć, że nic się nie stało (reguła z etapu 5)."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all(["nie-ma"])
    assert result["restored"] == [] and saves.states == 0
    assert result["errors"], "cisza wygląda jak sukces"
    assert any(e["params"].get("titles") == "nie-ma" for e in result["errors"]), \
        result["errors"]


def test_sync_reports_how_long_each_stage_took(tmp_path):
    """Bez pomiaru 'synchronizacja trwa długo' nie mówi, czy skracać wywołania
    Ludusavi, czy czekanie na rclone."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    service = SyncService(reg, saves, is_running=lambda t: False)
    service.sync_all()
    assert set(service.last_seconds) == {"stan_chmury", "podglad_lokalny",
                                         "kopie_bezpieczenstwa",
                                         "pobranie_z_chmury", "przywracanie"}


def test_every_ludusavi_call_survives_a_manifest_update_failure():
    """Regresja ZMIERZONA na Decku: Ludusavi przy każdym wywołaniu sprawdza
    aktualizację manifestu przez sieć i bez sieci PRZERYWA całe polecenie
    („Nie można sprawdzić aktualizacji dla pliku manifest"). Kosztowało to kopie
    bezpieczeństwa dwóch gier — wtyczka zameldowała „konflikt bez kopii
    bezpieczeństwa" i odmówiła ochrony zapisów przez chwilowy brak sieci.
    Flaga musi być na KAŻDEJ trasie, nie tylko na tej, którą akurat naprawiano."""
    saves, runner = _saves({"": (0, json.dumps({"games": {}}), "")})
    saves.cloud_state()
    saves.cloud_download(["Hades"])
    saves.cloud_upload(["Hades"])
    saves.backup("Hades")
    saves.local_changed_many(["Hades"])
    saves.safety_backup_many(["Hades"])
    saves.restore("Hades")
    saves.backups("Hades")
    assert runner.calls, "test bez ani jednego wywołania niczego nie dowodzi"
    for argv in runner.calls:
        assert argv[1] == "--try-manifest-update", argv


def test_sync_finishes_a_deferred_push_before_reading_the_cloud(tmp_path):
    """Wysyłka odłożona (zamek był zajęty przez przebieg) musi zostać dokończona,
    i to PRZED odczytem stanu chmury: inaczej podgląd widzi lokalną kopię, której
    w chmurze jeszcze nie ma, i bierze ją za rozjazd."""
    reg = _reg(tmp_path, "Hades", pending_push=True)
    saves = FakeSaves()
    SyncService(reg, saves, is_running=lambda title: False).sync_all()

    assert saves.events[0] == ("backup", "Hades", True), saves.events
    assert reg.get("hades")["pending_push"] is False, "zaległa wysyłka nie odhaczona"


def test_a_deferred_push_that_still_fails_stays_pending_and_is_reported(tmp_path):
    """Zasada 1: awaria nie może wyglądać jak sukces. Odhaczona, a niedoszła wysyłka
    to po przełożeniu karty gra ze STARSZEGO zapisu z chmury."""
    reg = _reg(tmp_path, "Hades", pending_push=True)
    saves = FakeSaves()
    saves.backup = lambda title, cloud=True: {"ok": False, "changed_bytes": 0,
                                              "conflict": False}
    result = SyncService(reg, saves, is_running=lambda title: False).sync_all()

    assert reg.get("hades")["pending_push"] is True
    assert any(e["params"].get("title") == "Hades" for e in result["errors"]), \
        result["errors"]


def test_total_preview_failure_is_reported_not_just_turned_into_conflicts(tmp_path):
    """Zbiorowy podglad padl -> wszystkie gry to konflikt. Kierunek jest bezpieczny,
    ale bez komunikatu uzytkownik widzi N konfliktow bez przyczyny i nie ma czego
    naprawic - a _stderr_hint dopisuje sie tylko przy niepustym errors."""
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(unknown={"Hades", "Animal Well"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert sorted(result["conflicts"]) == ["Animal Well", "Hades"]
    assert result["errors"], "cisza przy zbiorowej niewiedzy = konflikt bez powodu"


def test_unreadable_cloud_config_is_not_a_silent_success(tmp_path):
    """cloud_remote_set zwraca None, gdy konfiguracji nie da sie przeczytac.
    Ciche przejscie dalej znaczy 'sukces bez efektu': kod 0, pusta lista, wszystkie
    gry pominiete - dokladnie stan, przed ktorym ta kontrola miala chronic."""
    saves, _ = _saves({"cloud": (0, "", "{}")},
                      config_path=str(tmp_path / "nie-ma-takiego-pliku.yaml"))
    saves.cloud_state()
    assert saves.cloud_error, "nieczytelna konfiguracja przeszla bez slowa"


def test_failed_safety_backup_of_one_game_never_unblocks_another(tmp_path):
    """Bramka kopii bezpieczeństwa jest PER GRA. Gdyby udana kopia jednej gry
    odblokowywała przywracanie wszystkich, gra bez kopii zostałaby nadpisana
    danymi z chmury — utrata zapisu bez ostrzeżenia."""
    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = FakeSaves(cloud_ahead={"Hades", "Animal Well"},
                      safety_map={"Hades": True, "Animal Well": False})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["restored"] == ["Hades"]
    assert saves.restored == ["Hades"], "gra bez kopii bezpieczeństwa została nadpisana"
    assert any(e["params"].get("title") == "Animal Well" for e in result["errors"]), \
        result["errors"]
    assert saves.download_args == [["Hades"]], "pobranie objęło grę bez kopii"


def test_restore_reads_real_measured_output():
    """Zmierzona próbka z urządzenia zamiast ręcznie pisanego JSON-a."""
    saves, _ = _saves({"restore": (0, _real("RESTORE_PREVIEW_STDOUT"), "")})
    assert saves.restore("Animal Well") is True


def test_game_missing_from_the_batch_answer_is_unknown_not_unchanged(tmp_path):
    """Zbiorowy podgląd zwrócił odpowiedź, ale bez tej gry. Komentarz w sync.py nazywa
    ten stan „nie wiem"; gdyby był to „brak zmian", chmura nadpisałaby lokalny zapis."""

    class Partial(FakeSaves):
        def local_changed_many(self, titles):
            return {t: False for t in titles if t != "Hades"}   # Hades wypadł

    reg = _reg(tmp_path, "Hades", "Animal Well")
    saves = Partial(cloud_ahead={"Hades", "Animal Well"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["conflicts"] == ["Hades"], result
    assert "Hades" not in saves.restored


def test_ignored_files_are_not_a_local_change():
    """Ludusavi oznacza pliki ignorowane; policzenie ich jako zmiany daje fałszywy
    konflikt i blokuje przywracanie bez powodu."""
    payload = json.dumps({"games": {"Hades": {"change": "Same", "files": {
        "/a/save1": {"change": "New", "bytes": 10, "ignored": True}}}}})
    saves, _ = _saves({"backup --preview": (0, payload, "")})
    assert saves.local_changed("Hades") is False


def test_local_backup_of_a_conflict_never_touches_the_cloud():
    """resolve_conflict('local') robi kopię lokalną, a DOPIERO POTEM wysyła z filtrem
    gry. Synchronizacja chmury w trakcie kopii trafiłaby prosto w cloudConflict."""
    saves, runner = _saves({"backup": (0, _PROCESSED, "")})
    assert saves.backup("Hades", cloud=False)["ok"] is True
    argv = runner.calls[0]
    assert "--no-cloud-sync" in argv and "--cloud-sync" not in argv, argv


# --- redirecty: jedna kopia w chmurze dla obu urządzeń ---
#
# Ta sama gra ma na każdym urządzeniu INNY appid skrótu, a Ludusavi zapisuje w kopii
# ścieżkę bezwzględną żywego zapisu — czyli z appid urządzenia, które robiło kopię.
# ZMIERZONE na Decku i Machine: bez redirectów chmura trzyma dwie rozjechane gałęzie,
# a przywracanie ląduje w prefiksie, którego ta maszyna nie czyta (meldując sukces).
# Semantyka `kind: bidirectional` też jest zmierzona, nie wyczytana: kopia zapisała
# `/sdsync/animal-well/...`, a przywracanie na drugim urządzeniu odtworzyło plik
# w JEGO prefiksie (ten sam sha1).

_CONFIG_BEZ_REDIRECTOW = """---
language: en-US
roots:
  - store: steam
    path: /home/deck/.local/share/Steam
redirects: []
backup:
  path: /home/deck/ludusavi-backup
"""

_REKORDY = [
    {"title": "Animal Well", "title_key": "animal-well", "appid": 2238188571},
    {"title": "Gothic 1 Remake", "title_key": "gothic-1-remake", "appid": 3866710845},
]


def _saves_z_configiem(text: str, prefix_root="/pfxroot"):
    path = os.path.join(tempfile.mkdtemp(prefix="sdsync-redirect-"), "config.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    saves = Saves(["ludusavi"], "/tmp/safety", runner=FakeRunner({}),
                  config_path=path, prefix_root=prefix_root)
    return saves, path


def test_apply_redirects_wpisuje_wpis_na_gre():
    saves, path = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    assert saves.apply_redirects(_REKORDY) is True
    text = open(path, encoding="utf-8").read()
    assert 'source: "/pfxroot/2238188571/pfx"' in text
    assert 'target: "/sdsync/animal-well"' in text
    assert 'source: "/pfxroot/3866710845/pfx"' in text
    assert text.count("kind: bidirectional") == 2
    # reszta konfiguracji nie może zniknąć — to plik użytkownika, nie nasz
    assert "path: /home/deck/ludusavi-backup" in text
    assert "- store: steam" in text


def test_apply_redirects_jest_idempotentne():
    saves, path = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects(_REKORDY)
    pierwszy = open(path, encoding="utf-8").read()
    saves.apply_redirects(_REKORDY)
    assert open(path, encoding="utf-8").read() == pierwszy


def test_apply_redirects_wymienia_nasz_stary_wpis_a_cudzy_zostawia():
    """Appid zmienia się przy ponownym dodaniu gry, więc nasz wpis MUSI być
    nadpisany — inaczej przywracanie jedzie do martwego prefiksu. Wpis
    użytkownika (inny target) nie jest nasz i musi przeżyć."""
    saves, path = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects([{"title": "Animal Well", "title_key": "animal-well",
                            "appid": 111}])
    text = open(path, encoding="utf-8").read().replace(
        "redirects:\n", 'redirects:\n  - kind: restore\n    source: "/stare"\n'
                        '    target: "/nowe"\n', 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    saves.apply_redirects(_REKORDY)
    text = open(path, encoding="utf-8").read()
    assert "/pfxroot/111/pfx" not in text          # nasz stary wpis zniknął
    assert 'target: "/nowe"' in text               # cudzy został
    assert 'source: "/pfxroot/2238188571/pfx"' in text


def test_apply_redirects_bez_appid_nie_wpisuje_nic():
    """Gra bez appid to gra jeszcze nie dodana do Steama — wpis z pustą ścieżką
    kierowałby przywracanie w nieznane."""
    saves, path = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects([{"title": "X", "title_key": "x", "appid": None}])
    assert "redirects: []" in open(path, encoding="utf-8").read()


def test_apply_redirects_bez_pliku_konfiguracji_to_awaria_z_powodem():
    saves = Saves(["ludusavi"], "/tmp/safety", runner=FakeRunner({}),
                  config_path="/nie/ma/takiego/config.yaml")
    assert saves.apply_redirects(_REKORDY) is False
    assert saves.last_problem


# --- bramka: kopia CUDZEGO prefiksu nie może wyglądać na sukces ---

def _odpowiedz(sciezka: str, decision="Processed") -> str:
    return json.dumps({"games": {"Animal Well": {
        "decision": decision, "files": {sciezka: {"change": "New", "bytes": 10}}}}})


_NASZ = ("/pfxroot/2238188571/pfx/drive_c/users/steamuser/AppData/LocalLow/"
         "Billy Basso/Animal Well/AnimalWell.sav")
_CUDZY = ("/pfxroot/2911071959/pfx/drive_c/users/steamuser/AppData/LocalLow/"
          "Billy Basso/Animal Well/AnimalWell.sav")


def _saves_z_prefiksem(reply: str):
    saves, _ = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects(_REKORDY)
    saves.runner = FakeRunner({"ludusavi": (0, reply, "")})
    return saves


def test_backup_cudzego_prefiksu_to_awaria_nie_sukces():
    """ZMIERZONE na Decku: przez nieaktualny shortcuts.vdf Ludusavi robił kopię
    prefiksu 2911071959, choć skrót wtyczki miał 2238188571. Kod 0 i „Processed"
    znaczyły wtedy „zsynchronizowano", a prawdziwy zapis nigdy nie trafił do chmury."""
    saves = _saves_z_prefiksem(_odpowiedz(_CUDZY))
    assert saves.backup("Animal Well", cloud=False)["ok"] is False
    assert saves.last_problem


def test_backup_naszego_prefiksu_dalej_jest_sukcesem():
    saves = _saves_z_prefiksem(_odpowiedz(_NASZ))
    assert saves.backup("Animal Well", cloud=False)["ok"] is True


def test_podglad_cudzego_prefiksu_to_niewiedza_nie_brak_zmian():
    """„Same" na cudzym prefiksie mówiło „nic się nie zmieniło" — a mierzyło nie ten
    plik. Niewiedza (None) idzie na konflikt, czyli do decyzji człowieka."""
    saves = _saves_z_prefiksem(json.dumps({"games": {"Animal Well": {
        "decision": "Processed", "files": {_CUDZY: {"change": "Same"}}}}}))
    assert saves.local_changed("Animal Well") is None


def test_kopia_bezpieczenstwa_cudzego_prefiksu_blokuje_przywracanie():
    """Kopia zabezpieczyła nie ten plik, więc przywracanie z chmury nie ma zapasu."""
    saves = _saves_z_prefiksem(_odpowiedz(_CUDZY))
    assert saves.safety_backup("Animal Well") is False


def test_gra_bez_zapisow_nie_wpada_w_bramke():
    """Kod 0 i puste `games` to „nie ma czego chronić" (G2), nie awaria prefiksu."""
    saves = _saves_z_prefiksem(json.dumps({"games": {}}))
    assert saves.safety_backup("Animal Well") is None
    assert saves.local_changed("Animal Well") is False


# --- kierunek rozjazdu: "Removed" w wysyłce to dowód na CHMURĘ, nie na nas ---

def test_urzadzenie_w_tyle_widzi_chmure_przed_soba_a_nie_rozjazd():
    """ZMIERZONE na Decku, gdy chmura miała o jedną kopię więcej (oba podglądy
    z tej samej chwili, w fixtures jako *_BEHIND_*):

      download: {zapis: "Different", mapping.yaml: "Different"}
      upload:   {zapis: "Removed",   mapping.yaml: "Different"}

    „Removed" w podglądzie WYSYŁKI znaczy „wysłanie skasowałoby to z chmury",
    czyli chmura ma coś, czego my nie mamy — dowód, że przed nami jest CHMURA.
    Liczone jako różnica lokalna dawało rozjazd w obie strony przy KAŻDYM zwykłym
    pobraniu, więc tabela decyzyjna wołała konflikt, choć rozjazdu nie było.
    `mapping.yaml` to indeks Ludusavi, nie zapis — różni się w obu kierunkach
    zawsze, gdy drzewa nie są identyczne, więc nie jest dowodem na nic.
    """
    saves, _ = _saves({
        "cloud download": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_BEHIND_STDERR")),
        "cloud upload": (0, "", _real("CLOUD_UPLOAD_PREVIEW_BEHIND_STDERR")),
    })
    ok, cloud_ahead, local_ahead = saves.cloud_state()
    assert ok is True
    assert cloud_ahead == {"Animal Well"}
    assert local_ahead == set()


def test_nieznany_rodzaj_zmiany_liczy_sie_jako_roznica():
    """Nieznana wartość `change` to niewiedza — a niewiedza idzie na konflikt,
    nie na „wszystko w porządku"."""
    podglad = json.dumps({"games": {}, "cloud": {
        "Animal Well/backup-x/drive-0/sdsync/animal-well/x.sav": {"change": "Cheese"}}})
    saves, _ = _saves({"cloud download": (0, "", podglad)})
    assert saves.cloud_state() == (True, {"Animal Well"}, {"Animal Well"})


def test_sam_mapping_yaml_nie_jest_roznica():
    """Różny indeks przy identycznych kopiach to nie „chmura ma nowszy zapis"."""
    tylko_mapping = json.dumps({"games": {}, "cloud": {
        "Animal Well/mapping.yaml": {"change": "Different"}}})
    saves, _ = _saves({"cloud download": (0, "", tylko_mapping),
                       "cloud upload": (0, "", tylko_mapping)})
    assert saves.cloud_state() == (True, set(), set())


# --- jeden podgląd zamiast dwóch: oba kierunki są w tej samej odpowiedzi ---

def test_jeden_podglad_pobrania_daje_oba_kierunki():
    """ZMIERZONE na Decku (fixtures `*_BOTH_WAYS_*`): przy TYM SAMYM stanie —
    plik tylko lokalny (`.sdsync-probe.txt`) i nowsza kopia tylko w chmurze —
    oba podglądy są dokładnym odbiciem siebie:

      download: probe "Removed",   kopia z chmury "Different"
      upload:   probe "Different",  kopia z chmury "Removed"

    Czyli drugie wywołanie nie niesie ŻADNEJ nowej informacji, a kosztuje osobny
    przebieg rclone po chmurze — a to jest ~90% czasu synchronizacji.
    """
    saves, runner = _saves({
        "cloud download": (0, "", _real("CLOUD_DOWNLOAD_PREVIEW_BOTH_WAYS_STDERR")),
        # gdyby kod tu zajrzał, dostanie dane, ale test niżej pilnuje, że NIE zajrzy
        "cloud upload": (0, "", _real("CLOUD_UPLOAD_PREVIEW_BOTH_WAYS_STDERR")),
    })
    ok, cloud_ahead, local_ahead = saves.cloud_state()
    assert ok is True
    assert cloud_ahead == {"Animal Well"}   # "Different" — chmura ma nowszą kopię
    assert local_ahead == {"Animal Well"}   # "Removed"   — my mamy plik, chmura nie
    cloud_calls = [c for c in runner.calls if "cloud" in " ".join(c)]
    assert len(cloud_calls) == 1, "stan chmury to JEDNO wywołanie sieciowe"
    assert not any("upload" in " ".join(c) for c in cloud_calls)


def test_stan_chmury_bez_roznic_to_jedno_wywolanie_i_brak_rozjazdu():
    saves, runner = _saves({"cloud download": (0, "", json.dumps({"games": {},
                                                                  "cloud": {}}))})
    assert saves.cloud_state() == (True, set(), set())
    assert len([c for c in runner.calls if "cloud" in " ".join(c)]) == 1


def test_udane_przywrocenie_zapisuje_znacznik_kopii(tmp_path):
    """Ekran gry czyta `last_backup_ts` (status.ts: „brak kopii zapisów").
    Znacznik ustawiała tylko WYSYŁKA, więc urządzenie, które właśnie pobrało zapis
    z chmury, twierdziło, że kopii nie ma — zaraz po notyfikacji o udanej
    synchronizacji. Zgłoszone z urządzenia po przełożeniu karty."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"})
    assert reg.all()[0]["last_backup_ts"] is None
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == ["Hades"]
    assert reg.all()[0]["last_backup_ts"], "przywrócenie DOWODZI, że kopia istnieje"


def test_nieudane_przywrocenie_nie_zapisuje_znacznika_kopii(tmp_path):
    """Znacznik to obietnica dla użytkownika, nie ozdoba — po awarii nie wolno go
    postawić, bo ekran gry pokazałby kopię, której nikt nie potwierdził."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves(cloud_ahead={"Hades"}, restore_ok=False)
    SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert reg.all()[0]["last_backup_ts"] is None


# --- zapisy jadą na karcie: karta jest transportem, chmura kopią zapasową ---
#
# Bez karty nie da się w tę grę zagrać, więc karta jest jedynym nośnikiem, który
# ZAWSZE ma najświeższy stan — i jedynym, który nie potrzebuje sieci. Stąd kolejność:
# najpierw karta, chmura potem i tylko jako kopia. Kart może być dowolnie wiele, więc
# zapis gry leży na TEJ karcie, na której leży gra — nic globalnego.

_KARTA = "/run/media/deck/SD256/.sdsync/saves"


def _saves_karta(reply_by_needle):
    saves, _ = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects(_REKORDY)
    runner = FakeRunner(reply_by_needle)
    saves.runner = runner
    return saves, runner


def test_kopia_na_karte_idzie_do_katalogu_karty_i_nie_dotyka_sieci():
    saves, runner = _saves_karta({"backup": (0, _odpowiedz(_NASZ), "")})
    assert saves.card_backup_many(["Animal Well"], _KARTA) == {"Animal Well": True}
    argv = runner.calls[0]
    assert "--path" in argv and argv[argv.index("--path") + 1] == _KARTA
    assert "--no-cloud-sync" in argv, "faza karty nie ma prawa czekać na rclone"
    assert argv[argv.index("--full-limit") + 1] == CARD_FULL_LIMIT


def test_kopia_na_karte_cudzego_prefiksu_to_awaria():
    """Ta sama bramka co przy chmurze: kopia nie tego prefiksu nie jest sukcesem."""
    saves, _ = _saves_karta({"backup": (0, _odpowiedz(_CUDZY), "")})
    assert saves.card_backup_many(["Animal Well"], _KARTA) == {"Animal Well": False}


def test_stan_karty_to_JEDNO_wywolanie_na_karte_i_najnowszy_wpis():
    """Ludusavi kosztuje startem procesu, więc pytamy raz na kartę, nie raz na grę.
    Najnowszy `when` bierzemy jako max po tekście — format ISO-8601 UTC z fixtures
    porządkuje się leksykograficznie, więc nie parsujemy dat i nie zależymy od
    kolejności listy."""
    reply = json.dumps({"games": {
        "Animal Well": {"backups": [
            {"name": "backup-20260820T165236Z", "when": "2026-08-20T16:52:36.4Z"},
            {"name": ".", "when": "2026-08-19T16:34:52.6Z"}]},
        "Hades": {"backups": []},
    }})
    saves, runner = _saves_karta({"backups": (0, reply, "")})
    assert saves.card_when_many(["Animal Well", "Hades", "Gothic"], _KARTA) == {
        "Animal Well": "2026-08-20T16:52:36.4Z",
        "Hades": None,     # katalog kopii jest, ale ta gra nie ma tam nic
        "Gothic": None,    # gry nie ma w odpowiedzi wcale
    }
    assert len(runner.calls) == 1, "jedno wywołanie na kartę, nie na grę"
    argv = runner.calls[0]
    assert argv[argv.index("--path") + 1] == _KARTA


def test_stan_karty_z_prawdziwej_odpowiedzi_ludusavi():
    saves, _ = _saves_karta({"backups": (0, _real("BACKUPS_STDOUT"), "")})
    assert saves.card_when_many(["Animal Well"], _KARTA) == {
        "Animal Well": "2026-08-19T16:34:52.601297553Z"}


def test_stan_karty_przy_awarii_to_niewiedza_a_nie_brak_kopii():
    """None znaczy „karta nie ma kopii tej gry" i przepuszcza przywrócenie z chmury.
    Awaria wywołania NIE MOŻE tak wyglądać — inaczej nieczytelna karta cicho oddaje
    decyzję chmurze, która może mieć starszy stan."""
    saves, _ = _saves_karta({"backups": (1, "", "nie udało się")})
    assert saves.card_when_many(["Animal Well"], _KARTA) is None


def test_przywrocenie_z_karty_czyta_katalog_karty():
    saves, runner = _saves_karta({"restore": (0, _odpowiedz(_NASZ), "")})
    assert saves.card_restore("Animal Well", _KARTA) is True
    argv = runner.calls[0]
    assert argv[argv.index("--path") + 1] == _KARTA
    assert "restore" in argv


def test_przywrocenie_z_karty_bez_katalogu_nie_rusza_niczego():
    """Pusta ścieżka karty to wywołanie BEZ --path, czyli praca na katalogu chmury —
    zupełnie inna operacja niż zamierzona."""
    saves, runner = _saves_karta({"restore": (0, _odpowiedz(_NASZ), "")})
    assert saves.card_restore("Animal Well", "") is False
    assert runner.calls == []


# --- faza karty w przebiegu synchronizacji ---

class FakeSavesKarta(FakeSaves):
    """FakeSaves + warstwa karty. `card` to {tytuł: `when`} — stan karty widziany
    przez Ludusavi; None/brak znaczy „karta nie ma kopii tej gry"."""

    def __init__(self, card=None, card_when_ok=True, card_backup_ok=True,
                 card_restore_ok=True, **kwargs):
        super().__init__(**kwargs)
        self.card = dict(card or {})
        self.card_when_ok = card_when_ok
        self.card_backup_ok = card_backup_ok
        self.card_restore_ok = card_restore_ok
        self.card_paths = []
        self.card_written = []
        self.card_restored = []

    def card_when_many(self, titles, path):
        self.card_paths.append(path)
        self.events.append(("card_when", path))
        if not self.card_when_ok:
            return None
        return {t: self.card.get(t) for t in titles}

    def card_backup_many(self, titles, path):
        self.card_written.extend(titles)
        self.events.append(("card_backup", tuple(titles)))
        if not self.card_backup_ok:
            return {t: False for t in titles}
        for index, title in enumerate(titles):
            self.card[title] = "2026-08-20T20:00:0%d.0Z" % index
        return {t: True for t in titles}

    def card_restore(self, title, path):
        if not self.card_restore_ok:
            return False
        self.card_restored.append(title)
        self.events.append(("card_restore", title))
        return True


_KARTA_DIR = "/run/media/deck/SD256/.sdsync/saves"


def _serwis(reg, saves, karta=_KARTA_DIR):
    return SyncService(reg, saves, is_running=lambda t: False,
                       card_dir=lambda record: karta)


def test_karta_z_nowsza_kopia_przywraca_bez_dotykania_chmury(tmp_path):
    """Sedno całej zmiany: bez karty nie da się zagrać, więc karta zawsze ma
    najświeższy stan. Chmura nie jest wtedy potrzebna ANI do decyzji, ANI do
    pobrania — a to ona zjada 90% czasu przebiegu."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"})
    result = _serwis(reg, saves).sync_all()

    assert result["restored"] == ["Hades"]
    assert saves.card_restored == ["Hades"]
    assert saves.restored == [], "przywrócenie z chmury nie miało prawa się odbyć"
    assert saves.states == 0, "stan chmury to sieć — faza karty nie ma go tykać"
    assert saves.downloads == 0
    assert ("safety", "Hades") in saves.events, "kopia bezpieczeństwa przed nadpisaniem"
    assert saves.events.index(("safety", "Hades")) < saves.events.index(
        ("card_restore", "Hades")), "kopia MUSI być przed przywróceniem"


def test_kopia_z_karty_zapamietana_wiec_drugi_przebieg_nic_nie_robi(tmp_path):
    """`when` jest tożsamością kopii. Bez zapamiętania jej każdy kolejny przebieg
    przywracałby tę samą kopię jeszcze raz."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"})
    _serwis(reg, saves).sync_all()
    assert reg.get("hades")["card_seen"] == {"SD256": "2026-08-20T18:00:00Z"}

    saves2 = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"})
    result = _serwis(reg, saves2).sync_all()
    assert result["restored"] == []
    assert result["skipped"] == ["Hades"]
    assert saves2.card_restored == []


def test_zapis_z_pociagu_trafia_na_karte_i_dopiero_potem_do_chmury(tmp_path):
    """Grałem bez sieci, zapis został tylko u mnie. Przebieg musi go WYWIEŹĆ na kartę,
    bo inaczej drugie urządzenie dostanie starszy stan. Chmura idzie po karcie i tylko
    jako kopia — nigdy nie może dostać stanu, którego karta nie ma, bo wtedy drugie
    urządzenie nie mogłoby karcie zaufać."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-19T10:00:00Z"}, changed={"Hades"})
    reg.set_fields("hades", card_seen={"SD256": "2026-08-19T10:00:00Z"})
    result = _serwis(reg, saves).sync_all()

    assert saves.card_written == ["Hades"]
    assert result["conflicts"] == [], "nasz własny, niewywieziony zapis to nie rozjazd"
    kolejnosc = [e[0] for e in saves.events]
    assert kolejnosc.index("card_backup") < kolejnosc.index("backup"), \
        "karta przed chmurą"
    assert ("backup", "Hades", True) in saves.events, "chmura dostaje kopię zapasową"
    assert reg.get("hades")["card_seen"]["SD256"] != "2026-08-19T10:00:00Z"


def test_nieudana_kopia_na_karte_nie_wysyla_do_chmury(tmp_path):
    """Niezmienny warunek całej konstrukcji: chmura NIE MOŻE wyprzedzić karty.
    Gdyby wyprzedziła, drugie urządzenie widziałoby „karta bez zmian" i grało od
    starszego stanu, mając nowszy w chmurze — czyli dokładnie utratę postępu."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-19T10:00:00Z"}, changed={"Hades"},
                           card_backup_ok=False)
    reg.set_fields("hades", card_seen={"SD256": "2026-08-19T10:00:00Z"})
    result = _serwis(reg, saves).sync_all()

    assert not any(e[0] == "backup" for e in saves.events), "chmura wyprzedziłaby kartę"
    assert result["errors"], "cisza po nieudanej kopii wyglądałaby jak sukces"


def test_karta_nowsza_i_wlasny_zapis_to_rozjazd(tmp_path):
    """Obie strony się ruszyły — decyzja należy do człowieka, nie do wtyczki."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"}, changed={"Hades"})
    result = _serwis(reg, saves).sync_all()

    assert result["conflicts"] == ["Hades"]
    assert saves.card_restored == [] and saves.card_written == []
    assert reg.get("hades")["conflict"] is True


def test_gra_bez_kopii_na_karcie_wraca_do_chmury(tmp_path):
    """Pierwsze uruchomienie gry na nowym urządzeniu albo karta po sformatowaniu:
    karta nie ma czego dziedziczyć, więc chmura jest ratunkiem i wolno ją zapytać."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={}, cloud_ahead={"Hades"})
    result = _serwis(reg, saves).sync_all()

    assert result["restored"] == ["Hades"]
    assert saves.restored == ["Hades"], "przywrócenie z CHMURY"
    assert saves.states == 1


def test_nieczytelna_karta_nie_oddaje_decyzji_chmurze(tmp_path):
    """„Nie wiem, co jest na karcie" to nie „karta jest pusta". Gdyby awaria czytania
    karty przepuszczała grę do fazy chmury, chmura nadpisałaby nowszy stan z karty."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"}, card_when_ok=False,
                           cloud_ahead={"Hades"})
    result = _serwis(reg, saves).sync_all()

    assert saves.restored == [], "chmura nie miała prawa nic przywrócić"
    assert result["conflicts"] == ["Hades"] or result["errors"]


def test_bez_karty_w_czytniku_dziala_stara_droga_przez_chmure(tmp_path):
    """Urządzenie bez karty (gra i tak nie odpali) — zostaje dzisiejszy przepływ."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"}, cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False,
                         card_dir=lambda record: "").sync_all()
    assert result["restored"] == ["Hades"]
    assert saves.restored == ["Hades"]
    assert saves.card_restored == []


def test_kazda_karta_pytana_osobno_i_tylko_raz(tmp_path):
    """Kart może być dowolnie wiele; gra leży na swojej. Pytanie idzie raz na KARTĘ
    (nie raz na grę), bo każde wywołanie Ludusavi to start procesu."""
    reg = Registry(str(tmp_path / "games.json"))
    reg.upsert({"title": "Hades", "card_label": "SD256"})
    reg.upsert({"title": "Gothic", "card_label": "SD256"})
    reg.upsert({"title": "Hades II", "card_label": "SD512"})
    saves = FakeSavesKarta(card={})
    SyncService(reg, saves, is_running=lambda t: False,
                card_dir=lambda r: "/run/media/deck/%s/.sdsync/saves"
                                   % r["card_label"]).sync_all()
    assert sorted(saves.card_paths) == ["/run/media/deck/SD256/.sdsync/saves",
                                        "/run/media/deck/SD512/.sdsync/saves"]


def test_uruchomiona_gra_jest_nietykalna_takze_w_fazie_karty(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"})
    result = SyncService(reg, saves, is_running=lambda t: True,
                         card_dir=lambda record: _KARTA_DIR).sync_all()
    assert result["blocked"] == ["Hades"]
    assert saves.card_restored == []


def test_przywracanie_z_karty_bez_kopii_bezpieczenstwa_jest_wstrzymane(tmp_path):
    """Przywrócenie nadpisuje żywy zapis. Bez kopii bezpieczeństwa nie ma odwrotu,
    więc awaria kopii MUSI zatrzymać przywracanie — tak samo jak w fazie chmury.
    (Ta reguła istniała dla chmury; przy dopisywaniu karty nie miała testu i
    kontrola mutacyjna to wychwyciła.)"""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"}, safety_ok=False)
    result = _serwis(reg, saves).sync_all()

    assert saves.card_restored == [], "nadpisano żywy zapis bez zapasu"
    assert result["restored"] == []
    assert result["errors"], "cisza wyglądałaby jak udana synchronizacja"
    assert any(e["code"] == "safety_backup_failed" and e["params"].get("title") == "Hades"
               for e in result["errors"]), result["errors"]


def test_brak_zapisow_do_ochrony_nie_blokuje_przywracania_z_karty(tmp_path):
    """G3: „ta gra nie ma tu zapisów" (None) to nie awaria — to pierwsze uruchomienie
    na tym urządzeniu. Zablokowanie tego przypadku odcięłoby podstawową funkcję:
    przełożyłem kartę i chcę grać dalej."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-20T18:00:00Z"}, safety_ok=None)
    result = _serwis(reg, saves).sync_all()

    assert result["restored"] == ["Hades"]
    assert saves.card_restored == ["Hades"]


# --- tytuł z dwukropkiem: katalog kopii nazywa się INACZEJ niż gra ---

def test_nazwa_katalogu_kopii_dla_tytulu_z_dwukropkiem():
    """ZMIERZONE na Decku: dla „The Binding of Isaac: Rebirth" Ludusavi utworzył
    katalog `The Binding of Isaac_ Rebirth` — dwukropek na podkreślenie. To był
    otwarty pomiar w AGENTS.md i ukryte ryzyko: `_cloud_diff` rozpoznaje grę po
    PIERWSZYM segmencie ścieżki z chmury, więc bez tego przeliczenia taka gra po
    cichu wypadałaby z synchronizacji (chmura nigdy nie byłaby „przed nami")."""
    assert backup_dir_name("The Binding of Isaac: Rebirth") == "The Binding of Isaac_ Rebirth"
    assert backup_dir_name("Animal Well") == "Animal Well"


def test_chmura_rozpoznaje_gre_z_dwukropkiem_w_tytule():
    podglad = json.dumps({"games": {}, "cloud": {
        "The Binding of Isaac_ Rebirth/backup-x/drive-0/sdsync/isaac/save.dat":
            {"change": "Different"}}})
    saves, _ = _saves({"cloud download": (0, "", podglad)})
    ok, cloud_ahead, local_ahead = saves.cloud_state()
    assert ok is True
    assert cloud_ahead == {"The Binding of Isaac_ Rebirth"}
    # SyncService pyta o grę PO przeliczeniu nazwy, inaczej nigdy nie trafi
    assert backup_dir_name("The Binding of Isaac: Rebirth") in cloud_ahead


def test_gra_z_dwukropkiem_jest_przywracana_z_chmury(tmp_path):
    """Domknięcie od strony przebiegu: bez przeliczenia nazwy gra z dwukropkiem
    zawsze kończyła jako „pominięto", choć chmura miała nowszy zapis."""
    reg = _reg(tmp_path, "The Binding of Isaac: Rebirth")
    saves = FakeSaves(cloud_ahead={"The Binding of Isaac_ Rebirth"})
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert result["restored"] == ["The Binding of Isaac: Rebirth"], result


# --- bramka prefiksu: zapisy POZA compatdata są legalne ---

_CHMURA_STEAMA = "/home/deck/.local/share/Steam/userdata/67291926/250900/remote/save.dat"
_INNY_PREFIKS = ("/pfxroot/2666851887/pfx/drive_c/users/steamuser/AppData/LocalLow/x.sav")


def test_zapisy_w_chmurze_steama_nie_sa_obcym_prefiksem():
    """ZMIERZONE na Decku: „The Binding of Isaac: Rebirth" trzyma zapisy w
    `userdata/<id>/250900/remote` (chmura Steama przez steam_appid.txt), a nie
    w prefiksie skrótu. Bramka miała łapać kopię CUDZEGO prefiksu — jeśli odrzuca
    też legalne miejsca poza compatdata, blokuje grze obsługę zapisów na zawsze."""
    saves = _saves_z_prefiksem(json.dumps({"games": {"Animal Well": {
        "decision": "Processed", "files": {_CHMURA_STEAMA: {"change": "New", "bytes": 5}}}}}))
    assert saves.backup("Animal Well", cloud=False)["ok"] is True


def test_kopia_cudzego_prefiksu_dalej_jest_awaria():
    """Regresja pierwotnego błędu: pliki w INNYM prefiksie compatdata i ani jednego
    w naszym — to wciąż awaria, bo Ludusavi mierzy nie tę grę."""
    saves = _saves_z_prefiksem(_odpowiedz(_CUDZY))
    assert saves.backup("Animal Well", cloud=False)["ok"] is False


def test_zapisy_i_u_nas_i_poza_prefiksem_sa_w_porzadku():
    """Blue Prince na Decku: część w prefiksie Steama, część w prefiksie Heroica."""
    saves = _saves_z_prefiksem(json.dumps({"games": {"Animal Well": {
        "decision": "Processed",
        "files": {_NASZ: {"change": "New", "bytes": 5},
                  "/home/deck/Games/Heroic/Prefixes/default/x/pfx/save.es3":
                      {"change": "New", "bytes": 5}}}}}))
    assert saves.backup("Animal Well", cloud=False)["ok"] is True


# --- jeden nieznany tytuł nie może zatruć całej biblioteki ---

class RunnerZNieznanym:
    """Odwzorowuje ZMIERZONE zachowanie Ludusavi: gdy choć jeden tytuł z listy jest
    mu nieznany, wywala CAŁE polecenie (kod != 0, na stderr „Brak informacji dla tych
    gier"), a nie tylko tę jedną grę. Na Decku jeden taki wpis zamieniał wszystkie
    12 gier w konflikty."""

    def __init__(self, nieznane, zmienione=()):
        self.nieznane = set(nieznane)
        self.zmienione = set(zmienione)
        self.wywolania = []

    def __call__(self, argv, timeout=None):
        self.wywolania.append(argv)
        tytuly = [a for a in argv if not a.startswith("-")][1:]
        zle = [t for t in tytuly if t in self.nieznane]
        if zle:
            return 1, "", "Brak informacji dla tych gier:\n" + "".join("- %s\n" % t for t in zle)
        games = {}
        for t in tytuly:
            games[t] = {"decision": "Processed",
                        "change": "Different" if t in self.zmienione else "Same",
                        "files": {_NASZ: {"change": "Different" if t in self.zmienione else "Same",
                                          "bytes": 10}}}
        return 0, json.dumps({"games": games}), ""


def _saves_z_runnerem(runner):
    saves, _ = _saves_z_configiem(_CONFIG_BEZ_REDIRECTOW)
    saves.apply_redirects([
        {"title": t, "title_key": t.lower().replace(" ", "-"), "appid": 2238188571}
        for t in ("Animal Well", "Gothic 1 Remake", "GTA V Enhanced")
    ])
    saves.runner = runner
    return saves


def test_nieznany_tytul_nie_zatruwa_podgladu_pozostalych_gier():
    """Sedno błędu z urządzenia: „GTA V Enhanced" nieznane bazie Ludusavi, a konflikt
    dostawało wszystkie 12 gier. Po zejściu na wywołania pojedyncze prawdę znamy
    o każdej grze osobno, a nieznana jest wskazana z nazwy."""
    runner = RunnerZNieznanym(nieznane={"GTA V Enhanced"}, zmienione={"Gothic 1 Remake"})
    saves = _saves_z_runnerem(runner)
    wynik = saves.local_changed_many(["Animal Well", "Gothic 1 Remake", "GTA V Enhanced"])

    assert wynik["Animal Well"] is False, wynik
    assert wynik["Gothic 1 Remake"] is True, wynik
    assert wynik["GTA V Enhanced"] is None, wynik
    assert saves.unknown_titles == {"GTA V Enhanced"}, saves.unknown_titles


def test_zejscie_na_pojedyncze_dopiero_po_awarii_zbiorczego():
    """Zbiorcze wywołanie to świadoma optymalizacja (start procesu Ludusavi kosztuje
    kilka sekund) — schodzimy z niej tylko wtedy, gdy padnie."""
    runner = RunnerZNieznanym(nieznane=set())
    saves = _saves_z_runnerem(runner)
    saves.local_changed_many(["Animal Well", "Gothic 1 Remake"])
    assert len(runner.wywolania) == 1, runner.wywolania

    runner2 = RunnerZNieznanym(nieznane={"GTA V Enhanced"})
    saves2 = _saves_z_runnerem(runner2)
    saves2.local_changed_many(["Animal Well", "GTA V Enhanced"])
    # jedno zbiorcze (padło) + po jednym na grę
    assert len(runner2.wywolania) == 3, runner2.wywolania


def test_nieznany_tytul_nie_blokuje_kopii_bezpieczenstwa_innych_gier():
    """Ta sama trucizna dotyczy kopii bezpieczeństwa: bez tego jedna zła nazwa
    blokowała przywracanie WSZYSTKICH gier (kopia = False dla każdej)."""
    runner = RunnerZNieznanym(nieznane={"GTA V Enhanced"})
    saves = _saves_z_runnerem(runner)
    wynik = saves.safety_backup_many(["Animal Well", "GTA V Enhanced"])
    assert wynik["Animal Well"] is True, wynik
    assert wynik["GTA V Enhanced"] is False, wynik
    assert "GTA V Enhanced" in saves.unknown_titles


def test_gra_nieznana_ludusaviemu_nie_jest_konfliktem_a_bledem(tmp_path):
    """Wyrok dla takiej gry ma być KONKRETNY: „Ludusavi nie zna tytułu", przy tej
    jednej grze. Pozostałe idą normalnym torem."""
    reg = _reg(tmp_path, "Hades", "GTA V Enhanced")
    saves = FakeSaves(cloud_ahead={"Hades"})
    saves.unknown_titles = {"GTA V Enhanced"}
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()

    assert result["restored"] == ["Hades"], result
    assert "GTA V Enhanced" not in result["conflicts"], result
    assert any(e["params"].get("title") == "GTA V Enhanced" for e in result["errors"]), \
        result
    assert reg.get("gta-v-enhanced")["ludusavi_unknown"] is True


def test_oflagowana_gra_wypada_z_wywolan_ludusavi(tmp_path):
    """Po oflagowaniu wracamy do JEDNEGO wywołania zbiorczego — inaczej każdy przebieg
    płaciłby za tę jedną złą nazwę zejściem na wywołania pojedyncze."""
    reg = _reg(tmp_path, "Hades", "GTA V Enhanced")
    reg.set_fields("gta-v-enhanced", ludusavi_unknown=True)
    saves = FakeSaves(cloud_ahead={"Hades"})
    SyncService(reg, saves, is_running=lambda t: False).sync_all()
    assert saves.local_queries == ["Hades"], saves.local_queries


def test_martwe_ludusavi_nie_oflagowuje_gier_jako_nieznanych():
    """Rozróżnienie, które ratuje bibliotekę: gdy pada ZBIORCZE wywołanie i każde
    pojedyncze też, problemem jest narzędzie (brak Ludusavi, brak sieci na manifest),
    a nie tytuły. Oflagowanie ich wtedy jako „baza nie zna" odcięłoby WSZYSTKIE gry
    od obsługi zapisów — i to na stałe, bo flaga siedzi w rejestrze."""
    saves, _ = _saves({})  # runner odpowiada „{}" na wszystko → każde wywołanie pada
    wynik = saves.local_changed_many(["Animal Well", "Gothic 1 Remake"])
    assert wynik == {"Animal Well": None, "Gothic 1 Remake": None}, wynik
    assert saves.unknown_titles == set(), saves.unknown_titles


# --- wybór: chmura + karta czy tylko karta ---

def test_tylko_karta_nie_dotyka_sieci_ani_razu(tmp_path):
    """Świadomy wybór użytkownika, nie awaria: przy „tylko karta" cała faza chmury
    wypada, a z nią 60–140 s czekania na rclone (ZMIERZONE). Gra, której karta nie
    ma czym obsłużyć, kończy jako pominięta — nie jako błąd, bo to nie awaria."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={}, cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False,
                         card_dir=lambda r: _KARTA_DIR,
                         cloud_enabled=lambda: False).sync_all()

    assert saves.states == 0, "faza chmury ruszyła mimo wyboru „tylko karta”"
    assert saves.downloads == 0
    assert result["skipped"] == ["Hades"], result
    assert result["errors"] == [], result


def test_domyslnie_chmura_dziala_jak_dotad(tmp_path):
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={}, cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False,
                         card_dir=lambda r: _KARTA_DIR).sync_all()
    assert saves.states == 1
    assert result["restored"] == ["Hades"], result


# --- gry z dysku: nośnik lokalny nie jeździ, więc chmura decyduje ---

def test_gra_z_dysku_zawsze_pyta_chmure(tmp_path):
    """Karta JEŹDZI, więc „na karcie nic nowego" znaczy „nikt nic nie dodał" i wolno
    pominąć chmurę. Katalog na dysku nie jeździ: gdyby obsłużenie gry lokalnie
    zamykało przebieg, drugie urządzenie po pierwszym własnym zapisie przestałoby
    widzieć nowsze zapisy z pierwszego."""
    reg = _reg(tmp_path, "Hades", carrier="disk")
    saves = FakeSavesKarta(card={"Hades": "2026-08-22T10:00:00Z"}, cloud_ahead={"Hades"})
    result = SyncService(reg, saves, is_running=lambda t: False,
                         card_dir=lambda r: _KARTA_DIR).sync_all()

    assert saves.states == 1, "faza chmury musi ruszyć dla gry z dysku"
    assert result["restored"] == ["Hades"], result
    assert saves.restored == ["Hades"], "przywrócenie z CHMURY, nie z lokalnego nośnika"
    assert saves.card_restored == [], "lokalny nośnik nie jest transportem"


def test_gra_z_dysku_odklada_kopie_na_nosnik_lokalny(tmp_path):
    """Kopia lokalna nadal powstaje — to ona daje zapis poza prefiksem Protona
    i ona jest warunkiem wysyłki do chmury."""
    reg = _reg(tmp_path, "Hades", carrier="disk")
    saves = FakeSavesKarta(card={}, changed={"Hades"})
    SyncService(reg, saves, is_running=lambda t: False,
                card_dir=lambda r: _KARTA_DIR).sync_all()

    assert saves.card_written == ["Hades"], saves.card_written
    kolejnosc = [e[0] for e in saves.events]
    assert kolejnosc.index("card_backup") < kolejnosc.index("backup"), "nośnik przed chmurą"


def test_gra_z_karty_dalej_omija_chmure(tmp_path):
    """Regresja: zmiana dla dysku nie może zabrać kartom ich szybkiej drogi."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSavesKarta(card={"Hades": "2026-08-22T10:00:00Z"})
    result = SyncService(reg, saves, is_running=lambda t: False,
                         card_dir=lambda r: _KARTA_DIR).sync_all()
    assert saves.states == 0, "karta obsłużyła grę, chmura była niepotrzebna"
    assert saves.card_restored == ["Hades"], result


def test_nieznany_tytul_wraca_jako_kod_z_parametrem(tmp_path):
    """ZMIERZONE na Decku: jeden tytuł nieznany bazie Ludusavi dawał 12 widmowych
    konfliktów. Komunikat o tym MUSI nazywać grę — i musi to robić parametrem,
    nie sklejonym zdaniem, bo inaczej nie da się go przetłumaczyć."""
    reg = _reg(tmp_path, "GTA V Enhanced")
    reg.set_fields("gta-v-enhanced", ludusavi_unknown=True)
    saves = FakeSaves()
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all()
    kody = [e["code"] for e in result["errors"]]
    assert "ludusavi_unknown_title" in kody, result["errors"]
    wpis = next(e for e in result["errors"] if e["code"] == "ludusavi_unknown_title")
    assert wpis["params"]["title"] == "GTA V Enhanced", wpis


def test_kazdy_blad_przebiegu_ma_kod_i_zlozone_zdanie(tmp_path):
    """Żaden element `errors` nie może być napisem: frontend woła na nich
    `fromBackend`, a napis przeszedłby jako nieprzetłumaczalny."""
    reg = _reg(tmp_path, "Hades")
    saves = FakeSaves()
    result = SyncService(reg, saves, is_running=lambda t: False).sync_all(
        title_keys=["gra-ktorej-nie-ma"])
    assert result["errors"], "wskazanie gry poza rejestrem nie może być ciche"
    for entry in result["errors"]:
        assert isinstance(entry, dict), entry
        assert entry["code"] and entry["message"], entry
        assert "{" not in entry["message"], ("dziura w zdaniu", entry)
        assert "BAD PARAMS" not in entry["message"], entry


def test_zajety_zamek_niesie_kod_i_angielskie_zdanie(tmp_path):
    """`main.py` robił dotąd `str(exc)` i podawał to użytkownikowi jako gotowy
    komunikat — więc wyjątek musi nieść kod, a nie samo zdanie."""
    from sdsync.messages import msg as _msg
    path = str(tmp_path / "sync.lock")
    with sync_lock(path):
        try:
            with sync_lock(path):
                raise AssertionError("drugi zamek się udał")
        except SyncLocked as exc:
            assert exc.msg["code"] == "sync_lock_held", exc.msg
            assert str(exc) == exc.msg["message"]
            assert "process" in str(exc).lower(), str(exc)


# ---------- skąd wiadomo, KTÓRA kopia jest nowsza ----------
#
# ZGŁOSZONE: „przy tych przyciskach fajnie, jakby była data i godzina synchronizacji,
# bo nie wiem, który jest nowszy". Trzy kopie, trzy różne źródła daty — i żadnego
# wspólnego, więc każde trzeba zmierzyć osobno.

CONFIG_SAMPLE = """manifest:
  enable: true
backup:
  path: /home/deck/ludusavi-backup
cloud:
  remote:
    GoogleDrive:
      id: ludusavi-1787098149
  path: ludusavi-backup
  synchronize: true
apps:
  rclone:
    path: /app/bin/rclone
    arguments: "--fast-list --ignore-checksum"
customGames: []
"""


def test_cloud_target_reads_the_remote_id_and_path(tmp_path):
    # ZMIERZONE na Decku: identyfikator zdalnego leży pod `cloud.remote.<nazwa>.id`,
    # a katalog pod `cloud.path` — czyli adres to „<id>:<path>".
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE)
    assert cloud_target(str(cfg)) == ("ludusavi-1787098149", "ludusavi-backup")


def test_cloud_target_of_an_unconfigured_cloud_is_empty(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("cloud:\n  remote: ~\n  path: ludusavi-backup\n")
    assert cloud_target(str(cfg)) == (None, None)
    assert cloud_target(str(tmp_path / "nie-ma.yaml")) == (None, None)


def test_cloud_when_takes_the_newest_backup_folder(tmp_path):
    """ZMIERZONE na Decku: katalog kopii w chmurze NAZYWA SIĘ znacznikiem czasu
    (`backup-20260820T231843Z`), więc rclone lsjson wystarczy i nie trzeba ściągać
    ani jednego bajtu zapisu. `mapping.yaml` obok NIE jest kopią i nie może wygrać."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE)
    wywolania = []

    def runner(argv, timeout=None):
        wywolania.append(argv)
        return 0, json.dumps([
            {"Name": "backup-20260819T101010Z", "IsDir": True},
            {"Name": "mapping.yaml", "IsDir": False},
            {"Name": "backup-20260820T231843Z", "IsDir": True},
            # cudzy katalog sortujący się PO kopiach — bez filtra po „backup-"
            # wygrałby `max()` i data wyszłaby z nazwy, która datą nie jest.
            # Katalog kopii Ludusavi bywa zaśmiecony z zewnątrz (AGENTS.md:
            # 36 MB cudzych plików na Machine), więc to nie jest hipoteza.
            {"Name": "zrzuty-ekranu", "IsDir": True},
        ]), ""

    saves = Saves(["flatpak", "run", "com.github.mtkennerly.ludusavi"], str(tmp_path / "safety"),
                  runner=runner, config_path=str(cfg))
    assert saves.cloud_when("Baba Is You") == "2026-08-20T23:18:43Z"
    argv = wywolania[0]
    assert argv[:4] == ["flatpak", "run", "--command=rclone",
                        "com.github.mtkennerly.ludusavi"], argv
    assert argv[-1] == "ludusavi-1787098149:ludusavi-backup/Baba Is You", argv


def test_cloud_when_uses_the_backup_folder_name_not_the_title(tmp_path):
    """Ludusavi zamienia dwukropek na podkreślenie (ZMIERZONE), a w chmurze katalog
    nazywa się tak samo — pytanie o tytuł z dwukropkiem trafiłoby w nieistniejącą
    ścieżkę i gra wyglądałaby na „bez kopii w chmurze"."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE)
    argvs = []

    def runner(argv, timeout=None):
        argvs.append(argv)
        return 0, "[]", ""

    saves = Saves(["flatpak", "run", "com.github.mtkennerly.ludusavi"], str(tmp_path / "safety"),
                  runner=runner, config_path=str(cfg))
    saves.cloud_when("The Binding of Isaac: Rebirth")
    assert argvs[0][-1].endswith("/The Binding of Isaac_ Rebirth"), argvs[0]


def test_cloud_when_is_none_when_the_cloud_cannot_be_asked(tmp_path):
    """„Nie wiem" nie może udawać „nie ma kopii": z pustą datą przy braku sieci
    użytkownik wybierałby kartę, sądząc, że w chmurze nie ma nic."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE)
    saves = Saves(["flatpak", "run", "x"], str(tmp_path / "safety"),
                  runner=lambda argv, timeout=None: (1, "", "sieć padła"),
                  config_path=str(cfg))
    assert saves.cloud_when("Baba Is You") is None
    saves2 = Saves(["flatpak", "run", "x"], str(tmp_path / "safety"),
                   runner=lambda argv, timeout=None: (0, "to nie jest json", ""),
                   config_path=str(cfg))
    assert saves2.cloud_when("Baba Is You") is None


def test_cloud_when_distinguishes_no_copy_from_no_answer(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_SAMPLE)
    saves = Saves(["flatpak", "run", "x"], str(tmp_path / "safety"),
                  runner=lambda argv, timeout=None: (0, "[]", ""), config_path=str(cfg))
    assert saves.cloud_when("Baba Is You") == ""   # pusto = pytaliśmy, nie ma kopii


def test_live_save_when_is_the_newest_file_in_the_game(tmp_path):
    """Data „mojego zapisu" nie jest datą żadnej kopii — to czas plików W GRZE.
    Ludusavi podaje ich ŚCIEŻKI w podglądzie (ZMIERZONE), a czas czytamy sami."""
    stary = tmp_path / "stary.sav"
    nowy = tmp_path / "nowy.sav"
    stary.write_text("a")
    nowy.write_text("b")
    os.utime(str(stary), (1_600_000_000, 1_600_000_000))
    os.utime(str(nowy), (1_700_000_000, 1_700_000_000))

    def runner(argv, timeout=None):
        return 0, json.dumps({"games": {"Gra": {"decision": "Processed", "files": {
            str(stary): {"change": "Same"}, str(nowy): {"change": "Same"},
            str(tmp_path / "nie-ma.sav"): {"change": "Removed"}}}}}), ""

    saves = Saves(["ludusavi"], str(tmp_path / "safety"), runner=runner)
    assert saves.live_save_when("Gra") == "2023-11-14T22:13:20Z"


def test_live_save_when_says_nothing_when_the_game_has_no_files(tmp_path):
    def runner(argv, timeout=None):
        return 0, json.dumps({"games": {}}), ""

    saves = Saves(["ludusavi"], str(tmp_path / "safety"), runner=runner)
    assert saves.live_save_when("Gra") == ""      # pytaliśmy: gra nie ma zapisów


def test_live_save_when_is_none_when_ludusavi_fails(tmp_path):
    def runner(argv, timeout=None):
        return 1, "", "padło"

    saves = Saves(["ludusavi"], str(tmp_path / "safety"), runner=runner)
    assert saves.live_save_when("Gra") is None    # nie wiem — a to nie „brak zapisu"
