import { GameRecord } from "./backend";
import { locale, t } from "./i18n";
import { entriesForTitle, isHidden, nameOf } from "./steam";

/** Jedna linia pod tytułem: NAJGORSZA rzecz, która jest prawdą. Kolejność jest
 *  ważniejsza niż zawartość — użytkownik z trzema grami czyta pierwszą linijkę,
 *  nie pięć faktów rozdzielonych kropkami (tak wyglądał ekran przed etapem 5). */
export function gameStatus(game: GameRecord): { text: string; alarm: boolean } {
  // Gra, której bazie Ludusavi nie zna, NIE MA obsługi zapisów — i to jest inny
  // problem niż konflikt. Wcześniej wychodziła właśnie jako konflikt, i to nie
  // ona jedna: jedna taka nazwa wywalała zbiorcze wywołanie i konflikt dostawały
  // wszystkie gry (ZMIERZONE na Decku: 12 z 12).
  if (game.ludusavi_unknown) {
    return { text: t("status.ludusavi_unknown"), alarm: true };
  }
  if (game.conflict) return { text: t("status.conflict"), alarm: true };
  if (game.excluded) return { text: t("status.excluded"), alarm: false };
  // Dwa różne stany, dotąd zlane w jeden: bez tego użytkownik nie wie, czy czekać
  // na kartę, czy gra została z niej usunięta i kafelek może zniknąć.
  // Gra z dysku konsoli nie ma karty — „brak karty" byłoby tu bzdurą, a „gry nie ma
  // na karcie" sugerowałoby, że coś zniknęło.
  if (game.carrier === "disk" && !game.available) {
    return { text: t("status.no_file_on_disk"), alarm: true };
  }
  if (!game.available) {
    return game.card_present
      ? { text: t("status.not_on_card"), alarm: true }
      : { text: t("status.no_card"), alarm: false };
  }
  const backup = game.last_backup_ts;
  if (!backup) return { text: t("status.no_backup"), alarm: false };
  return {
    text: t("status.backup_at", { when: new Date(backup * 1000).toLocaleString(locale()) }),
    alarm: false,
  };
}

/** Co wiemy o chmurze BEZ pytania sieci: ekran gry nie może czekać kilkudziesięciu
 *  sekund na rclone (tyle trwa listowanie chmury — zmierzone). Dlatego ostatni wariant
 *  mówi dokładnie tyle, ile wie: kopia MOŻE tam być z drugiego urządzenia, tylko my jej
 *  tam nie kładliśmy. Wcześniej stał tu literalny znak zapytania. */
export function cloudStatus(game: GameRecord): string {
  if (game.conflict) return t("status.cloud_conflict");
  if (game.pending_push) return t("status.cloud_pending_push");
  if (game.last_push_ts) {
    return t("status.cloud_pushed", {
      when: new Date(game.last_push_ts * 1000).toLocaleString(locale()),
    });
  }
  return t("status.cloud_never_pushed");
}

/** Stan kafelka czytamy ze Steama, nie z rejestru: użytkownik mógł kafelek usunąć
 *  albo mu zmienić nazwę, a rejestr dalej trzymałby appid, którego nikt nie widzi. */
export function tileState(game: GameRecord): string {
  if (!game.appid) return t("status.tile_none");
  const name = nameOf(game.appid);
  if (name === null) return t("status.tile_removed", { appid: game.appid });
  if (name !== game.title) return t("status.tile_renamed", { name });
  return t("status.tile_ok");
}

/** Kafelki o tej samej nazwie, których NIE stworzyliśmy: ręczne wpisy użytkownika
 *  i skróty ogłoszone przez drugie urządzenie. To one dają duplikaty w bibliotece.
 *  Zwracamy też te już ukryte — bez nich nie ma jak pokazać przycisku odkrywającego,
 *  a ukrycie byłoby decyzją bez odwrotu w interfejsie wtyczki. */
export function foreignTiles(game: GameRecord): { visible: number[]; hidden: number[] } {
  const out = { visible: [] as number[], hidden: [] as number[] };
  try {
    for (const entry of entriesForTitle(game.title)) {
      if (entry.appid === game.appid) continue;
      // Tylko SKRÓTY. Tytuł pochodzi z kanonicznej bazy Ludusavi, czyli jest to nazwa
      // sklepowa — bez tego filtra „Ukryj duplikaty" ukrywa grę KUPIONĄ na Steamie.
      // Gdy Valve usunie BIsShortcut, entriesForTitle zwraca pustą listę i funkcja
      // przestaje cokolwiek ukrywać — awaria w stronę bezczynności.
      if (!entry.isShortcut) continue;
      (isHidden(entry.appid) ? out.hidden : out.visible).push(entry.appid);
    }
  } catch {
    // brak appStore/collectionStore nie może wysypać całej strony
  }
  return out;
}

/** Kolor kropki stanu: pomarańczowa = konflikt, zielona = nośnik na miejscu,
 *  biała = nie ma go. Ta sama trójka co w panelu na ekranie gry — reguła musi być
 *  JEDNA, bo trzy kropki w trzech kolorach znaczące co innego byłyby gorsze niż
 *  brak kropek.
 *
 *  GRANICA, i jest z pomiaru: kropka na KAFELKU w bibliotece (`steam-badges.tsx`,
 *  `main.py`) tu nie należy. Jest celowo DWUSTANOWA i tania — RPC `card_badges` nie
 *  woła Ludusaviego i nie czyta karty, bo front odpytuje je co 10 s, a stanu konfliktu
 *  świadomie nie pobiera. Objęcie jej tą funkcją znaczyłoby dołożenie kafelkowi
 *  zapytania, którego cała tamta ścieżka unika. */
export function dotColor(game: GameRecord): string {
  if (game.conflict) return "#ffb347";
  return game.available ? "#5ba32b" : "#e9e9e9";
}
