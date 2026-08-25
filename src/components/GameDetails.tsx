import { DialogButton, Focusable, Navigation, ToggleField } from "@decky/ui";
import {
  FaExclamationTriangle,
  FaEye,
  FaEyeSlash,
  FaFolderOpen,
  FaAlignLeft,
  FaImages,
  FaSearch,
  FaSteam,
  FaSync,
  FaTrashAlt,
} from "react-icons/fa";
import { FileSelectionType, openFilePicker } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import {
  GameRecord,
  archiveToCard,
  forgetGame,
  resolveConflict,
  setExcluded,
  setExe,
  logAdd,
  retitle,
  setStoreAppid,
  storeSearch,
  syncGame,
} from "../backend";
import { openPicker } from "./TitlePicker";
import { gameRoute, selectGame } from "../selection";
import { steamLocale } from "../steam";
import { fetchAndApplyArtwork, useCover } from "../artwork";
import { useGameMeta } from "../metadata";
import { GameFacts } from "./GameFacts";
import { GameHero } from "./GameHero";
import { GameMeta } from "./GameMeta";
import { ConflictChoice } from "./ConflictChoice";
import { nameOf, removeShortcut, setHidden, setShortcutName, setShortcutPath } from "../steam";
import { foreignTiles } from "../status";
import { fromBackend, t } from "../i18n";

interface Props {
  game: GameRecord;
  onChanged: () => void;
  /** true, gdy użytkownik przyszedł z ekranu gry przez „Zobacz szczegóły" —
   *  wtedy zaznaczenie ląduje na pierwszej akcji, a nie na spisie po lewej. */
  autofocusAction: boolean;
}

/** Przyciski akcji: KAŻDY tej samej szerokości, w równych rzędach.
 *  ZGŁOSZONE z urządzenia: „nie są na pewno szerokość te funkcyjne na dole" —
 *  „Usuń mimo to" nie miał w ogóle ustawionej szerokości i wystawał, a reszta stała
 *  w słupku sztywnych 260 px niezależnie od tego, ile miejsca ma sekcja. Siatka
 *  `auto-fit` sama liczy, ile kolumn się mieści, i rozciąga je równo — więc ten sam
 *  kod wygląda dobrze i na Decku, i na szerszym ekranie Machine. */
const SIATKA = {
  display: "grid",
  // DWIE kolumny na stałe, nie `auto-fit`. ZMIERZONE na Decku: przy `auto-fit` przyciski
  // akcji wyszły po 293 px, ale „Przestań obsługiwać" stoi w swojej strefie SAM, więc
  // jako jedyny element wiersza rozciągnął się na 595 px — czyli dokładnie ta
  // nierówność, którą użytkownik zgłosił („nie są na pewno szerokość te funkcyjne na
  // dole"). Stała liczba kolumn daje tę samą szerokość niezależnie od tego, ile
  // przycisków akurat widać.
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: "10px",
} as const;

/** Ikonka + napis w jednym rzędzie. `minWidth: 0` jest konieczne: element siatki ma
 *  domyślnie `min-width: auto`, więc długi napis rozpychałby kolumnę zamiast się zawinąć. */
const PRZYCISK = {
  width: "100%",
  minWidth: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "8px",
} as const;

/** Wspólne pudełko strefy. Prawa sekcja miała czytać się jak ekran gry, nie jak
 *  formularz: strefy oddziela tło i ramka, a nie sam odstęp. */
/** Strefa jako MROŻONE SZKŁO nad okładką gry.
 *
 *  ZGŁOSZONE z urządzenia: „bardziej taki glass frost na tym tle, przyciemnione, ale
 *  troszkę bardziej przezroczyste i z blurem pod spodem". Warunek, bez którego to nie
 *  działa i wygląda jak „blur nie działa": `backdrop-filter` rozmywa TO, CO LEŻY POD
 *  SPODEM, więc pod strefami musi naprawdę coś być — stąd okładka jako tło całej
 *  sekcji niżej. Sama strefa bez tła pod spodem rozmyłaby pustkę.
 *
 *  Wartości zmierzone na CEF-ie Decka przez sesję od metadanych (`GamePageSection`
 *  ma ten sam efekt): `backdrop-filter` działa bez prefiksu `-webkit-`. Tło jest tu
 *  RZADSZE niż tam (0,58 wobec 0,82), bo tamta karta leży na ekranie gry Steama i musi
 *  się obronić na cudzym tle, a ta leży na własnym, już przyciemnionym. */
