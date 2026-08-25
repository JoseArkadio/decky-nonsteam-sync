"""Komunikaty dla człowieka: kod do tłumaczenia, parametry do podstawienia,
angielskie zdanie jako język przewodowy.

Ten moduł NIE zna polskiego. Tłumaczenia żyją w `plugin/src/i18n/*.json` pod kluczem
`err.<kod>`, a test `tests/test_i18n.py` pilnuje, żeby żaden kod nie został bez hasła.

Angielskie zdanie stąd jest jednocześnie tym, co widać w `tail` na pliku logu, i tym,
co interfejs pokazuje przy nieznanym kodzie — więc musi być PEŁNYM zdaniem, nie
skrótem. Dlatego `en.json` nie powtarza tych zdań: jedno źródło angielskiego.
"""
import string

# Kod → angielski szablon (`str.format`). Nazwy pól są jednocześnie nazwami
# parametrów w `params`, więc trafiają do tłumaczeń jako `{title}`, `{path}` itd.
CODES = {
    # --- warstwa RPC (main.py) ---
    "internal_error": "{method} failed with {type}: {detail}",
    "sync_already_running": "A sync is already running, so this call was rejected.",
    "ludusavi_config_problem": "Could not prepare the Ludusavi configuration: {detail}",
    "sync_summary": ("Restored: {restored} | conflicts: {conflicts} |"
                     " skipped: {skipped} | blocked: {blocked} | times: {timing}"),
    "sync_problems": "The sync reported {count} problem(s): {detail}",
    "scan_found": "Found {count} game(s) on the card.",
    "scan_untitled_title": ("Ludusavi did not return a title for: {names}"
                            " (command: {command})."),
    "path_repaired": ("{title}: the card changed its mount point, so the path"
                      " was repaired."),
    "no_such_file": "No such file: {path}.",
    "title_required": "Without a title there is no way to find this game's saves.",
    "disk_game_added": "{title}: added from disk ({path}).",
    "record_not_found": "{title_key} is not in the registry.",
    "record_not_found_refresh": ("{title_key} is not in the registry"
                                 " — refresh the game list."),
    "card_not_in_reader": "{title}: this game's card is not in the reader.",
    "file_not_on_card": ("This file is not on the game's card ({card_label}) —"
                         " pick a file from the game's folder on the card."),
    "exe_path_set": "{title}: pointed to game file {path}.",
    "game_registered": "Registered {title}.",
    "game_registered_neutralized": "Registered {title} (steam_appid.txt neutralized).",
    "field_not_editable": "{field} is not editable from the interface.",
    "archive_no_card": ("{title}: the card is not in the reader — there is"
                        " nowhere to put the save."),
    "archive_backup_failed": "{title}: could not put the save on the card{detail}.",
    "archive_done": "{title}: save and playtime moved to the card.",
    "steamworks_restore_failed": ("{title}: could not restore steam_appid.txt"
                                  " (card disconnected?)."),
    "game_forgotten": "No longer syncing {title}.",
    "game_forgotten_restored": "No longer syncing {title} (steam_appid.txt restored).",
    "retitle_running": ("{title} is running — its saves must not be moved out from"
                        " under it. Quit the game and try again."),
    "retitle_taken": ("{title} is already in the registry under this title. Two games"
                      " cannot share one title: the title decides which saves belong"
                      " to which game."),
    "retitle_card_busy": ("{title}: could not rename the save folder on the card"
                          " ({detail}), so the title was left alone."),
    "retitled": "{old} is now {title}.",
    "retitle_cloud_leftover": ("{old}: the copy under the old name stays in the cloud"
                               " — the card is the transport, so nothing was lost, but"
                               " you may want to clean it up."),
    "appid_override_set": "{title}: store description now comes from Steam app {appid}.",
    "appid_override_cleared": "{title}: store description comes from the Ludusavi database again.",
    "conflict_card_restored": "{title}: the save from the card was restored.",
    "conflict_card_failed": "{title}: restoring the save from the card failed.",
    "game_excluded": "{title}: excluded from sync.",
    "game_included": "{title}: syncing again.",
    "push_no_card": ("{title}: could not write to the card — not uploading to"
                     " the cloud{detail}."),
    "push_nowhere_no_card": ("{title}: nowhere to save — the card is not in the"
                             " reader and the cloud is not configured."),
    "push_nowhere_no_saves": "{title}: nowhere to save — the game has no saves.",
    "push_saved_card_only": "{title}: saved to the card (cloud not configured).",
    "push_deferred": ("{title}: the upload was deferred ({detail}) — the next"
                      " sync will finish it."),
    "push_sent": "{title}: sent.",
    "push_failed": "{title}: FAILED.",
    "push_sent_conflict": "{title}: sent (conflict).",
    "push_failed_conflict": "{title}: FAILED (conflict).",
    "push_upload_failed": "{title}: the upload did not go through{detail}.",
    "unknown_choice": "Unknown choice: {choice!r}.",
    "conflict_local_sent": "Conflict {title}: kept the local save → sent.",
    "conflict_local_failed": "Conflict {title}: kept the local save → FAILED.",
    "conflict_no_safety_backup": ("Conflict {title}: no safety backup — nothing"
                                  " was touched{detail}."),
    "conflict_safety_backup_failed": "The safety backup failed — nothing was changed.",
    "conflict_cloud_restored": "Conflict {title}: took the cloud save → restored.",
    "conflict_cloud_failed": "Conflict {title}: took the cloud save → FAILED.",
    "conflict_lock_error": "Conflict {title}: {detail}.",
    "conflict_resolve_failed": "Conflict {title}: resolving ({choice}) failed{detail}.",
    "invalid_seconds": "{seconds!r} is not a number.",
    "appid_not_ours": "Appid {appid} does not belong to any game from the card.",
    "playtime_card_missing": ("{title}: playtime saved locally, the card is"
                              " unavailable — the total is incomplete."),
    "log_tail_bad_count": "{count!r} is not a number of entries — showing the last 50.",
    "no_sgdb_key": ("No SteamGridDB key — artwork unavailable (set your own key"
                    " in the plugin settings)."),
    "sgdb_no_game": "{title}: SteamGridDB returned no game — {detail}.",
    "sgdb_no_assets": ("{title}: SGDB knows the game but returned not a single"
                       " image — {detail}."),
    "unknown_setting": "{key} is not a known setting.",
    "invalid_setting_value": "{value!r} is not a valid value for {key} (allowed: {allowed}).",

    # --- warstwa zapisów (saves.py) ---
    "sync_lock_held": "A save sync is already running (process {pid}, since {when}).",
    "sync_lock_takeover_failed": "Could not take over the sync lock: {detail}",
    "cloud_not_configured": "The cloud is not configured in Ludusavi.",
    "cloud_unreadable": "Could not read the cloud state.",
    "ludusavi_config_read_failed": "Could not read the Ludusavi configuration ({path}): {detail}",
    "ludusavi_config_write_failed": "Could not write the Ludusavi configuration ({path}): {detail}",
    "wrong_prefix": ("{title}: Ludusavi backed up a different prefix than the"
                     " plugin's shortcut ({prefix}) — the game's saves are not"
                     " what goes to the cloud."),

    # --- przebieg synchronizacji (sync.py) ---
    "sync_nothing_to_do": "Nothing to sync for {titles}: not in the registry.",
    "pending_push_still_failing": "{title}: the deferred upload still did not go through.",
    "ludusavi_unknown_title": ("{title}: the Ludusavi database does not know this title,"
                               " so its saves are not handled. Fix the title on the"
                               " game page."),
    "local_preview_failed": ("The local save preview did not complete, so every game is"
                             " marked as a conflict for manual resolution."),
    "card_saves_unreadable": ("Could not read the saves on the card ({path}), so those"
                              " games were skipped."),
    "safety_backup_failed": "{title}: the safety backup failed, so the game was skipped.",
    "restore_from_card_failed": "{title}: restoring from the card failed.",
    "card_write_failed": ("{title}: could not write to the card, so nothing goes to the"
                          " cloud."),
    "cloud_backup_failed": ("{title}: the cloud backup did not go through. The save is"
                           " on the card."),
    "cloud_state_unreadable": "Could not read the cloud state: {detail}",
    "record_without_key": "{title}: the record has no title_key, so it was skipped.",
    "conflict_without_safety_backup": "{title}: conflict with no safety backup.",
    "cloud_download_failed": "Downloading from the cloud failed, so nothing was restored.",
    "restore_failed": "{title}: restoring failed.",

    # --- metadane ze sklepu Steama (metadata.py; podmienia je równoległa sesja) ---
    "metadata_no_ludusavi_db": ("Cannot look up the store id: the Ludusavi database is"
                                " missing."),
    "metadata_store_unreachable": "Could not ask the Steam store: {detail}",

    # --- czas przejścia (hltb.py) ---
    "hltb_unreachable": "Could not ask HowLongToBeat: {detail}",
}


