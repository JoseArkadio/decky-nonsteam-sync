import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.decisions import decide


def test_table_from_plan_f4():
    # cloud_ahead i nic lokalnie nowego → jedyny przypadek przywracania
    assert decide(local_changed=False, cloud_ahead=True, local_ahead=False) == "restore"
    assert decide(local_changed=False, cloud_ahead=False, local_ahead=False) == "skip"
    assert decide(local_changed=True, cloud_ahead=False, local_ahead=False) == "skip"
    assert decide(local_changed=True, cloud_ahead=True, local_ahead=False) == "conflict"


def test_local_ahead_alone_is_never_a_restore():
    # Kopia lokalna czeka na wysyłkę, chmura nie ma nic nowego: nie ma czego przywracać.
    assert decide(local_changed=False, cloud_ahead=False, local_ahead=True) == "skip"
    assert decide(local_changed=True, cloud_ahead=False, local_ahead=True) == "skip"


def test_divergence_in_both_directions_is_a_conflict():
    # F3: chmura ma kopię Decka z 10:00, lokalny katalog niewysłaną kopię Machine z 12:00.
    # Sama "różnica w podglądzie pobrania" nie może znaczyć "chmura jest nowsza".
    assert decide(local_changed=False, cloud_ahead=True, local_ahead=True) == "conflict"
    assert decide(local_changed=True, cloud_ahead=True, local_ahead=True) == "conflict"


def test_running_game_is_never_touched():
    for local_changed in (True, False, None):
        for cloud_ahead in (True, False):
            for local_ahead in (True, False):
                assert decide(local_changed, cloud_ahead, local_ahead,
                              running=True) == "blocked"


def test_unknown_local_state_is_a_conflict_never_a_skip():
    # None = podgląd Ludusavi zawiódł. Gdyby zamienić to na "brak zmian",
    # przywracanie nadpisałoby żywy zapis o nieznanej zawartości.
    assert decide(local_changed=None, cloud_ahead=True, local_ahead=False) == "conflict"
    assert decide(local_changed=None, cloud_ahead=False, local_ahead=False) == "conflict"


def test_running_game_wins_over_unknown_local_state():
    assert decide(local_changed=None, cloud_ahead=True, local_ahead=True,
                  running=True) == "blocked"


def test_game_without_any_saves_is_restored_not_conflicted():
    """Regresja G2: „brak zapisów lokalnie" (False) to nie „nie wiem" (None). Gra
    uruchamiana pierwszy raz na drugim urządzeniu musi dostać zapis z chmury —
    nie ma czego stracić, więc przywrócenie jest bezpieczne."""
    assert decide(local_changed=False, cloud_ahead=True, local_ahead=False) == "restore"
    assert decide(local_changed=None, cloud_ahead=True, local_ahead=False) == "conflict"