const KARTA = {
  background: "rgba(10, 15, 21, 0.52)",
  backdropFilter: "blur(12px)",
  border: "1px solid rgba(255, 255, 255, 0.08)",
  borderRadius: "8px",
  padding: "12px 14px",
  boxShadow: "0 4px 14px rgba(0, 0, 0, 0.45)",
} as const;

/** Zapas, o jaki tło wychodzi POZA kolumnę treści, żeby dojść do krawędzi panelu.
 *
 *  Nie jest to liczba pikseli i to jest cała poprawka. Poprzednia wersja miała wpisane
 *  −28 px na boki i −24 px w górę, bo tyle ZMIERZONO w jednym oknie Decka (985×616) —
 *  ZGŁOSZONE z urządzenia: „nie dochodzi do wszystkich krawędzi", bo na Steam Machine
 *  (1500×844) do lewej krawędzi jest 60 px, a do góry 64 px.
 *
 *  Druga wersja te odległości MIERZYŁA (pierwszy przodek z `overflow-y` innym niż
 *  `visible` wyznacza krawędzie) i była o 3 px za krótka na Machine: `useLayoutEffect`
 *  biegnie, zanim panel dojedzie na miejsce, a `ResizeObserver` widzi zmiany ROZMIARU,
 *  nie POŁOŻENIA — więc poprawki nigdy nie dostawał. Pomiar w tym miejscu jest po prostu
 *  zależny od chwili i nie ma jak go zrobić dobrze bez pętli po klatkach.
 *
 *  Więc nie mierzymy. ZMIERZONE na OBU urządzeniach: kontener przewijania ma
 *  `overflow-x: hidden`, a `scrollWidth === clientWidth` — czyli nadmiar w bok jest
 *  przycinany i NIE wydłuża przewijania. Nadmiar w górę też nie: nad początkiem treści
 *  nie da się przewinąć. Zapas w jednostkach okna (8vw = 79 px na Decku, 120 px na
 *  Machine; 12vh = 74 px i 101 px) jest wszędzie większy niż prawdziwy margines,
 *  a przycięciem zajmuje się Steam.
 *
 *  `minHeight` jest siatką bezpieczeństwa na grę z bardzo krótką treścią: w praktyce
 *  treść i tak jest wyższa niż panel (ZMIERZONE: 809 px wobec 616 na Decku, 891 wobec
 *  844 na Machine), więc ta reguła nigdy nie wiąże. */
const TLO_ZAPAS = { bok: "8vw", gora: "12vh", dol: "112vh" } as const;

/** Przycisk, który został w wierszu SAM, ma zająć całą szerokość.
 *
 *  ZGŁOSZONE: „»Pokaż ukryte« jest sam w linii, może lepiej zrobić go na pełną
 *  szerokość". To ODWRÓCENIE wcześniejszej decyzji i wpisuję to wprost: siatka miała
 *  stałe dwie kolumny właśnie po to, żeby samotny przycisk się NIE rozciągał (poprzednie
 *  zgłoszenie brzmiało „nie są na pewno szerokość te funkcyjne na dole"). Różnica jest
 *  w tym, CO wtedy sąsiadowało: przy `auto-fit` samotny przycisk stawał się dwa razy
 *  szerszy od przycisków w SĄSIEDNIEJ strefie i to było widać jako nierówność. Dziś
 *  każda strefa jest osobną siatką pod własnym nagłówkiem, więc pełna szerokość czyta
 *  się jako domknięcie wiersza, a nie jako przycisk-olbrzym obok normalnych.
 *
 *  Liczymy to w kodzie, a nie w CSS, bo CSS nie zna liczby dzieci — a `auto-fit`
 *  rozciągnąłby też wiersz DWÓCH przycisków przy wąskim panelu. */
const samotny = (indeks: number, ile: number) =>
  ile % 2 === 1 && indeks === ile - 1 ? { gridColumn: "1 / -1" } : undefined;