def fields(template: str) -> set:
    """Nazwy pól `{…}` w szablonie. Osobno, bo tego samego potrzebuje test
    pilnujący, że polskie hasło nie używa pola, którego kod nie przysyła."""
    return {name for _text, name, _spec, _conv
            in string.Formatter().parse(template) if name}


def msg(code: str, /, **params) -> dict:
    """Komunikat dla człowieka.

    NIGDY nie rzuca: wołamy to ze ścieżek, na których właśnie coś padło, a wyjątek
    w składaniu komunikatu o błędzie zabrałby informację o błędzie. Niezgodność
    parametrów kończy się WIDOCZNYM znacznikiem w zdaniu — reguła „awaria nie może
    wyglądać jak sukces" obowiązuje też tutaj, bo zdanie z cichą dziurą wygląda
    jak gotowy komunikat.
    
    Argument `code` jest pozycyjny tylko, żeby `params` nie mogły go przysłonić.
    Gdyby `params` zawierały klucz "code", kolizja rzucałaby `TypeError` PRZED
    wejściem do ciała funkcji, czyli poza zasięgiem `except`.
    """
    template = CODES.get(code)
    if template is None:
        return {"code": code, "params": params,
                "message": "unknown message code %r with %r" % (code, params)}
    try:
        message = template.format(**params)
    except Exception as exc:
        # `except Exception`, nie lista klas: całym zadaniem tej funkcji jest NIE
        # propagować. ZMIERZONE, że lista `(KeyError, IndexError, ValueError)` nie
        # wystarcza — `"{count:d}".format(count=None)` rzuca `TypeError`, a parametr
        # `count` mamy w `sync_problems`. Wyjątek lecący stąd zabrałby informację
        # o awarii, która to wywołanie w ogóle spowodowała.
        message = "%s [BAD PARAMS: wants %s, got %s (%s)]" % (
            template, sorted(fields(template)), sorted(params),
            type(exc).__name__)
    return {"code": code, "params": params, "message": message}
