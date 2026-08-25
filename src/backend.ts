import { callable } from "@decky/api";

/** Komunikat dla człowieka z Pythona. Backend NIE produkuje zdań: `code` jest do
 *  przetłumaczenia, `params` do podstawienia, a `message` to angielskie zdanie —
 *  język przewodowy, ten sam, który widać w `tail` na pliku logu. Nieznany kod
 *  pokazujemy jako `message`, więc użytkownik nigdy nie widzi identyfikatora. */
export interface Msg {
  code: string;
  params?: Record<string, string | number>;
  message: string;
}

// Każda metoda backendu jest opakowana dekoratorem @guarded: przy wyjątku zwraca pusty
// wynik o tym samym kształcie z DODATKOWYM polem "error". Dlatego wszędzie `error?`.
interface Failable {
  error?: Msg;
}

export interface Candidate extends Failable {
  folder: string;
  exe_abs: string;
  exe_rel: string;
  card_label: string;
  steam_appid_file: string | null;
  title: string | null;
  candidates: string[];
  /** Kafelki Steama wskazujące dokładnie ten .exe — do PRZEJĘCIA zamiast robienia
   *  drugiego. Pusta lista = nikt tej gry jeszcze nie dodał. */
  adopt?: Array<{ appid: number; name: string }>;
}

export interface GameRecord extends Failable {
  title_key: string;
  title: string;
  folder: string;
  card_label: string;
  exe_abs: string;
  exe_rel: string;
  appid: number | null;
  conflict: boolean;
  proton: string | null;
  steamworks_neutralized: boolean;
  artwork_done: boolean;
  pending_push: boolean; // wysyłka odłożona (zajęty zamek albo chmura padła)
  last_push_ts: number | null;
  last_backup_ts: number | null;
  excluded: boolean;
  available?: boolean; // dokłada tylko games(); register_game/set_appid go nie mają
  playtime_total?: number; // sekundy ze WSZYSTKICH urządzeń (rejestr + plik z karty)
  playtime_devices?: Record<string, number>; // rozbicie „u kogo ile"
  /** Czy karta TEJ gry jest w czytniku. Z `available` daje różnicę między
   *  „karty nie ma (czekamy)" i „karta jest, a gry na niej nie ma" (można zdjąć kafelek). */
  card_present?: boolean;
  /** Baza Ludusavi nie zna tego tytułu → gra nie ma obsługi zapisów. */
  ludusavi_unknown?: boolean;
  /** „card" = jeździ wymienną kartą, „disk" = leży na dysku konsoli. */
  carrier?: "card" | "disk";
  /** Gra w sklepie Steama wskazana RĘCZNIE (opis, ocena, zgodność). null = z bazy
   *  Ludusaviego. ZMIERZONE, po co: baza wiąże „Grand Theft Auto V" z wydaniem Legacy,
   *  a na karcie leży Enhanced. */
  steam_appid?: number | null;
}

export interface SyncResult extends Failable {
  restored: string[];
  conflicts: string[];
  skipped: string[];
  blocked: string[];
  errors: Msg[];
}

export interface PushResult extends Failable {
  title: string | null;
  ok: boolean;
  conflict: boolean;
}

export interface ForgetResult extends Failable {
  ok: boolean;
  steamworks_restored?: boolean;
}

export interface LogEntry {
  ts: number;
  kind: string;
  /** Brak `code` = wpis sprzed wielojęzyczności albo zgłoszony przez frontend
   *  przez `log_add`. `fromBackend` pokazuje wtedy `message`. */
  code?: string;
  params?: Record<string, string | number>;
  message: string;
}

export const ping = callable<[], string>("ping");
export const scan = callable<[], Candidate[]>("scan");
export const games = callable<[], GameRecord[]>("games");
export const registerGame = callable<
  [folder: string, title: string, exe_abs: string, card_label: string, neutralize_steamworks: boolean],
  GameRecord
