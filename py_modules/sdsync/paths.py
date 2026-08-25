import os

DEFAULT_SETTINGS_DIR = os.path.expanduser("~/homebrew/settings/NonSteam Sync")


def games_file(settings_dir: str = DEFAULT_SETTINGS_DIR) -> str:
    return os.path.join(settings_dir, "games.json")


def safety_dir(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "safety")


# Katalog wtyczki na karcie. Jedno miejsce prawdy: czas gry (playtime.py) i zapisy
# muszą trafiać do TEGO SAMEGO katalogu, inaczej na karcie rosną dwa nasze drzewa.
CARD_DIR_NAME = ".sdsync"


def card_saves_dir(mount: str) -> str:
    """Katalog kopii Ludusavi na karcie. Kart może być dowolnie wiele i każda nosi
    zapisy WŁASNYCH gier — nie ma tu nic globalnego dla urządzenia."""
    return os.path.join(mount, CARD_DIR_NAME, "saves")


def disk_carrier_dir(runtime_dir: str) -> str:
    """Nośnik gier zainstalowanych na dysku konsoli — układ w środku jak na karcie.

    Dzięki temu kopia, tożsamość `card_seen`, tabela decyzyjna i kopie bezpieczeństwa
    działają bez zmian; różni się tylko odpowiedź na pytanie „gdzie jest nośnik".
    Efekt uboczny, który jest tu wartością: zapis gry z dysku ma kopię POZA prefiksem
    Protona — czyli to, czego zabrakło, gdy czyszczenie danych Protona zabrało
    użytkownikowi prefiksy razem z zapisami.
    """
    return os.path.join(runtime_dir, "dysk")
