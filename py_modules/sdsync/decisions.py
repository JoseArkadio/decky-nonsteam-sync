def decide(local_changed, cloud_ahead: bool, local_ahead: bool,
           running: bool = False) -> str:
    """Tabela decyzyjna F4 planu. Kolejność sprawdzeń jest wiążąca.

    `local_changed` jest trójstanem: True / False / None ("nie wiem", bo podgląd
    Ludusavi zawiódł). Niewiedza to konflikt — nie zamieniamy jej na "brak zmian",
    bo wtedy przywrócenie z chmury nadpisałoby nieznany żywy zapis.

    `cloud_ahead` i `local_ahead` to dwa kierunki tej samej różnicy. Sam
    `cloud_ahead` nie znaczy "chmura ma nowszy zapis": gdy lokalny katalog ma
    niewysłaną kopię, obie strony mają coś, czego druga nie ma — to rozjazd.
    """
    if running:
        return "blocked"
    if local_changed is None:
        return "conflict"
    if cloud_ahead and local_ahead:
        return "conflict"
    if cloud_ahead and local_changed:
        return "conflict"
    if cloud_ahead:
        return "restore"
    return "skip"