>("register_game");
export const setAppid = callable<[title_key: string, appid: number], GameRecord>("set_appid");
/** Nazwa, pod którą Ludusavi zna tę grę. `title: null` = nie zna jej wcale. */
export const resolveTitle = callable<[text: string], { title: string | null; candidates: string[] }>(
  "resolve_title",
);
/** Tytuły z bazy Ludusaviego zawierające wpisany FRAGMENT — lista do wyboru.
 *
 *  Osobna droga od `resolveTitle`, bo tamta wymaga trafienia w tytuł co do znaku.
 *  ZMIERZONE na Decku: „Marvel Tōkon: Fighting Souls" ma makron, którego nie ma na
 *  klawiaturze ekranowej Steama, a `--fuzzy` na „Marvel Tokon" odpowiada „Mall Tycoon".
 *  To wywołanie czyta manifest z dysku (0,33 s), więc nie chodzi do sieci. */
export const searchTitles = callable<[text: string, limit: number], string[]>("search_titles");
/** Skąd wolno wziąć zapis przy rozjeździe i kiedy każda kopia powstała.
 *
 *  `when` jest TRÓJSTANEM i front musi go rozróżniać razem z backendem: znacznik
 *  ISO-8601 UTC / `""` („pytaliśmy, kopii nie ma") / `null` („nie wiem"). Pokazanie
 *  niewiedzy jako braku kopii kazałoby wybierać w przekonaniu, że gdzieś nic nie ma. */
export interface ConflictOption {
  when: string | null;
}
export interface ConflictOptions extends Failable {
  ok: boolean;
  local: ConflictOption;
  card: ConflictOption & { present: boolean; label: string };
  cloud: ConflictOption & { enabled: boolean };
  /** „local" | „card" | „cloud" | „" — którą kopię zrobiono NAJPÓŹNIEJ. Liczy to
   *  backend: to porównanie znaczników czasu, nie rzecz do rysowania. */
  newest: string;
}
export const conflictOptions = callable<[title_key: string], ConflictOptions>(
  "conflict_options",
);
/** Czas przejścia z HowLongToBeat. TRZY stany, jak metadane ze sklepu: liczby /
 *  `missing` („HLTB nie zna tej gry") / `error` („nie udało się zapytać"). */
export interface HltbTimes extends Failable {
  hltb_id?: number;
  main?: number;
  plus?: number;
  full?: number;
  missing?: boolean;
}
export const hltbTimes = callable<[title_key: string], HltbTimes>("hltb_times");
/** Ilu gra TERAZ. Pusty obiekt = Steam tej gry nie liczy (albo nie znamy appidu);
 *  `error` = nie udało się zapytać. Zero graczy jest prawdziwą odpowiedzią i wolno
 *  je pokazać — dlatego stany są rozdzielone. */
export const playerCount = callable<[appid: number], { players?: number } & Failable>(
  "player_count",
);
/** „deck" / „machine" / „steamos" — na czym wtyczka właśnie działa. Do pokazania
 *  JEDNEJ pastylki zgodności zamiast trzech. */
export const deviceKind = callable<[], "deck" | "machine" | "steamos">("device_kind");
/** Wersje na ekran „O wtyczce". Obie z Decky'ego, nie z naszego kodu — wpisana u nas
 *  rozjechałaby się z tą, którą widzi sklep. */
export type AboutInfo = { version: string; decky: string };
export const about = callable<[], AboutInfo>("about");
/** To samo dla gry ZE STEAMA, której nie ma w naszym rejestrze. */
export const storeHltbTimes = callable<[appid: number, name: string], HltbTimes>(
  "store_hltb_times",
);
/** Zmiana tytułu gry JUŻ dodanej — czyli zmiana jej TOŻSAMOŚCI. Backend przenosi
 *  razem z nią klucz rejestru, katalog kopii na karcie i klucz czasu gry; kafelek
 *  Steama przemianowuje wywołujący, bo to jedyne, co żyje po stronie frontendu. */
export const retitle = callable<
  [title_key: string, title: string],
  { ok: boolean; title_key?: string; title?: string; appid?: number | null } & Failable
>("retitle");
/** Gry w sklepie Steama pod wpisaną frazą — do ręcznego wskazania, skąd brać opis. */
export const storeSearch = callable<
  [text: string, lang: string],
  Array<{ appid: number; name: string; year?: string }>
