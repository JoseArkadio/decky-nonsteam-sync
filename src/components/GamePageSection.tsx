import { DialogButton, Focusable, Navigation } from "@decky/ui";
import { FaInfoCircle, FaSteam, FaSync } from "react-icons/fa";
import { CSSProperties, useEffect, useState } from "react";
import { GameMetaRecord, GameRecord, UiSettings, deviceKind, games, getUiSettings, storeMetadata, syncGame } from "../backend";
import { gameRoute, selectGame } from "../selection";
import { dotColor, gameStatus } from "../status";
import { greyGamePage } from "../steam-badges";
import { isSteamGame } from "../steam";
import { locale, t } from "../i18n";
import { useGameMeta } from "../metadata";
import { PlayFacts } from "./PlayFacts";
import { useLang } from "../i18n/resolve";
import { MetaFacts } from "./MetaFacts";

/** Panel na ekranie gry — informacja, nie centrum sterowania.
 *
 *  Pozycję wybiera użytkownik w ustawieniach wtyczki, bo ekran gry jest wspólny dla
 *  wszystkich wtyczek: pierwsza wersja siedziała w pasku u góry i nachodziła na tytuł
 *  gry, druga po prawej — dokładnie tam, gdzie rysuje się hltb-for-deck (oba przypadki
 *  ZMIERZONE na zrzutach z urządzenia). Formy wzięte z tamtej wtyczki, bo tam się
 *  sprawdziły: pływający panel po lewej/prawej albo pasek nad treścią.
 *
 *  Akcji tu nie ma świadomie: ekran gry służy do uruchomienia gry. Jedyne wyjście to
 *  „Zobacz szczegóły", które przenosi na nasz ekran z TĄ grą zaznaczoną. */
/** Pas, w którym karta ma się zmieścić, i wyśrodkowanie w NIM.
 *
 *  ZGŁOSZONE: „wyrównaj box w pionie, żeby odstępy od góry i od dołu były takie same".
 *  Pas jest między paskami Steama i ZMIERZONY na obu urządzeniach: górny kończy się na
 *  40 px (identycznie na Decku i Machine — to stała wysokość, nie proporcja), a dolny
 *  zaczyna się na 548 px przy oknie 616 (89,0vh) i 740 px przy 844 (87,7vh). `13vh`
 *  zapasu od dołu mieści się pod obiema wartościami: środek pasa wypada wtedy 6 px
 *  (Deck) i 3 px (Machine) od prawdziwego środka.
 *
 *  Wyśrodkowanie robi FLEX na opakowaniu, a NIE `transform: translateY(-50%)` na karcie
 *  — i to nie jest wybór stylu. ZMIERZONE prototypem na urządzeniu: `transform` na
 *  przodku ZRYWA `background-attachment: fixed`, czyli szybę pod kartą (tło przestaje
 *  liczyć się wobec okna i cały obraz wciska się w pudełko). Wyśrodkowanie flexem tego
 *  nie robi.
 *
 *  Opakowanie przepuszcza kliknięcia (`pointerEvents: none`) — inaczej niewidzialny
 *  prostokąt na pół ekranu przykryłby przycisk GRAJ. */
const PAS: CSSProperties = {
  // ABSOLUTE, nie `fixed`. ZMIERZONE na Decku: `position: fixed` wyszło tu wysokie na
  // 1108 px zamiast 496 — czyli było liczone wobec PRZEWIJANEGO kontenera ekranu gry
  // (1228 px), a nie wobec okna. Któryś przodek jest blokiem zawierającym dla rzeczy
  // przypiętych do okna, i to bywa stan przelotny po animacji Steama, więc nie da się
  // na tym oprzeć. Rodzic od Steama jest za to stabilny: zerowa wysokość, y=532 przy
  // oknie 616 i y=724 przy 844 (ZMIERZONE na obu urządzeniach).
  position: "absolute",
  bottom: 0,
  // Wysokość pasa: od rodzica w górę. 78vh daje środek w 292 px na Decku (prawdziwy
  // środek pasa 294) i w 395 px na Machine (prawdziwy 390) — różnice niewidoczne,
  // a górna krawędź zostaje pod paskiem Steama (52 px i 66 px wobec paska do 40 px).
  height: "78vh",
  display: "flex",
  alignItems: "center",
  // Niewidzialny prostokąt na pół ekranu nie może przykryć przycisku GRAJ ani okładki;
  // karta w środku przywraca sobie klikalność.
  pointerEvents: "none",
};

