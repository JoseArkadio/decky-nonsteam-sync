import os
import time

from .decisions import decide
from .messages import msg
from .saves import backup_dir_name


def _card_label(record, path: str) -> str:
    """Klucz karty w `card_seen`. Etykieta z rejestru jest STABILNA, a ścieżka nie:
    ta sama karta bywa montowana raz jako /run/media/SD256, raz jako
    /run/media/deck/SD256 (u nas jedno jest symlinkiem na drugie), więc klucz po
    ścieżce zmieniałby się sam i wyglądał jak „karta ma nową kopię"."""
    label = (record.get("card_label") or "").strip()
    # <mount>/.sdsync/saves → nazwa punktu montowania, czyli to samo, co cards.py
    # daje jako etykietę
    return label or os.path.basename(os.path.dirname(os.path.dirname(path)))


def remember_card_copy(registry, record, path: str, when) -> bool:
    """Zapamiętuje tożsamość kopii, którą na tej karcie widzieliśmy — inaczej kolejny
    przebieg uzna WŁASNĄ kopię za nieznaną i przywróci ją bez powodu (a przy lokalnym
    postępie zgłosi rozjazd, którego nie ma). Wspólna dla przebiegu synchronizacji
    i dla wyjścia z gry: obie drogi piszą na kartę, więc obie muszą to odnotować."""
    if not when:
        return False
    seen = dict(record.get("card_seen") or {})
    seen[_card_label(record, path)] = when
    registry.set_fields(record["title_key"], card_seen=seen)
    return True