/** Strefa nieodwracalna ma inny kolor, nie tylko inne miejsce. ZGŁOSZONE: „mógłby być
 *  czerwony jako, że usuwa grę". Sam kolor informacją nie jest (napis i kreska nad
 *  strefą zostają), ale różnica jest widoczna kątem oka przed kliknięciem.
 *
 *  REGUŁA CSS, nie styl inline, i to jest konieczne. ZGŁOSZONE: „po najechaniu tekst
 *  robi się szary i brzydko wygląda na czerwonym". ZMIERZONE, dlaczego: Steam ma dla
 *  tego wariantu przycisku własną regułę zaznaczenia
 *  `button.cXzBZ….DialogButton.gpfocus => color: rgb(35,38,46); background: white`.
 *  Styl inline bije ją TYLKO w tym, co sam ustawia — tło zostawało czerwone, a kolor
 *  tekstu Steam zmieniał na ciemny, bo zakładał, że pod spodem jest jego białe tło.
 *  Stanu `:hover` i zaznaczenia nie da się wyrazić stylem inline, więc idzie reguła.
 *
 *  Selektory celowo BEZ `.gpfocus`: to nazwa z wnętrza Steama, a `:focus` wystarcza —
 *  ZMIERZONE, że nawigacja pada woła `.focus()` na przycisku (sprawdzone odczytem
 *  `document.activeElement` po strzałce). Dzięki temu ta reguła nie jest piątym miejscem
 *  zależnym od nieudokumentowanego Steama. `!important` jest konieczne, bo reguły
 *  Steama są bardziej szczegółowe od naszej klasy. */
const NIEODWRACALNY_CSS = `
.sdsync-danger.DialogButton {
  background: rgba(120, 32, 32, 0.55) !important;
  border: 1px solid rgba(255, 107, 107, 0.45) !important;
  color: #ffe9e9 !important;
}
.sdsync-danger.DialogButton:hover,
.sdsync-danger.DialogButton:focus,
.sdsync-danger.DialogButton:active {
  background: rgba(176, 46, 46, 0.92) !important;
  border-color: rgba(255, 160, 160, 0.85) !important;
  color: #fff !important;
}`;

/** Nagłówek grupy w strefie akcji. Zwykły `div`, nie `Focusable`: cokolwiek tu
 *  przyjmowałoby zaznaczenie, stałoby się przystankiem między przyciskami. */
const GRUPA = {
  fontSize: "0.75em",
  opacity: 0.55,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginTop: "4px",
} as const;