>("store_search");
/** Zapamiętanie wskazanej gry ze sklepu. `appid = 0` wraca do bazy Ludusaviego. */
export const setStoreAppid = callable<
  [title_key: string, appid: number, lang: string],
  { ok: boolean; steam_appid?: number | null; metadata?: GameMetaRecord } & Failable
>("set_store_appid");
/** {appid: {description, developers, publishers}} — do wypełnienia zakładki
 *  „Informacje o grze" Steama. TANIE i bez sieci: sam rejestr i pamięć podręczna. */
export const tileMetadata = callable<
  [lang: string],
  Record<string, { description: string; developers: string[]; publishers: string[] }>
>("tile_metadata");
/** Metadane gry ZE STEAMA po appidzie — dla kafelków, których nie obsługujemy.
 *  Nie zapisuje niczego w rejestrze: to wyłącznie informacja na ekranie gry. */
export const storeMetadata = callable<[appid: number, lang: string], GameMetaRecord>(
  "store_metadata",
);
/** Metadane ze sklepu Steama. TRZY stany, nie dwa: dane (`name`), `missing` („Steam nie
 *  zna tej gry") i `error` („nie udało się zapytać"). Zlanie dwóch ostatnich w jedno
 *  odebrałoby grze opis na zawsze — po jednym zapytaniu bez sieci. */
export interface GameMetaRecord {
  steam_appid?: number;
  name?: string;
  description?: string;
  release_date?: string;
  genres?: string[];
  developers?: string[];
  publishers?: string[];
  metacritic?: number | null;
  /** Tryby gry — nazwy przychodzą ze sklepu JUŻ przetłumaczone, więc nie tłumaczymy
   *  ich u siebie. Wybrane z kategorii sklepu po identyfikatorze, bo w tej samej
   *  liście siedzi szum („Dostępne napisy", ten sam pad dwa razy). */
  modes?: string[];
  /** null = nie wiemy, ile ich jest. To nie to samo co „gra ich nie ma". */
  achievements?: number | null;
  cloud?: boolean;
  controller?: string | null;
  /** Zgodność ze sprzętem Valve: 0 nietestowana, 1 niewspierana, 2 grywalna,
   *  3 zweryfikowana. Brak klucza albo null = nie wiemy. */
  compat?: {
    deck?: number | null;
    steamos?: number | null;
    machine?: number | null;
    frame?: number | null;
  };
  /** kod języka, w którym zapisano opis — patrz `gameMetadata` */
  lang?: string;
  missing?: boolean;
  error?: Msg;
  fetched?: number;
}
/** Zapamiętane metadane. Bez sieci — pusty obiekt znaczy „jeszcze nie pytaliśmy".
 *  `lang` przekazujemy, bo opis i gatunki są TŁUMACZONE przez sklep: wpis w innym
 *  języku jest tak samo bezużyteczny jak brak wpisu. */
export const gameMetadata = callable<[title_key: string, lang: string], GameMetaRecord>(
  "game_metadata",
);
/** Pobiera metadane ze sklepu. `lang` to kod dwuliterowy; backend tłumaczy go na nazwę. */
export const fetchMetadata = callable<[title_key: string, lang: string], GameMetaRecord>(
  "fetch_metadata",
);
/** {appid: "green"|"white"} — stan kropki na kafelku. TANIE, bo front pyta często. */
export const cardBadges = callable<[], Record<string, "green" | "white">>("card_badges");
/** `error` bywa OBIEMA postaciami naraz: `Msg` ze wspólnego dekoratora `@guarded`
 *  (łapie wyjątek), string z zawartego w metodzie `return {"error": "…"}` na ścieżce
 *  BEZ wyjątku (`_resolve_conflict` i siostrzane funkcje — ujednolicenie na `Msg` to
 *  stage B, nie ta zmiana). Obie postacie są dziś ŻYWE, więc unia zostaje: przez
 *  `fromBackend()` przechodzą bezpiecznie obie. */
type BackendError = Msg | string;

/** Ręczne wskazanie pliku gry. Zwraca appid, żeby dało się przestawić kafelek. */
export const setExe = callable<
  [title_key: string, exe_abs: string],
  { ok: boolean; appid?: number; exe_abs?: string; exe_rel?: string; error?: BackendError }