class SyncService:
    """Jedno przejście chmury na bibliotekę (albo na wskazane gry), decyzja per gra."""

    def __init__(self, registry, saves, is_running, on_stage=None, card_dir=None,
                 cloud_enabled=None):
        self.registry = registry
        self.saves = saves
        self.is_running = is_running
        # Katalog kopii na karcie danej gry — kart może być dowolnie wiele, więc pyta
        # się o to per rekord. Brak funkcji (albo pusta ścieżka) = zostaje sama chmura,
        # czyli dokładnie dawne zachowanie.
        self.card_dir = card_dir or (lambda record: "")
        # Świadomy wybór użytkownika „chmura + karta" / „tylko karta". To NIE to samo
        # co nieskonfigurowana chmura: tam mówimy o awarii, tu o decyzji — więc przy
        # „tylko karta" nie ma ani wywołania sieciowego, ani komunikatu o błędzie.
        self.cloud_enabled = cloud_enabled or (lambda: True)
        # ZMIERZONE: sam przebieg trwa 20–120 s i to czekanie na rclone, którego nie
        # da się skrócić. Skoro nie da się przyspieszyć, to przynajmniej niech widać,
        # na co czekamy — „Pracuję…" przez dwie minuty wygląda jak zawieszenie.
        self.on_stage = on_stage or (lambda name: None)
        # ile sekund zjadł każdy etap ostatniego przebiegu; OSOBNO od wyniku, bo
        # kształt wyniku jest kontraktem z frontendem i nie dokładamy do niego
        # pola, którego nikt tam nie czyta
        self.last_seconds = {}

    def _records(self, title_keys):
        # pusty tytuł = Ludusavi dostałby wywołanie bez filtra gry (robota na całej
        # bibliotece), więc taki rekord pomijamy
        records = [r for r in self.registry.all() if (r.get("title") or "").strip()]
        if title_keys is None:
            # automat pomija gry wykluczone przez użytkownika
            return [r for r in records if not r.get("excluded")]
        # wskazanie gry z ekranu jest świadomą decyzją — bije wykluczenie z automatu,
        # inaczej przycisk „Synchronizuj" przy wykluczonej grze nie robiłby nic
        wanted = set(title_keys)
        return [r for r in records if r.get("title_key") in wanted]

    def sync_all(self, title_keys=None) -> dict:
        """`title_keys=None` → cała biblioteka bez wykluczonych; lista kluczy →
        tylko te gry (także wykluczone)."""
        result = {"restored": [], "conflicts": [], "skipped": [],
                  "blocked": [], "errors": []}
        self.last_seconds = {}
        clock = _Clock(self.last_seconds, self.on_stage)
        records = self._records(title_keys)
        if title_keys is not None and not records:
            # cisza wyglądałaby jak "zsynchronizowane"; użytkownik kliknął
            # konkretną grę i ma prawo wiedzieć, że nic się nie stało
            result["errors"].append(
                msg("sync_nothing_to_do", titles=", ".join(title_keys)))
            return result
        if not records:
            return result

        # Odłożone wysyłki idą PRZED odczytem stanu chmury: inaczej podgląd zobaczyłby
        # lokalną kopię, której w chmurze jeszcze nie ma, i uznał to za rozjazd.
        pending = [r for r in records if r.get("pending_push")]
        if pending:
            with clock("zalegle_wysylki"):
                for record in pending:
                    if self.saves.backup(record["title"], cloud=True)["ok"]:
                        self.registry.set_fields(record["title_key"], pending_push=False)
                    else:
                        result["errors"].append(
                            msg("pending_push_still_failing", title=record["title"]))

        # Gry, o których Ludusavi już raz powiedział, że ich nie zna, w ogóle nie
        # wchodzą do wywołań: jedna taka nazwa wywala CAŁE polecenie i zamienia
        # wszystkie gry w konflikty (ZMIERZONE na Decku). Milczeć o nich nie wolno —
        # użytkownik musi wiedzieć, że TA gra nie ma obsługi zapisów.
        nieznane = [r for r in records if r.get("ludusavi_unknown")]
        for record in nieznane:
            result["errors"].append(
                msg("ludusavi_unknown_title", title=record["title"]))
        records = [r for r in records if not r.get("ludusavi_unknown")]
        if not records:
            return result

        # Podgląd lokalny raz na przebieg — czytają go OBIE fazy. Jeden podgląd na całą
        # listę zamiast jednego na grę: koszt to start procesu Ludusavi, nie sama praca,
        # więc N wywołań to N×kilka sekund w oknie, w którym użytkownik patrzy na
        # „Pracuję…".
        with clock("podglad_lokalny"):
            changed = self.saves.local_changed_many([r["title"] for r in records])
        # Ludusavi mógł właśnie odkryć nieznany tytuł (zejście na wywołania
        # pojedyncze). Zapamiętujemy to, żeby następny przebieg znów był jednym
        # wywołaniem, i wyłączamy tę grę z tego przebiegu.
        swiezo_nieznane = set(getattr(self.saves, "unknown_titles", None) or set())
        if swiezo_nieznane:
            for record in list(records):
                if record["title"] not in swiezo_nieznane:
                    continue
                self.registry.set_fields(record["title_key"], ludusavi_unknown=True)
                result["errors"].append(
                    msg("ludusavi_unknown_title", title=record["title"]))
                records.remove(record)
                changed.pop(record["title"], None)
            if not records:
                return result

        if changed and all(value is None for value in changed.values()):
            # Kierunek jest bezpieczny (niewiedza = konflikt), ale cisza zamienia to
            # w N konfliktow bez przyczyny. _stderr_hint dopisuje ogon Ludusavi tylko
            # wtedy, gdy errors jest niepuste.
            result["errors"].append(msg("local_preview_failed"))

        # FAZA KARTY — bez sieci, więc idzie pierwsza i w typowym dniu kończy robotę.
        served = self._card_phase(records, changed, result, clock)
        rest = [r for r in records if r["title"] not in served]
        if not rest:
            return result

        if not self.cloud_enabled():
            # użytkownik wybrał „tylko karta": te gry nie mają dziś skąd wziąć zapisu,
            # ale to nie awaria — cisza byłaby myląca, wpis „pominięto" jest prawdą
            result["skipped"].extend(r["title"] for r in rest)
            return result

        # FAZA CHMURY — tylko dla gier, których karta nie ma czym obsłużyć. Chmura jest
        # kopią zapasową, nie transportem: sięgamy do niej, gdy nie ma co dziedziczyć
        # z karty (pierwsze uruchomienie na nowym urządzeniu, karta po sformatowaniu).
        return self._cloud_phase(rest, changed, result, clock)

    def _card_phase(self, records, changed, result, clock) -> set:
        """Zwraca tytuły, których karta dotyczy — chmura nie ma prawa ich drugi raz
        rozstrzygać.

        Karta jest transportem zapisów, bo bez karty nie da się w tę grę zagrać: to
        czyni ją jedynym nośnikiem, który ZAWSZE ma najświeższy stan, i jedynym, który
        nie potrzebuje sieci.

        NIEZMIENNIK, na którym stoi cała konstrukcja: chmura nigdy nie dostaje stanu,
        którego nie ma karta. Wysyłka idzie wyłącznie PO udanej kopii na kartę. Gdyby
        chmura mogła wyprzedzić kartę, drugie urządzenie widziałoby „karta bez zmian",
        grałoby od starszego stanu i nie miałoby jak dowiedzieć się o nowszym.
        """
        by_card = {}
        for record in records:
            path = (self.card_dir(record) or "").strip()
            if path:
                by_card.setdefault(path, []).append(record)
        if not by_card:
            return set()

        # Jedno wywołanie na KARTĘ, nie na grę (start procesu Ludusavi kosztuje więcej
        # niż sama praca). None w miejscu całego słownika = nie wiem, co na karcie.
        with clock("stan_karty"):
            state = {path: self.saves.card_when_many([r["title"] for r in group], path)
                     for path, group in by_card.items()}

        served, to_restore, to_write, whens = set(), [], [], {}
        for path, group in by_card.items():
            on_card = state.get(path)
            if on_card is None:
                # „Nie wiem, co na karcie" to NIE „karta jest pusta". Przepuszczenie
                # tych gier do fazy chmury pozwoliłoby chmurze nadpisać nowszy stan
                # z karty, więc nie robimy nic i mówimy o tym wprost.
                served.update(r["title"] for r in group)
                result["errors"].append(msg("card_saves_unreadable", path=path))
                continue
            for record in group:
                title = record["title"]
                # Gra z dysku: nośnik lokalny NIE JEŹDZI między urządzeniami, więc nie
                # jest transportem i nie może rozstrzygać przywracania — to robi chmura
                # (jedyna droga między urządzeniami dla takiej gry). Kopię lokalną
                # nadal odkładamy: to ona daje zapis poza prefiksem Protona i ona jest
                # warunkiem wysyłki do chmury.
                dysk = record.get("carrier") == "disk"
                card_when = on_card.get(title)
                if card_when is None and not changed.get(title):
                    continue  # karta nie ma czego dziedziczyć → niech spróbuje chmura
                if not dysk:
                    served.add(title)
                whens[title] = card_when
                label = _card_label(record, path)
                seen = (record.get("card_seen") or {}).get(label)
                verdict = decide(
                    # karta „przed nami" = jej kopia ma inną tożsamość niż ta, którą
                    # widzieliśmy ostatnio; `local_ahead` nie ma tu osobnego pomiaru,
                    # bo „mamy coś swojego" nosi już local_changed
                    local_changed=changed.get(title),
                    cloud_ahead=bool(card_when) and card_when != seen,
                    local_ahead=False,
                    running=self.is_running(title),
                )
                # dla dysku wyrok „przywróć z nośnika" nie ma sensu (patrz wyżej),
                # a zapisanie go w wyniku wprowadzałoby w błąd — chmura powie swoje
                if not (dysk and verdict == "restore"):
                    self._note(record, verdict, result)
                if verdict == "restore" and not dysk:
                    to_restore.append((record, path, label))
                elif verdict == "skip" and changed.get(title):
                    # graliśmy, a karta o tym nie wie (np. brak sieci w pociągu albo
                    # wyjęta karta przy wyjściu z gry) — trzeba to na nią wywieźć
                    to_write.append((record, path, label))

        if to_restore:
            with clock("kopie_bezpieczenstwa"):
                safety = self.saves.safety_backup_many(
                    [r["title"] for r, _, _ in to_restore])
            with clock("przywracanie_z_karty"):
                for record, path, label in to_restore:
                    title = record["title"]
                    if safety.get(title) is False:
                        result["errors"].append(
                            msg("safety_backup_failed", title=title))
                        continue
                    # gra mogła wystartować w czasie wywołań Ludusavi
                    if self.is_running(title):
                        result["blocked"].append(title)
                        continue
                    if not self.saves.card_restore(title, path):
                        result["errors"].append(
                            msg("restore_from_card_failed", title=title))
                        continue
                    result["restored"].append(title)
                    self._remember_card(record, path, whens.get(title))
                    # własny katalog kopii musi poznać przywrócony stan, inaczej
                    # następny przebieg uzna go za „nasz nowy postęp" i będzie go
                    # wywoził na kartę bez końca
                    self.saves.backup(title, cloud=False)

        if to_write:
            self._carry_to_card(to_write, result, clock)
        return served

    def _carry_to_card(self, to_write, result, clock) -> None:
        """Wywozi nasz zapis na kartę, a POTEM (i tylko wtedy) do chmury."""
        by_path = {}
        for record, path, label in to_write:
            by_path.setdefault(path, []).append((record, label))
        for path, group in by_path.items():
            titles = [r["title"] for r, _ in group]
            with clock("kopia_na_karte"):
                written = self.saves.card_backup_many(titles, path)
            fresh = self.saves.card_when_many(titles, path) or {}
            for record, label in group:
                title = record["title"]
                if written.get(title) is False:
                    # chmura NIE MOŻE wyprzedzić karty, więc tu przebieg dla tej gry
                    # się kończy — i musi to być widoczne
                    result["errors"].append(
                        msg("card_write_failed", title=title))
                    continue
                self._remember_card(record, path, fresh.get(title))
                if not self.cloud_enabled():
                    self.registry.set_fields(record["title_key"], pending_push=False,
                                             last_push_ts=time.time(),
                                             last_backup_ts=time.time())
                    continue
                with clock("kopia_do_chmury"):
                    outcome = self.saves.backup(title, cloud=True)
                if outcome["ok"]:
                    self.registry.set_fields(record["title_key"], pending_push=False,
                                             last_push_ts=time.time(),
                                             last_backup_ts=time.time())
                else:
                    # zapis jest już na karcie, więc nic nie ginie — chmura dogoni
                    self.registry.set_fields(record["title_key"], pending_push=True)
                    result["errors"].append(
                        msg("cloud_backup_failed", title=title))

    def _remember_card(self, record, path: str, when) -> None:
        if remember_card_copy(self.registry, record, path, when):
            self.registry.set_fields(record["title_key"], last_backup_ts=time.time())

    def _note(self, record, verdict: str, result) -> None:
        """Zapis wyroku w wyniku i w rejestrze — wspólny dla obu faz."""
        key = record["title_key"]
        title = record["title"]
        if verdict == "conflict":
            self.registry.set_fields(key, conflict=True)
            result["conflicts"].append(title)
        elif verdict == "blocked":
            result["blocked"].append(title)
        elif verdict == "skip":
            result["skipped"].append(title)
        # Rozjazd rozstrzygnięty → flaga nie może zostać w rejestrze (i w
        # interfejsie) na zawsze. "blocked" jej nie czyści: gra chodzi, więc
        # stanu nie zmierzyliśmy do końca.
        if verdict in ("restore", "skip") and record.get("conflict"):
            self.registry.set_fields(key, conflict=False)

    def _cloud_phase(self, records, changed, result, clock) -> dict:
        # Stan chmury czytamy PRZED pobraniem — pobranie jest tą samą operacją,
        # po nim podgląd nie pokazuje już żadnych różnic (P1 planu). Czytamy oba
        # kierunki: sama różnica "chmura ma, my nie" nie znaczy "chmura nowsza" (F3).
        with clock("stan_chmury"):
            ok, cloud_ahead, local_ahead = self.saves.cloud_state()
        if not ok:
            # powód pochodzi z warstwy zapisów: nieskonfigurowana chmura to inna
            # awaria niż nieczytelny stan, a użytkownik musi wiedzieć która.
            # `cloud_error` jest już gotowym `msg()` z saves.py — pomost, który tu
            # kiedyś owijał surowy napis, zniknął razem z etapem B.
            result["errors"].append(
                getattr(self.saves, "cloud_error", None)
                or msg("cloud_state_unreadable", detail="cloud state could not be read"))
            return result

        candidates, conflicts = [], []
        for record in records:
            title = record["title"]
            key = record.get("title_key")
            if not key:
                result["errors"].append(msg("record_without_key", title=title))
                continue
            verdict = decide(
                # brak tytułu w odpowiedzi zbiorczej to „nie wiem", nie „brak zmian":
                # wywołanie mogło paść zanim doszło do tej gry
                local_changed=changed.get(title),
                # chmura zna grę po NAZWIE KATALOGU kopii, a ta różni się od tytułu,
                # gdy tytuł ma znak niedozwolony w nazwie pliku (zmierzone: dwukropek)
                cloud_ahead=backup_dir_name(title) in cloud_ahead,
                local_ahead=backup_dir_name(title) in local_ahead,
                running=self.is_running(title),
            )
            if verdict == "restore":
                candidates.append(title)
            elif verdict == "conflict":
                conflicts.append(title)
            self._note(record, verdict, result)

        # Kopie bezpieczeństwa PRZED cloud_download(): pobranie nadpisuje lokalny
        # katalog kopii, więc po nim nie ma zapasu ani do przywracania, ani do
        # ręcznego rozstrzygnięcia konfliktu. Konflikty i kandydaci idą jednym
        # wywołaniem — to ten sam `backup --path`, tylko z dłuższą listą gier.
        if conflicts or candidates:
            with clock("kopie_bezpieczenstwa"):
                safety = self.saves.safety_backup_many(conflicts + candidates)
        else:
            safety = {}

        for title in conflicts:
            if safety.get(title) is False:
                result["errors"].append(
                    msg("conflict_without_safety_backup", title=title))

        to_restore = []
        for title in candidates:
            # kontrakt jest trójstanowy: True = skopiowano, None = ta gra nie ma tu
            # żadnych zapisów (nie ma czego stracić, więc przywracamy — to jest
            # pierwsze uruchomienie gry na drugim urządzeniu), False = awaria kopii
            # i tylko ona blokuje.
            if safety.get(title) is not False:
                to_restore.append(title)
            else:
                result["errors"].append(
                    msg("safety_backup_failed", title=title))

        if not to_restore:
            return result

        # Pobranie z filtrem gier: nadpisuje lokalny katalog kopii, więc nie może
        # tknąć gry, która ma tylko niewysłaną kopię lokalną (wyrok "skip").
        with clock("pobranie_z_chmury"):
            downloaded = self.saves.cloud_download(to_restore)
        if not downloaded:
            # katalog kopii jest w nieznanym stanie — przywracanie z niego mogłoby
            # nadpisać żywy zapis starszymi danymi
            result["errors"].append(msg("cloud_download_failed"))
            return result

        keys = {r["title"]: r.get("title_key") for r in records}
        with clock("przywracanie"):
            for title in to_restore:
                # między pomiarem a tym momentem minęło kilkanaście sekund wywołań
                # Ludusavi — użytkownik mógł uruchomić grę
                if self.is_running(title):
                    result["blocked"].append(title)
                    continue
                if self.saves.restore(title):
                    result["restored"].append(title)
                    # Udane przywrócenie DOWODZI, że kopia w chmurze istnieje.
                    # Znacznik ustawiała dotąd tylko wysyłka, więc urządzenie, które
                    # właśnie pobrało zapis, pokazywało na ekranie gry „brak kopii
                    # zapisów" — zaraz po notyfikacji o udanej synchronizacji
                    # (zgłoszone z urządzenia po przełożeniu karty).
                    self.registry.set_fields(keys[title], last_backup_ts=time.time())
                else:
                    result["errors"].append(msg("restore_failed", title=title))
        return result


class _Clock:
    """Ile sekund zjadł każdy etap. Bez tego „synchronizacja trwa długo" jest
    przekonaniem, a nie pomiarem, i nie wiadomo, czy skracać wywołania Ludusavi,
    czy czekanie na rclone."""

    def __init__(self, into: dict, on_stage=None):
        self.into = into
        self.on_stage = on_stage or (lambda name: None)

    def __call__(self, name: str):
        return _Span(self.into, name, self.on_stage)


class _Span:
    def __init__(self, into: dict, name: str, on_stage):
        self.into, self.name, self.on_stage = into, name, on_stage

    def __enter__(self):
        self.on_stage(self.name)
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.into[self.name] = round(time.monotonic() - self.started, 1)
        return False
