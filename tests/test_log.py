import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.log import EventLog


def test_add_writes_one_json_per_line(tmp_path):
    log = EventLog(str(tmp_path / "brak" / "events.jsonl"))  # katalog jeszcze nie istnieje
    log.add("scan", "znaleziono 2 gry")
    log.add("error", "Ludusavi nie odpowiedział")
    lines = (tmp_path / "brak" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["message"] for x in lines] == [
        "znaleziono 2 gry", "Ludusavi nie odpowiedział"]
    assert json.loads(lines[0])["kind"] == "scan"
    assert json.loads(lines[0])["ts"] > 0


def test_tail_returns_newest_first(tmp_path):
    log = EventLog(str(tmp_path / "events.jsonl"))
    for i in range(5):
        log.add("pull", "wpis %d" % i)
    assert [e["message"] for e in log.tail(3)] == ["wpis 4", "wpis 3", "wpis 2"]
    assert len(log.tail(50)) == 5


def test_rotation_keeps_the_newest_entries_up_to_limit(tmp_path):
    log = EventLog(str(tmp_path / "events.jsonl"), limit=3)
    for i in range(6):
        log.add("pull", "wpis %d" % i)
    assert [e["message"] for e in log.tail(50)] == ["wpis 5", "wpis 4", "wpis 3"]
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # plik naprawdę obcięty, nie tylko widok


def test_broken_line_does_not_hide_the_rest(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(str(path))
    log.add("scan", "przed awarią")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"ts": 1, "kind": "pull", "mes')  # zapis przerwany np. zanikiem prądu
        fh.write("\n")
    log.add("scan", "po awarii")
    assert [e["message"] for e in log.tail(50)] == ["po awarii", "przed awarią"]


def test_tail_survives_a_non_numeric_count(tmp_path):
    """count przychodzi z frontendu przez RPC — „nie-liczba" nie może zgasić
    jedynego okna diagnostycznego użytkownika."""
    log = EventLog(str(tmp_path / "events.jsonl"))
    log.add("scan", "jest wpis")
    assert [e["message"] for e in log.tail("nie-liczba")] == ["jest wpis"]
    assert [e["message"] for e in log.tail(None)] == ["jest wpis"]
    assert [e["message"] for e in log.tail("2")] == ["jest wpis"]  # JSON-owy string liczby
    assert log.tail(0) == []
    assert log.tail(-5) == []


def test_tail_of_missing_file_is_empty(tmp_path):
    assert EventLog(str(tmp_path / "nie-ma.jsonl")).tail() == []


def test_invalid_bytes_do_not_break_the_log(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(str(path))
    log.add("scan", "dobry wpis")
    with open(path, "ab") as fh:
        fh.write(b"\xff\xfe nie-utf8\n")
    assert [e["message"] for e in log.tail(50)] == ["dobry wpis"]


def test_wpis_z_msg_ma_kod_i_parametry(tmp_path):
    from sdsync.messages import msg
    log = EventLog(str(tmp_path / "e.jsonl"))
    log.add("pull", msg("restore_failed", title="Animal Well"))
    entry = log.tail(1)[0]
    assert entry["code"] == "restore_failed"
    assert entry["params"] == {"title": "Animal Well"}
    assert "Animal Well" in entry["message"], "angielskie zdanie zostaje w pliku"


def test_goly_napis_dalej_dziala_i_nie_dostaje_kodu(tmp_path):
    """RPC `log_add` z frontendu przysyła napis (nieudane wstrzyknięcie sekcji
    w ekran gry). Ta droga MUSI zostać: bez niej taka awaria jest cicha."""
    log = EventLog(str(tmp_path / "e.jsonl"))
    log.add("error", "sekcja na ekranie gry nie weszła")
    entry = log.tail(1)[0]
    assert entry["message"] == "sekcja na ekranie gry nie weszła"
    assert "code" not in entry, "napis nie może dostać kodu z powietrza"


def test_stary_wpis_bez_kodu_dalej_sie_czyta(tmp_path):
    """Na urządzeniach użytkownika leży plik logu w STARYM kształcie. Ta zmiana
    nie migruje pliku, więc historia musi zostać czytelna."""
    path = tmp_path / "e.jsonl"
    path.write_text(
        json.dumps({"ts": 1.0, "kind": "pull", "message": "stary polski wpis"}) + "\n",
        encoding="utf-8")
    log = EventLog(str(path))
    log.add("pull", {"code": "restore_failed", "params": {"title": "Hades"},
                     "message": "Hades: restoring failed."})
    got = log.tail(50)
    assert [e["message"] for e in got] == ["Hades: restoring failed.", "stary polski wpis"]
    assert got[0]["code"] == "restore_failed"
    assert "code" not in got[1], "stary wpis nie może zyskać kodu przy czytaniu"


def test_uszkodzony_msg_nie_gasi_logu(tmp_path):
    """Log jest jedynym oknem diagnostycznym — dict bez pól nie może go wywalić."""
    log = EventLog(str(tmp_path / "e.jsonl"))
    log.add("error", {})
    entry = log.tail(1)[0]
    assert entry["kind"] == "error"
    assert entry["code"] == "" and entry["params"] == {}


def test_parametr_nieserializowalny_nie_gasi_logu(tmp_path):
    """Parametry przychodzą z rejestru i z treści wyjątków, więc kiedyś trafi się
    wartość, której JSON nie zna. Wyjątek stąd byłby DRUGĄ awarią, wywołaną przez
    zapisywanie pierwszej — a log jest jedynym oknem diagnostycznym użytkownika."""
    import pathlib
    log = EventLog(str(tmp_path / "e.jsonl"))
    log.add("error", {"code": "card_saves_unreadable",
                      "params": {"path": pathlib.Path("/run/media/SD256")},
                      "message": "Could not read the saves on the card."})
    entry = log.tail(1)[0]
    assert entry["code"] == "card_saves_unreadable"
    assert entry["params"]["path"] == "/run/media/SD256", "treść musi zostać, nie zniknąć"