const LAYOUT: Record<UiSettings["game_page"], CSSProperties | null> = {
  left: { ...PAS, left: "2.8vw", justifyContent: "flex-start" },
  right: { ...PAS, right: "2.8vw", justifyContent: "flex-end" },
  bar: { position: "relative", width: "100%", borderBottom: "2px solid rgba(61, 68, 80, 0.54)" },
  off: null,
};

/** Pudełko karty — wspólne dla gry naszej i gry ze Steama, żeby nie rozjechały się
 *  wizualnie. Szerokość ZWIĄZANA, wysokość rośnie z treścią. */
const pudelko = (pasek: boolean): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
  // ZGŁOSZONE: „popracuj nad odstępami, wygląda średnio; przyciski są zbyt blisko
  // innych informacji i na pierwszy rzut oka nie wyglądają na klikalne". Odstęp
  // MIĘDZY GRUPAMI jest większy niż wewnątrz grupy — dopiero wtedy widać, że to są
  // osobne rzeczy, a nie jeden ciąg tekstu.
  gap: "12px",
  width: pasek ? "100%" : "min(420px, 34vw)",
  padding: pasek ? "12px 16px" : "16px 18px",
  fontSize: "0.8em",
  lineHeight: 1.35,
  // ZGŁOSZONE: „bardzo podoba mi się blur w sekcjach, czy możesz dać taki sam pod
  // kafelek na ekranie gry". Sedno nie jest w sile rozmycia, tylko w GĘSTOŚCI tła:
  // `backdrop-filter` rozmywa to, co pod spodem, a rozmycie prawie płaskiej czerni
  // wygląda dokładnie jak brak rozmycia (ta sama grabla, co przy prawej sekcji).
  // 0,82 było właśnie taką czernią. Pod tą kartą leży grafika gry ze Steama, czyli
  // tło z prawdziwym szczegółem — więc wolno je przerzedzić. Gęściej niż nasza sekcja
  // (0,60 wobec 0,52), bo tamta leży na własnym, już przyciemnionym tle, a ta na
  // cudzym i bywa jasne.
  // Tło zostaje na wypadek gry BEZ grafiki: wtedy nie ma czego rozmywać i karta
  // musi być czytelna sama z siebie. Gdy grafika jest, warstwa niżej ją przykrywa.
  background: TLO,
  position: "relative",
  overflow: "hidden",
  maxHeight: pasek ? undefined : "100%",
  // ZGŁOSZONE: „została biała obwódka wokół sekcji, co na jasnych tłach wygląda
  // średnio". I tak było: jasna linia na ciemnej karcie nad jasną grafiką odcinała
  // się od obu. Kształt karty niesie dziś sam cień — mocniejszy i szerszy, bo to on
  // przejął rolę krawędzi.
  border: "none",
  borderRadius: pasek ? 0 : "10px",
  boxShadow: pasek ? "none" : "0 10px 30px rgba(0, 0, 0, 0.55)",
  // kontenery Steama po drodze mogą mieć pointer-events: none — bez tego kliknięcie
  // nie reaguje (wzorzec z hltb-for-deck)
  pointerEvents: "auto",
});