>("set_exe");
/** Rejestruje grę zainstalowaną na dysku konsoli. Plik wskazany na karcie zostaje
 *  grą KARTOWĄ — inaczej ta sama gra miałaby dwa nośniki. */
export const addDiskGame = callable<[exe_abs: string, title: string], GameRecord>(
  "add_disk_game",
);
export const forgetGame = callable<[title_key: string], ForgetResult>("forget_game");
/** Odkłada zapis i czas gry NA KARTĘ przed zdjęciem kafelka. Zwraca appid do zdjęcia. */
export const archiveToCard = callable<
  [title_key: string],
  { ok: boolean; appid?: number; title?: string; had_saves?: boolean; error?: BackendError }
>("archive_to_card");

/** `null` = cała biblioteka bez gier wykluczonych; lista kluczy = tylko te gry.
 *  Argument podajemy ZAWSZE — pominięty parametr nie ma jak dojechać do Pythona. */
export const syncAll = callable<[titleKeys: string[] | null], SyncResult>("sync_all");
export const pushAfterGame = callable<[appid: number], PushResult>("push_after_game");
export const resolveConflict = callable<[title_key: string, choice: "local" | "card" | "cloud"], { ok: boolean; error?: BackendError }>(
  "resolve_conflict",
);
/** Etap trwającego przebiegu albo pusty łańcuch. */
export const syncStage = callable<[], string>("sync_stage");
export const markRunning = callable<[appid: number, running: boolean], void>("mark_running");
export const logTail = callable<[count: number], LogEntry[]>("log_tail");

export const setFlag = callable<[title_key: string, field: string, value: boolean], GameRecord>("set_flag");

export const seedPlaytime = callable<[appid: number, seconds: number], { ok: boolean; total: number; error?: BackendError }>(
  "seed_playtime",
);
export const addPlaytime = callable<[appid: number, seconds: number], { ok: boolean; total: number; error?: BackendError }>(
  "add_playtime",
);
/** {appid: sekundy} — klucze tekstem, bo tak przechodzą przez JSON. */
export const playtimeByAppid = callable<[], Record<string, number>>("playtime_by_appid");

export const artworkFor = callable<[title: string], Record<string, string>>("artwork_for");
export const artworkBase64 = callable<[url: string], string>("artwork_base64");
export const setSgdbKey = callable<[key: string], void>("set_sgdb_key");
export const hasSgdbKey = callable<[], boolean>("has_sgdb_key");

export const syncGame = callable<[title_key: string], SyncResult>("sync_game");
export const setExcluded = callable<[title_key: string, excluded: boolean], GameRecord>("set_excluded");
export const setArtworkDone = callable<[title_key: string, done: boolean], GameRecord>("set_artwork_done");
export const cloudConfigured = callable<[], { configured: boolean | null; error?: BackendError }>("cloud_configured");
export const logAdd = callable<[kind: string, message: string], void>("log_add");

/** Ustawienia wyglądu. `game_page`: gdzie panel na ekranie gry — „left" (domyślnie,
 *  bo prawą stronę zajmuje hltb-for-deck), „right", „bar" (pasek nad treścią), „off". */
export interface UiSettings extends Failable {
  game_page: "left" | "right" | "bar" | "off";
  /** „off" = zapisy jeżdżą tylko na karcie, bez ani jednego wywołania sieciowego. */
  sync_cloud: "on" | "off";
  /** Narożnik ikonki karty na kafelku; „off" = nie rysuj. */
  badge_pos: "bottom-right" | "bottom-left" | "top-right" | "top-left" | "off";
  /** „auto" = język interfejsu Steama. Lista musi zgadzać się z CATALOGS
   *  w src/i18n/index.ts — pilnuje tego test w tests/test_main.py. */
  lang: "auto" | "pl" | "en";
  /** Karta informacyjna także na ekranie gier ZE STEAMA. Gra Steama nie wchodzi przez
   *  to pod opiekę wtyczki — zapisów jej nie tykamy. */
  game_page_steam: "on" | "off";
}
export const getUiSettings = callable<[], UiSettings>("get_ui_settings");
export const setUiSetting = callable<[key: string, value: string], UiSettings>("set_ui_setting");
