import glob
import os

# Nazwy, które nigdy nie są plikiem gry. Gdy heurystyka wybierze zły plik,
# użytkownik nadpisuje wybór w UI — nie rozbudowujemy tej listy w nieskończoność.
EXE_BLACKLIST = (
    "unins", "setup", "install", "redist", "vcredist", "vc_redist", "dxsetup",
    "dxwebsetup", "dotnet", "crash", "handler", "touchup", "activation", "cleanup",
    "oalinst",
)
# Katalogi z bibliotekami towarzyszącymi. ZMIERZONE na karcie: dla „Invincible VS"
# automat wybrał `_Redist/oalinst.exe`, bo instalator OpenAL był NAJWIĘKSZYM plikiem
# .exe w całym drzewie, a filtr patrzył tylko na nazwę pliku. Prawdziwym sygnałem jest
# katalog — i on jest tani do sprawdzenia.
DIR_BLACKLIST = (
    "_redist", "redist", "redistributable", "commonredist", "_commonredist",
    "prerequisites", "directx", "dotnet", "vcredist", "_installer",
)


def pick_exe(game_dir: str):
    candidates = []
    for depth in ("*", "*/*", "*/*/*", "*/*/*/*"):
        for path in glob.glob(os.path.join(glob.escape(game_dir), depth)):
            if not path.lower().endswith(".exe") or not os.path.isfile(path):
                continue
            name = os.path.basename(path).lower()
            if any(bad in name for bad in EXE_BLACKLIST):
                continue
            czesci = os.path.relpath(path, game_dir).lower().split(os.sep)[:-1]
            if any(part in DIR_BLACKLIST for part in czesci):
                continue
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=os.path.getsize)


def scan_card(card: dict) -> list:
    games_dir = card["games_dir"]
    mount = card["mount"]
    out = []
    for entry in sorted(os.listdir(games_dir)):
        game_dir = os.path.join(games_dir, entry)
        if not os.path.isdir(game_dir):
            continue
        exe = pick_exe(game_dir)
        if not exe:
            continue
        appid_file = os.path.join(game_dir, "steam_appid.txt")
        out.append({
            "folder": entry,
            "exe_abs": exe,
            "exe_rel": os.path.relpath(exe, mount),
            "steam_appid_file": appid_file if os.path.isfile(appid_file) else None,
        })
    return out