/** Tło karty: samo PRZYCIEMNIENIE, bez rozmycia.
 *
 *  ZGŁOSZONE po obejrzeniu szyby na wielu grach: „pozbądź się blura i zrób fajne
 *  przyciemnienie, bo ta sekcja nie wygląda tak dobrze na każdej z gier". To jest
 *  odwrócenie decyzji sprzed dwóch kroków i wpisuję je jako odwrócenie: rozmyta
 *  okładka pod kartą działała pięknie na Gothicu (ciemne, jednolite tło) i psuła się
 *  na okładkach z mocnym rysunkiem tuż za kartą — bo rozmycie zachowuje JASNOŚĆ
 *  i KOLOR, a te na każdej grze są inne. Przyciemnienie jest przewidywalne: wygląda
 *  tak samo niezależnie od tego, co jest pod spodem, a o to chodzi w tle pod tekstem.
 *
 *  Odrobina przezroczystości zostaje (0,94 → 0,88 z góry na dół), żeby karta nie
 *  czytała się jak wklejony prostokąt — okładka prześwituje na tyle, żeby było widać,
 *  że karta leży NA grze, a nie obok niej. Gradient, bo u góry stoi tytuł i wiersz
 *  stanu, a niżej plakietki, które znoszą jaśniejsze tło.
 */
const TLO = "linear-gradient(rgba(10, 14, 20, 0.94) 0%, rgba(10, 14, 20, 0.88) 100%)";

/** Przycisk na karcie ekranu gry. Mniejszy niż na naszym ekranie, bo karta ma
 *  szerokość związaną (`min(420px, 34vw)`) i dzieli ją na trzy. */
const MALY = {
  width: "100%",
  minWidth: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "5px",
  padding: "5px 6px",
  fontSize: "0.95em",
} as const;

/** Na czym to chodzi. Pytamy RAZ na proces: to fakt o sprzęcie, więc nie zmieni się
 *  między jedną grą a drugą, a każde wejście na ekran gry to kolejne wywołanie RPC. */
let znaneUrzadzenie: "deck" | "machine" | "steamos" | null = null;

