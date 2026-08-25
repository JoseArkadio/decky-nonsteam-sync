import glob
import os

GAMES_DIR_NAME = "Games"


def default_media_roots() -> list:
    return ["/run/media", "/media"]


def find_cards(media_roots: list) -> list:
    """Karty z katalogiem Games, każda dokładnie raz.

    Deduplikacja po realpath jest tu obowiązkowa, nie kosmetyczna: w SteamOS
    /run/media/SD256 jest symlinkiem na /run/media/deck/SD256, więc oba wzorce globa
    łapią tę samą kartę. Bez tego każda gra trafia na listę dwukrotnie i użytkownik
    dostaje podwójne kafelki — problem, od którego zaczął się ten projekt.
    Zostaje prawdziwy punkt montowania, symlink przegrywa."""
    cards = {}
    for root in media_roots:
        # obsługujemy oba warianty montowania: /run/media/SD256 i /run/media/deck/SD256
        for pattern in (
            os.path.join(root, "*", GAMES_DIR_NAME),
            os.path.join(root, "*", "*", GAMES_DIR_NAME),
        ):
            for games_dir in glob.glob(pattern):
                if not os.path.isdir(games_dir):
                    continue
                mount = os.path.dirname(games_dir)
                real = os.path.realpath(mount)
                known = cards.get(real)
                if known and known["mount"] == real:
                    continue  # prawdziwy punkt montowania już mamy
                cards[real] = {
                    "label": os.path.basename(mount),
                    "mount": mount,
                    "games_dir": games_dir,
                }
    return sorted(cards.values(), key=lambda c: c["label"])