export function GameDetails({ game, onChanged, autofocusAction }: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirmForget, setConfirmForget] = useState(false);
  // Odblokowuje się DOPIERO po nieudanym odłożeniu zapisu na kartę. Nie ma go
  // od początku świadomie: „usuń mimo to" ma być wyjściem awaryjnym, nie
  // równorzędną opcją obok bezpiecznej.
  const [forceReady, setForceReady] = useState(false);
  const first = useRef<HTMLDivElement | null>(null);
  const duplicates = foreignTiles(game);
  // JEDNO wyszukanie grafiki na całą sekcję: ten sam kandydat idzie na baner i na tło.
  const art = useCover(game.appid);
  // Hak metadanych mieszka TUTAJ, nie w GameMeta: czyta go i karta informacji, i przycisk
  // „Odśwież opis" w siatce akcji. Dwa wywołania haka znaczyłyby dwa zapytania do sklepu
  // na jedno wejście na grę.
  const { meta, problem: metaProblem, busy: metaBusy, refresh: refreshMeta } = useGameMeta(
    game.title_key,
  );

  useEffect(() => {
    setProblem(null);
    setNote(null);
    setConfirmForget(false);
    if (autofocusAction) (first.current as any)?.focus?.();
  }, [game.title_key, autofocusAction]);

  /** `action` zwraca komunikat błędu albo null. Nieudana operacja NIGDY nie kończy
   *  się cicho — zostaje na ekranie do następnej akcji. */
  const run = (label: string, action: () => Promise<string | null>) => async () => {
    setBusy(true);
    setProblem(null);
    try {
      const failure = await action();
      if (failure) setProblem(`${label}: ${failure}`);
    } catch (err) {
      setProblem(`${label}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  /** Rozstrzygnięcie rozjazdu: TRZY źródła, nie dwa (patrz ConflictChoice).
   *  Wołane od razu, a nie zwracane jako obsługa — przycisk siedzi w komponencie
   *  niżej i to on decyduje, które źródło wskazał człowiek. */
  const resolve = (choice: "local" | "card" | "cloud") =>
    void run(
      choice === "local"
        ? t("ui.label.push_local")
        : choice === "card"
          ? t("ui.label.take_card")
          : t("ui.label.pull_cloud"),
      async () => {
        const result = await resolveConflict(game.title_key, choice);
        return result.ok ? null : fromBackend(result.error) || t("ui.action_failed");
      },
    )();

  const sync = run(t("ui.label.sync_game"), async () => {
    const result = await syncGame(game.title_key);
    // pusty wynik BEZ błędu znaczy "nie było czego robić" — to prawda, nie awaria
    if (result.errors?.length) return result.errors.map(fromBackend).join("; ");
    setNote(
      result.restored?.length
        ? t("ui.sync_note.restored")
        : result.conflicts?.length
          ? t("ui.sync_note.conflict")
          : result.blocked?.length
            ? t("ui.sync_note.blocked")
            : t("ui.sync_note.unchanged"),
    );
    return null;
  });

  const forget = run(t("ui.label.stop_managing"), async () => {
    // Zapis MUSI wyjechać na kartę PRZED zdjęciem kafelka: Ludusavi rozwiązuje prefiks
    // gry non-Steam przez `shortcuts.vdf`, więc po zniknięciu kafelka nie miałby czego
    // szukać, a zapis zostałby tylko w prefiksie — czyli poza wszelkim obiegiem.
    // Nieudane odłożenie zatrzymuje całą operację: kafelek da się zdjąć później,
    // straconego zapisu nie da się odzyskać.
    const archived = await archiveToCard(game.title_key);
    if (!archived.ok) {
      setForceReady(true); // dopiero teraz wolno pokazać wyjście awaryjne
      return fromBackend(archived.error) || t("ui.archive_failed");
    }
    // kafelek bez wpisu w rejestrze to sierota, którą kolejny skan zdubluje —
    // usuwamy WYŁĄCZNIE kafelek, który sami stworzyliśmy
    if (game.appid && nameOf(game.appid) !== null) removeShortcut(game.appid);
    const result = await forgetGame(game.title_key);
    if (!result.ok) return result.error ? fromBackend(result.error) : t("ui.registry_release_failed");
    setNote(
      archived.had_saves
        ? t("ui.forget_note.had_saves")
        : t("ui.forget_note.no_saves"),
    );
    return null;
  });

  /** Ręczne wskazanie pliku gry. Automat (`scan.pick_exe`) wybiera po heurystyce
   *  i na jakimś wydaniu zawsze się pomyli — ZGŁOSZONE: „Invincible VS" dostał nie
   *  ten plik. Podgląd startuje w folderze gry na karcie, żeby nie trzeba było
   *  klikać przez pół systemu plików. */
  const pickExe = run(t("ui.label.pick_exe"), async () => {
    // ZGŁOSZONE z urządzenia: wybierak otwierał się PUSTY — bez plików i bez folderów.
    // Startował w folderze gry, a przy wyjętej karcie taka ścieżka nie istnieje.
    // Backend i tak odrzuca plik spoza karty, więc bez karty nie ma czego wskazywać
    // i lepiej powiedzieć to wprost niż pokazać pustą listę.
    if (!game.available) {
      return t("ui.pick_exe_no_card");
    }
    const start = game.exe_abs.substring(0, game.exe_abs.lastIndexOf("/"));
    // `allowAllFiles` zostawia w wybieraku przełącznik na wypadek wydania, w którym
    // plik gry nie ma rozszerzenia .exe
    const wybrane = await openFilePicker(FileSelectionType.FILE, start, true, true,
                                         undefined, ["exe"], false, true);
    const sciezka = wybrane?.realpath || wybrane?.path;
    if (!sciezka) return null; // anulowane — to nie błąd
    const out = await setExe(game.title_key, sciezka);
    if (!out.ok || !out.exe_abs) return fromBackend(out.error) || t("ui.exe_save_failed");
    // kafelek trzyma ścieżkę na sztywno, więc bez tego Steam dalej uruchamiałby stary plik
    if (out.appid) {
      setShortcutPath(out.appid, out.exe_abs, out.exe_abs.substring(0, out.exe_abs.lastIndexOf("/")));
    }
    setNote(t("ui.exe_set_note", { path: String(out.exe_rel) }));
    return null;
  });

  /** Zdjęcie kafelka BEZ odkładania zapisu — wyjście awaryjne, gdy karty nie ma
   *  albo kopia nie chce się udać. Cena jest wypisana wprost i wpisana do logu
   *  zdarzeń: zapis zostaje w prefiksie TEGO urządzenia i nie pojedzie nigdzie
   *  dalej. Rejestr zwalniamy na końcu, żeby przy awarii w połowie gra dalej była
   *  nasza (kafelek bez wpisu w rejestrze zdubluje najbliższy skan). */
  const forgetForce = run(t("ui.label.forget_force"), async () => {
    await logAdd(
      "error",
      t("ui.forget_force_log", { title: game.title }),
    ).catch(() => undefined);
    if (game.appid && nameOf(game.appid) !== null) removeShortcut(game.appid);
    const result = await forgetGame(game.title_key);
    if (!result.ok) return result.error ? fromBackend(result.error) : t("ui.registry_release_failed");
    setNote(t("ui.forget_force_note"));
    return null;
  });

  /** Dociągnięcie grafik dla gry JUŻ dodanej. Skan robi to tylko dla nowych gier, więc
   *  klucz SteamGridDB wpisany po fakcie nie miał jak niczego zmienić — a wyglądało to
   *  dokładnie jak „grafiki nie działają mimo klucza". */
  const artwork = run(t("ui.label.fetch_artwork"), async () => {
    if (!game.appid) return t("ui.artwork_no_tile");
    const problems = await fetchAndApplyArtwork(game.title_key, game.title, game.appid);
    if (problems.length) return problems.join("; ");
    setNote(t("ui.artwork_done_note"));
    return null;
  });

  /** Zmiana tytułu gry, czyli jej TOŻSAMOŚCI.
   *
   *  ZGŁOSZONE: „nie mam z poziomu już wybranej gry wstecznie wyszukać, jakiej gry
   *  dotyczy". Do tej pory jedynym sposobem na poprawienie źle wskazanego tytułu było
   *  zdjęcie kafelka i dodanie gry od nowa — a to gubi prefiks Protona razem z postępem.
   *
   *  Nazwę kafelka ustawiamy TUTAJ i to jest konieczne, nie kosmetyka: Ludusavi
   *  rozwiązuje prefiks gry non-Steam po nazwie ze `shortcuts.vdf` (ZMIERZONE), więc
   *  kafelek pod starą nazwą znaczy „nie znajdę zapisów tej gry". Backend do
   *  `shortcuts.vdf` nie pisze, bo plik należy do klienta Steama. */
  const changeTitle = (wanted: string) =>
    run(t("ui.retitle_button"), async () => {
      const out = await retitle(game.title_key, wanted);
      if (!out.ok || !out.title) return fromBackend(out.error) || t("ui.action_failed");
      if (out.appid && nameOf(out.appid) !== out.title) setShortcutName(out.appid, out.title);
      setNote(t("ui.retitle_done", { title: out.title }));
      // Trasą tego ekranu jest KLUCZ gry, a klucz właśnie się zmienił — bez przejścia
      // pod nowy adres SidebarNavigation nie znajduje strony i spada na pierwszą grę
      // z listy, co wygląda jak „wtyczka zgubiła moją grę".
      if (out.title_key && out.title_key !== game.title_key) {
        selectGame(out.title_key);
        Navigation.Navigate(gameRoute(out.title_key));
      }
      return null;
    })();

  const pointAtStore = (appid: number, name: string) =>
    run(t("ui.storepick_button"), async () => {
      const out = await setStoreAppid(game.title_key, appid, steamLocale() || "en");
      if (!out.ok) return fromBackend(out.error) || t("ui.action_failed");
      setNote(appid ? t("ui.storepick_done", { name }) : t("ui.storepick_cleared"));
      return null;
    })();

  /** Ukrycie/odkrycie jest lokalne i pamięta je Steam, więc nic nie zapisujemy
   *  w rejestrze; po akcji odświeżamy listę i foreignTiles liczy na nowo. */
  const toggleHidden = (appids: number[], hidden: boolean) => () => {
    appids.forEach((appid) => setHidden(appid, hidden));
    onChanged();
  };

  /** Pierwsza strefa: to, co robi się często. Budowana jako LISTA, bo pełna szerokość
   *  samotnego przycisku wymaga znajomości liczby sąsiadów — a warunki są trzy
   *  i każdy może zniknąć. */
  const teraz: Array<{ klucz: string; tresc: any; akcja: () => void }> = [];
  if (!game.conflict) {
    teraz.push({
      klucz: "sync",
      tresc: <><FaSync /> {busy ? t("qa.working") : t("ui.sync_this_game")}</>,
      akcja: sync,
    });
  }
  if (duplicates.visible.length > 0) {
    teraz.push({
      klucz: "hide",
      tresc: <><FaEyeSlash /> {t("ui.hide_duplicates", { count: duplicates.visible.length })}</>,
      akcja: toggleHidden(duplicates.visible, true),
    });
  }
  if (duplicates.hidden.length > 0) {
    teraz.push({
      klucz: "show",
      tresc: <><FaEye /> {t("ui.show_hidden", { count: duplicates.hidden.length })}</>,
      akcja: toggleHidden(duplicates.hidden, false),
    });
  }

  return (
    // BEZ `overflow: hidden` i bez zaokrąglenia: tło ma wychodzić POZA kolumnę treści
    // na ciemne marginesy panelu (ZGŁOSZONE: „powinno być na całą szerokość, żeby
    // ładnie było widać blur i krawędzie tych sekcji"). Przycięciem zajmuje się
    // kontener przewijania Steama, który ma `overflow: hidden auto`.
    <div style={{ position: "relative" }}>
      {/* Reguła w treści komponentu, nie wstrzykiwana do `document.head`: ekran renderuje
          się w innym kontekście CEF niż logika wtyczki, a `<style>` w JSX trafia tam,
          gdzie trzeba, i znika razem z ekranem — bez własnego sprzątania. */}
      <style>{NIEODWRACALNY_CSS}</style>
      {/* Okładka jako OSOBNA WARSTWA pod treścią, nie jako `background-image` kontenera.
          ZGŁOSZONE z urządzenia: „jest fajnie przezroczyste, ale nie ma blura".
          Przyczyna była w gęstości, nie w warstwach: `backdrop-filter` rozmywa to, co za
          elementem, a za nim stała okładka przyciemniona do 0,80–0,92 — czyli prawie
          płaska czerń. Rozmycie czerni wygląda dokładnie jak brak rozmycia.
          Dziś okładka zostaje WIDOCZNA (własne lekkie rozmycie + przyciemnienie do 0,62),
          więc strefy mają co rozmywać i różnica między szybą a szczeliną między szybami
          jest widoczna. `scale(1.06)` zakrywa jasny brzeg, który rozmycie zostawia na
          krawędzi obrazu. */}
      {art.src && (
        <div
          aria-hidden
          style={{
            position: "absolute",
            // Zapas z zapasem, a przycina Steam — patrz TLO_ZAPAS.
            left: `-${TLO_ZAPAS.bok}`,
            right: `-${TLO_ZAPAS.bok}`,
            top: `-${TLO_ZAPAS.gora}`,
            bottom: 0,
            minHeight: TLO_ZAPAS.dol,
            overflow: "hidden",
          }}
        >
          {/* BEZ `onError` i to jest konieczne, nie przeoczenie. ZMIERZONE na Decku:
              gdy tło i baner zgłaszały 404 do TEGO SAMEGO licznika prób, jeden nieudany
              adres przeskakiwał DWA miejsca w kolejce kandydatów — przy czterech
              adresach (hero.jpg → hero.png → .jpg → .png) wypadało się za listę i gra
              z grafikami dostawała gradient („007 First Light", appid 4200886711, hero
              w chmurze OBECNY). Kandydata próbuje więc wyłącznie baner; tło pokazuje
              to, co baner ustali, a jego własna nieudana próba nic nie rysuje. */}
          <img
            src={art.src}
            alt=""
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              filter: "blur(5px) saturate(1.1)",
              transform: "scale(1.06)",
            }}
          />
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "linear-gradient(rgba(9, 13, 18, 0.55) 0%, rgba(9, 13, 18, 0.72) 45%, rgba(9, 13, 18, 0.82) 100%)",
            }}
          />
        </div>
      )}

      <div
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          padding: "0 0 16px",
        }}
      >
      <GameHero art={art} title={game.title} />

      {game.conflict && (
        <div style={{ ...KARTA, borderLeft: "3px solid #ffb347" }}>
          <ConflictChoice titleKey={game.title_key} busy={busy} onChoose={resolve} firstRef={first} />
        </div>
      )}

      {/* `Focusable` na karcie informacji NIE jest po to, żeby dało się ją kliknąć —
          jest po to, żeby strzałka w górę miała gdzie wrócić. ZGŁOSZONE: schodząc do
          przycisków i wracając w górę, zaznaczenie wyskakiwało z naszej sekcji na
          wyszukiwarkę Steama, bo nad przyciskami nie było ANI JEDNEGO elementu, który
          przyjmuje zaznaczenie — baner i fakty to zwykłe `div`y. Teraz zaznaczenie
          zatrzymuje się tutaj, a Steam przewija sekcję na górę. */}
      <Focusable
        // `onActivate` jest tu OBOWIĄZKOWE, choć nic nie robi. ZMIERZONE na Decku:
        // `Focusable` bez niego renderuje się z `tabIndex: -1`, czyli nie jest celem
        // nawigacji i cała ta zmiana nie działa — a wygląda, jakby działała, bo element
        // ma już klasę „Focusable". Pusta obsługa znaczy „przycisk A nic tu nie robi",
        // i tak ma być: to jest przystanek dla zaznaczenia, nie akcja.
        onActivate={() => undefined}
        style={{ ...KARTA, display: "flex", flexDirection: "column", gap: "10px" }}
      >
        <GameMeta meta={meta} problem={metaProblem} busy={metaBusy} />
        <GameFacts game={game} duplicates={duplicates} />
      </Focusable>

      {problem && <div style={{ ...KARTA, fontSize: "0.85em", color: "#ff6b6b" }}>{problem}</div>}
      {note && <div style={{ ...KARTA, fontSize: "0.85em", opacity: 0.8 }}>{note}</div>}

      {/* JEDNO pudełko na całą strefę „co mogę z tą grą zrobić", nie osiem osobnych
          przycisków na gołym tle i przełącznik obok. ZGŁOSZONE: „jest ich już trochę,
          a »pomijaj przy synchronizacji« wygląda dziwnie" — bo wyglądał: był jedynym
          elementem w osobnej szybie, choć jest ustawieniem TEJ SAMEJ rzeczy.

          Grupy mają nagłówki, bo te przyciski nie są równorzędne: synchronizacja to
          robota codzienna, grafiki i opis to ozdoby, a trzy pozostałe rusza się wtedy,
          gdy wtyczka źle trafiła — i wtedy szuka się ich po znaczeniu, nie po kolejności.

          Pudełko jest zwykłym `div`em, NIE `Focusable`: rodzic przyjmujący zaznaczenie
          staje się jedynym przystankiem i nawigacja pada nie schodzi do przycisków
          w środku (dokładnie to zabrało „Odśwież opis" — patrz GameMeta). */}
      <div style={{ ...KARTA, display: "flex", flexDirection: "column", gap: "10px" }}>
        <Focusable style={SIATKA}>
          {teraz.map((przycisk, indeks) => (
            <DialogButton
              key={przycisk.klucz}
              ref={indeks === 0 ? (first as any) : undefined}
              style={{ ...PRZYCISK, ...samotny(indeks, teraz.length) }}
              disabled={busy}
              onClick={przycisk.akcja}
            >
              {przycisk.tresc}
            </DialogButton>
          ))}
        </Focusable>

        <div style={GRUPA}>{t("ui.actions.decor")}</div>
        <Focusable style={SIATKA}>
          {game.appid !== null && (
            <DialogButton style={PRZYCISK} disabled={busy} onClick={artwork}>
              <FaImages /> {game.artwork_done ? t("ui.fetch_artwork_again") : t("ui.fetch_artwork")}
            </DialogButton>
          )}
          {/* „Odśwież opis" MUSI być tutaj, a nie w karcie informacji. ZGŁOSZONE:
              „przycisk jest nieklikalny przy użyciu kontrolera, trzeba użyć myszki" —
              siedział w `Focusable` zagnieżdżonym w karcie, która sama ma `onActivate`,
              więc zaznaczenie zatrzymywało się na rodzicu i nigdy nie schodziło niżej. */}
          {/* Gra bez kafelka nie ma czego dekorować, więc grafik nie ma nad czym
              pracować — wtedy opis zostaje tu sam i bierze cały wiersz. */}
          <DialogButton
            style={{ ...PRZYCISK, ...(game.appid === null ? { gridColumn: "1 / -1" } : {}) }}
            disabled={busy || metaBusy}
            onClick={() => refreshMeta()}
          >
            <FaAlignLeft /> {metaBusy ? t("ui.meta.fetching") : t("ui.meta.refresh")}
          </DialogButton>
        </Focusable>

        <div style={GRUPA}>{t("ui.actions.fix")}</div>
        <Focusable style={SIATKA}>
          <DialogButton style={PRZYCISK} disabled={busy} onClick={pickExe}>
            <FaFolderOpen /> {t("ui.pick_exe_button")}
          </DialogButton>
          <DialogButton
            style={PRZYCISK}
            disabled={busy}
            onClick={() =>
              // Bez karty backend i tak odmówi (katalogu kopii nie ma jak przemianować),
              // więc mówimy to PRZED wpisywaniem tytułu, a nie po wybraniu z listy.
              game.card_present === false
                ? setProblem(t("ui.retitle_no_card", { title: game.title }))
                : openPicker({
                    title: t("ui.retitle_title"),
                    intro: t("ui.retitle_intro"),
                    initial: game.title,
                    onPick: (hit) => changeTitle(hit.value),
                  })
            }
          >
            <FaSearch /> {t("ui.retitle_button")}
          </DialogButton>
          <DialogButton
            style={{ ...PRZYCISK, gridColumn: "1 / -1" }}
            disabled={busy}
            onClick={() =>
              openPicker({
                title: t("ui.storepick_title"),
                intro: t("ui.storepick_intro"),
                initial: game.title,
                label: t("ui.storepick_label"),
                foundLabel: t("ui.storepick_found"),
                noneLabel: (wanted) => t("ui.storepick_none", { wanted }),
                // Na przycisku NAZWA i ROK, nigdy appid. ZGŁOSZONE: „appid to nie jest
                // dla ludzi znane" — a rozróżnić wydania (Legacy wobec Enhanced) trzeba,
                // więc rok robi to, co miała robić liczba. `value` niesie appid dalej.
                search: async (text) =>
                  (await storeSearch(text, steamLocale() || "en")).map((gra) => ({
                    value: String(gra.appid),
                    label: gra.year ? `${gra.name} · ${gra.year}` : gra.name,
                  })),
                // Cofnięcie wskazania musi być W TYM SAMYM oknie: osobny przycisk
                // w siatce akcji istniałby tylko dla jednej gry na dwadzieścia.
                extra: game.steam_appid
                  ? (close) => (
                      <Focusable>
                        <DialogButton
                          onClick={() => {
                            close();
                            pointAtStore(0, "");
                          }}
                        >
                          {t("ui.storepick_clear")}
                        </DialogButton>
                      </Focusable>
                    )
                  : undefined,
                onPick: (hit) => pointAtStore(Number(hit.value), hit.label),
              })
            }
          >
            <FaSteam /> {t("ui.storepick_button")}
          </DialogButton>
        </Focusable>

        {/* Przełącznik w TYM SAMYM pudełku co akcje, za kreską: to ustawienie tej samej
            rzeczy, którą włącza przycisk „Synchronizuj tę grę", więc osobna szyba obok
            czytała się jak nowa sekcja o niczym. */}
        <div style={{ borderTop: "1px solid rgba(255, 255, 255, 0.08)", paddingTop: "2px" }}>
          <ToggleField
            label={t("ui.exclude_toggle_label")}
            checked={!!game.excluded}
            disabled={busy}
            onChange={(value) =>
              void run(t("ui.label.toggle_exclude"), async () => {
                const updated = await setExcluded(game.title_key, value);
                return updated.error ? fromBackend(updated.error) : null;
              })()
            }
          />
        </div>
      </div>

      {/* Strefa nieodwracalna: za kreską i niżej niż reszta. Zdjęcie kafelka odkłada
          zapis na kartę i tego się nie cofa jednym kliknięciem, więc nie może stać
          w tym samym rytmie co „Pobierz grafiki". */}
      <Focusable
        style={{
          ...SIATKA,
          borderTop: "1px solid rgba(255, 255, 255, 0.08)",
          paddingTop: "12px",
        }}
      >
        <DialogButton
          className="sdsync-danger"
          style={{ ...PRZYCISK, ...(forceReady ? {} : { gridColumn: "1 / -1" }) }}
          disabled={busy}
          onClick={confirmForget ? forget : () => setConfirmForget(true)}
        >
          <FaTrashAlt /> {confirmForget ? t("ui.confirm_forget_button") : t("ui.stop_managing_button")}
        </DialogButton>
        {forceReady && (
          <DialogButton className="sdsync-danger" style={PRZYCISK} disabled={busy} onClick={forgetForce}>
            <FaExclamationTriangle /> {t("ui.forget_force_button")}
          </DialogButton>
        )}
      </Focusable>
      </div>
    </div>
  );
}