export function GamePageSection({ appid }: { appid: number }) {
  // Trzeci korzeń renderowania (obok panelu Quick Access i ekranu `/sdsync`): każdy
  // z nich żyje w INNYM kontekście CEF i żaden nie jest przodkiem pozostałych, więc
  // każdy musi sam zamówić przerysowanie po rozstrzygnięciu języka. Bez tego karta
  // rysowała napisy zapasowe — ZMIERZONE: „20 achievements" i „Steam Deck: verified"
  // po angielsku obok polskiej reszty.
  useLang();
  const [game, setGame] = useState<GameRecord | null>(null);
  // null = ustawień jeszcze nie znamy. Nie zgadujemy „left": przy panelu wyłączonym
  // (`off`) zgadnięcie odpaliłoby zapytanie do sklepu o grę, której panelu nikt nie
  // zobaczy — a sieci za plecami użytkownika nie robimy.
  const [where, setWhere] = useState<UiSettings["game_page"] | null>(null);
  const [dlaSteama, setDlaSteama] = useState(false);
  // Karta informacyjna dla gry ZE STEAMA: jej nie obsługujemy (zapisów nie tykamy),
  // ale ekran gry i tak nie pokazuje daty premiery, oceny ani trybów — a my je mamy.
  const [obca, setObca] = useState<GameMetaRecord | null>(null);
  // Stan przycisku synchronizacji. Osobno od reszty: to jedyna rzecz na tym ekranie,
  // która trwa dłużej niż mrugnięcie, a „Pracuję…" bez śladu na przycisku wyglądałoby
  // jak kliknięcie, które nic nie zrobiło.
  const [urzadzenie, setUrzadzenie] = useState(znaneUrzadzenie);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  // Ekran gry dla kafelka non-Steam jest pusty: Valve nie ma o tej grze nic. Opis
  // i ocena ze sklepu wypełniają tę dziurę — treść tłumaczy sklep, więc idzie
  // w języku interfejsu.
  const placement = where ? LAYOUT[where] : null;
  const { meta } = useGameMeta(game && placement ? game.title_key : null);

  useEffect(() => {
    games()
      .then((list) => setGame(list.find((record) => record.appid === appid) ?? null))
      .catch(() => setGame(null));
    if (znaneUrzadzenie === null) {
      deviceKind()
        .then((rodzaj) => {
          znaneUrzadzenie = rodzaj;
          setUrzadzenie(rodzaj);
        })
        .catch(() => undefined); // brak odpowiedzi = pokażemy pełne rozbicie
    }
    getUiSettings()
      .then((settings) => {
        setWhere(settings.game_page);
        setDlaSteama(settings.game_page_steam !== "off");
      })
      .catch(() => setWhere("left")); // brak ustawień = domyślna lewa strona
  }, [appid]);

  // Pytamy sklep TYLKO o prawdziwe gry Steama. Cudzy skrót non-Steam ma appid, którego
  // sklep nigdy nie zna, więc bez tego sprawdzenia każde wejście na taki kafelek
  // kosztowałoby jedno zapytanie bez żadnej możliwej odpowiedzi.
  useEffect(() => {
    setObca(null);
    if (!dlaSteama || !placement || game || !isSteamGame(appid)) return;
    let zyje = true;
    storeMetadata(appid, locale())
      .then((m) => {
        if (zyje && m && !m.missing && !m.error) setObca(m);
      })
      .catch(() => undefined); // informacja dodatkowa: cisza jest tu proporcjonalna
    return () => {
      zyje = false;
    };
  }, [appid, dlaSteama, placement, game]);

  // Czarno-białe tło, gdy karty nie ma — ta sama informacja co ostrzeżenie niżej,
  // tylko widoczna kątem oka. Cofamy przy odmontowaniu, żeby ekran innej gry (albo
  // ta sama po włożeniu karty) nie został szary.
  useEffect(() => {
    if (!game) return;
    greyGamePage(appid, !game.available);
    return () => {
      greyGamePage(appid, false);
    };
  }, [appid, game?.available]);

  if (!placement) return null; // panel wyłączony w ustawieniach

  // Gra ze Steama: sama informacja, bez wiersza stanu i bez przejścia na nasz ekran —
  // nie mamy o niej nic do powiedzenia poza tym, co przyszło ze sklepu.
  if (!game) {
    if (!obca) return null; // cudzy skrót, emulator albo gra bez danych w sklepie
    return (
      <div style={placement}>
        <div style={pudelko(where === "bar")}>
        <div style={{ position: "relative", fontSize: "1.3em", fontWeight: "bold", lineHeight: 1.15 }}>
          {obca.name}
        </div>
        <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: "9px" }}>
          <MetaFacts meta={obca} clamp={4} onlyDevice={urzadzenie ?? undefined} />
        </div>
        {/* Czas przejścia także dla gry ZE STEAMA — po to ta karta w ogóle jest:
            ekran gry tych rzeczy nie pokazuje, a my mamy je dla obu rodzajów gier. */}
        <div style={{ position: "relative" }}>
          <PlayFacts appid={appid} steamAppid={obca.steam_appid ?? appid} name={obca.name} compact />
        </div>
        </div>
      </div>
    );
  }

  const status = gameStatus(game);
  // Gra w sklepie Steama, na którą wolno przejść. Bierzemy ją z METADANYCH, nie z appid
  // kafelka: appid skrótu non-Steam nadaje Steam sam i sklep nigdy go nie zna, więc
  // przycisk prowadziłby na pustą stronę.
  const sklep = meta?.steam_appid;

  /** Synchronizacja TEJ gry, przed kliknięciem GRAJ. Wynik w jednym słowie — kartę
   *  szczegółów mamy o jeden przycisk dalej i tam jest miejsce na całą prawdę. */
  const synchronizuj = async () => {
    setSyncBusy(true);
    setSyncNote(null);
    try {
      const wynik = await syncGame(game.title_key);
      setSyncNote(
        wynik.errors?.length
          ? t("ui.page.sync_failed")
          : wynik.restored?.length
            ? t("ui.sync_note.restored")
            : wynik.conflicts?.length
              ? t("ui.sync_note.conflict")
              : t("ui.sync_note.unchanged"),
      );
    } catch {
      setSyncNote(t("ui.page.sync_failed"));
    } finally {
      setSyncBusy(false);
      // stan mógł się zmienić (kropka, „brak kopii zapisów") — bez tego karta
      // pokazywałaby to, co przed synchronizacją
      games()
        .then((list) => setGame(list.find((record) => record.appid === appid) ?? null))
        .catch(() => undefined);
    }
  };

  // Karta informacyjna, nie tylko wiersz stanu. ZGŁOSZONE: „poszerz i powiększ, żeby
  // wszystko się zmieściło" — bo ekran gry kafelka non-Steam jest inaczej PUSTY, Valve
  // nie ma o tej grze nic, więc to jedyne miejsce, gdzie te fakty mogą się pokazać przed
  // uruchomieniem gry. Układ jest TEN SAM co w szczegółach na naszym ekranie (wspólny
  // `MetaFacts`), tylko bez przycisków: ekran gry służy do odpalenia gry, nie do akcji.
  const kropka = dotColor(game);
  const problem = game.conflict
    ? t("status.conflict_short")
    : game.excluded
      ? t("status.excluded_short")
      : !game.available
        ? game.card_present
          ? t("status.not_on_card_short")
          : t("status.no_card")
        : null;
  const pasek = where === "bar";

  return (
    <div style={placement}>
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        // odstęp między GRUPAMI (stan / tytuł / fakty / czas przejścia / akcje);
        // wewnątrz grup jest ciaśniej, i to ta różnica robi porządek
        gap: "12px",
        // Szerokość związana, żeby karta nie weszła w tytuł gry po lewej; wysokość
        // rośnie z treścią, bo opis ma się zmieścić CAŁY.
        width: pasek ? "100%" : "min(420px, 34vw)",
        padding: pasek ? "12px 16px" : "16px 18px",
        fontSize: "0.8em",
        lineHeight: 1.35,
        background: TLO,
        border: "none",
        borderRadius: pasek ? 0 : "10px",
        boxShadow: pasek ? "none" : "0 10px 30px rgba(0, 0, 0, 0.55)",
        // kontenery Steama po drodze mogą mieć pointer-events: none — bez tego
        // kliknięcie nie reaguje (wzorzec z hltb-for-deck)
        pointerEvents: "auto",
        // `overflow` przycina treść do zaokrąglonych rogów i do `maxHeight`
        position: "relative",
        overflow: "hidden",
        maxHeight: pasek ? undefined : "100%",
      }}
      title={status.text}
    >
      <div style={{ position: "relative", display: "flex", alignItems: "center", gap: "7px", marginBottom: "-6px" }}>
        <div
          style={{
            width: "9px",
            height: "9px",
            borderRadius: "50%",
            background: kropka,
            flex: "0 0 auto",
            boxShadow: "0 0 0 1px rgba(0,0,0,.5)",
          }}
        />
        {/* Etykieta karty to nazwa woluminu, więc bywa UUID-em („1281db6f-…") — przy
            `nowrap` w karcie o STAŁEJ szerokości taki napis wyszedłby poza ramkę.
            Kurczy się z wielokropkiem, a słowo o problemie zostaje całe: ono jest
            ważniejsze niż to, jak karta się nazywa. */}
        <span
          style={{
            opacity: 0.85,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {game.card_label || t("ui.card_unknown")}
        </span>
        {problem && (
          <span
            style={{
              color: game.conflict ? "#ffb347" : "#ff6b6b",
              whiteSpace: "nowrap",
              flex: "0 0 auto",
            }}
          >
            {problem}
          </span>
        )}
      </div>

      <div style={{ position: "relative", fontSize: "1.3em", fontWeight: "bold", lineHeight: 1.15 }}>
        {game.title}
      </div>

      {/* MetaFacts jest fragmentem, więc SAM nie ma odstępów — bierze je z rodzica.
          Bez tej siatki data, opis, autorzy i plakietki zlewały się w jeden ciąg
          (ZGŁOSZONE: „wygląda średnio"). */}
      {meta && !meta.missing && (
        <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: "9px" }}>
          {/* Opis PRZYCIĘTY: ekran gry ma ograniczony pas między paskami Steama, a pełny
              opis potrafi mieć dziesięć wierszy. Cała treść jest o jeden przycisk dalej. */}
          <MetaFacts meta={meta} clamp={4} onlyDevice={urzadzenie ?? undefined} />
        </div>
      )}

      <div style={{ position: "relative" }}>
        <PlayFacts titleKey={game.title_key} steamAppid={meta?.steam_appid} compact />
      </div>

      {/* Akcje NA EKRANIE GRY — i to jest zmiana wcześniejszej decyzji („akcji tu nie
          ma świadomie"). ZGŁOSZONE: „tam też przydałyby się może jakieś przyciski,
          np. do synchronizacji, czy przycisk przenoszący do strony gry na Steamie".
          Zasada, która zostaje: wolno tu tylko to, po co inaczej trzeba by WYJŚĆ
          z ekranu gry. Synchronizacja przed kliknięciem GRAJ jest dokładnie tym —
          a strona w sklepie nie istnieje dla kafelka non-Steam w ogóle.

          Pudełko MUSI być zwykłym `div`em, nie `Focusable` z `onActivate`: rodzic
          przyjmujący zaznaczenie jest jedynym przystankiem w swoim poddrzewie i do
          przycisków nie dałoby się dojechać padem (ZMIERZONE przy „Odśwież opis").
          Dlatego wejście na nasz ekran to teraz przycisk, a nie kliknięcie w kartę. */}
      {/* ZGŁOSZONE: „przyciski są zbyt blisko innych informacji i na pierwszy rzut oka
          nie wyglądają, jakby były klikalne". Nie zmieniamy samych przycisków —
          zmieniamy to, ile mają wokół siebie: kreska oddziela je od faktów, a odstęp
          nad nią jest większy niż odstęp między wierszami informacji. */}
      <Focusable
        style={{
          position: "relative",
          display: "grid",
          gridTemplateColumns: `repeat(${sklep ? 3 : 2}, minmax(0, 1fr))`,
          gap: "8px",
          marginTop: "4px",
          paddingTop: "14px",
          borderTop: "1px solid rgba(255, 255, 255, 0.10)",
        }}
      >
        <DialogButton style={MALY} disabled={syncBusy} onClick={() => void synchronizuj()}>
          <FaSync /> {syncBusy ? t("qa.working") : t("ui.page.sync")}
        </DialogButton>
        {sklep && (
          <DialogButton
            style={MALY}
            onClick={() =>
              Navigation.NavigateToSteamWeb(`https://store.steampowered.com/app/${sklep}`)
            }
          >
            <FaSteam /> {t("ui.page.store")}
          </DialogButton>
        )}
        <DialogButton
          style={MALY}
          onClick={() => {
            selectGame(game.title_key); // niesie tylko fokus — stronę wybiera trasa
            Navigation.Navigate(gameRoute(game.title_key));
          }}
        >
          <FaInfoCircle /> {t("ui.page.details")}
        </DialogButton>
      </Focusable>

      {syncNote && <div style={{ position: "relative", opacity: 0.8 }}>{syncNote}</div>}
    </div>
    </div>
  );

}
