import os

APPID_FILE = "steam_appid.txt"
BACKUP_FILE = "steam_appid.txt.sdsync-bak"


def _paths(game_dir: str):
    return os.path.join(game_dir, APPID_FILE), os.path.join(game_dir, BACKUP_FILE)


def state(game_dir: str) -> str:
    active, backup = _paths(game_dir)
    if os.path.isfile(active):
        return "active"
    if os.path.isfile(backup):
        return "neutralized"
    return "absent"


def neutralize(game_dir: str) -> dict:
    """Wyłącza identyfikację Steamworks, żeby Steam nie liczył sesji sklepowej grze.
    Odwracalne: plik jest tylko przemianowany."""
    active, backup = _paths(game_dir)
    if not os.path.isfile(active):
        return {"changed": False, "appid": None, "backup": backup if os.path.isfile(backup) else None}
    with open(active, encoding="utf-8", errors="replace") as fh:
        appid = fh.read().strip()
    os.replace(active, backup)
    return {"changed": True, "appid": appid, "backup": backup}


def restore(game_dir: str) -> bool:
    active, backup = _paths(game_dir)
    if not os.path.isfile(backup):
        return False
    os.replace(backup, active)
    return True
