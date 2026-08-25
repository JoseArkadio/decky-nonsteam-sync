// Jedyne miejsce w projekcie, które dotyka nieudokumentowanego API Steama.
// Gdy Valve zmieni nazwy metod, naprawiamy tylko ten plik.
import { t } from "./i18n";

declare const appStore: any;
declare const appInfoStore: any;
declare const collectionStore: any;

/** Odczyt niezadeklarowanej globalnej rzuca ReferenceError — opcjonalne łańcuchowanie
 *  (`appStore?.x`) NIE chroni przed tym, bo najpierw musi odczytać `appStore`.
 *  `nameOf` leci synchronicznie w renderze GameRow, więc wyjątek tam gasi cały ekran
 *  NonSteam Sync — jedyne miejsce, gdzie użytkownik rozstrzyga konflikty. */
const apps = (): any => (typeof appStore === "undefined" ? null : appStore);
const collections = (): any => (typeof collectionStore === "undefined" ? null : collectionStore);

export interface SteamEntry {
  appid: number;
  name: string;
  isShortcut: boolean;
  isLocal: boolean;
  remoteClientIds: string[];
}

export async function addShortcut(name: string, exe: string, startDir: string): Promise<number> {
  const appid = await SteamClient.Apps.AddShortcut(name, exe, startDir, "");
  // ZMIERZONE na SteamOS 3.x: AddShortcut IGNORUJE przekazaną nazwę i bierze ją z pliku
  // .exe (kafelek wychodził jako „007FirstLight.exe"). Tytuł jest w tym projekcie
  // tożsamością gry — bez tego wywołania rejestr i biblioteka mówią o czym innym.
  if (appid) SteamClient.Apps.SetShortcutName(appid, name);
  return appid;
}

/** Język interfejsu Steama — goły kod ISO albo null, gdy nie ma czym zapytać.
 *
 *  ZMIERZONE na Decku (pomiar w `docs/superpowers/specs/2026-08-22-wielojezycznosc-design.md`):
 *  `LocalizationManager.m_rgLocalesToUse` to `["pl"]`, czyli SYNCHRONICZNIE i dokładnie
 *  w formacie, którego potrzebujemy — to ta sama tablica, z której Steam wybiera swoje
 *  tokeny, więc jest autorytatywna dla języka interfejsu.
 *
 *  `SteamClient.Settings.GetCurrentLanguage()` też istnieje, ale zwraca `"polish"`,
 *  a `GetAvailableLanguages()` daje `strShortName: "schinese"` — czyli mapkę nazw
 *  własnych Valve dla każdego dokładanego języka. Świadomie nie używamy.
 *
 *  Zwraca `null`, a nie zapas, bo zapas należy do miejsca, które zna łańcuch
 *  rozstrzygania (dziś `metadata.ts`, po etapie A wielojęzyczności `i18n/`). `try` jest
 *  tu z tego samego powodu co w całym pliku: brakujące API Steama nie może zabić
 *  wtyczki. */
/** Czy ten appid to prawdziwa gra ze Steama (a nie skrót non-Steam — ani nasz, ani
 *  cudzy). ZMIERZONE: `app_type` to 1 dla gry Steama i 1073741824 dla skrótu.
 *  Dzięki temu karta informacyjna nie pyta sklepu o appidy skrótów, których sklep
 *  nigdy nie zna — jedno pytanie mniej na każde wejście na cudzy kafelek. */
export function isSteamGame(appid: number): boolean {
  const store = apps();
  const overview = store?.GetAppOverviewByAppID?.(appid);
  return overview?.app_type === 1;
}

export function steamLocale(): string | null {
  try {
    const list = (window as any).LocalizationManager?.m_rgLocalesToUse;
    return Array.isArray(list) && typeof list[0] === "string" ? list[0] : null;
  } catch {
    return null;
  }
}

export function setShortcutName(appid: number, name: string): void {
  SteamClient.Apps.SetShortcutName(appid, name);
}

/** Nazwa kafelka o tym appid albo null, gdy takiego kafelka nie ma (np. użytkownik go
 *  usunął). Sprawdzamy po appid, nie po nazwie: appid jest tym, co trzyma rejestr. */
export function nameOf(appid: number): string | null {
  const app = apps()?.GetAppOverviewByAppID?.(appid);
  return app ? String(app.display_name ?? "") : null;
}

export function setCompatTool(appid: number, tool: string): void {
  SteamClient.Apps.SpecifyCompatTool(appid, tool);
}

export function removeShortcut(appid: number): void {
  SteamClient.Apps.RemoveShortcut(appid);
}

export function setLaunchOptions(appid: number, options: string): void {
  SteamClient.Apps.SetShortcutLaunchOptions(appid, options);
}

/** Wszystkie wpisy biblioteki o dokładnie tej nazwie. Wpis lokalny ma clientid "0";
 *  wpis zdalny (skrót z drugiego urządzenia ogłoszony przez Steam) ma tylko id tego
 *  urządzenia i INNY appid — dlatego ukrycie po appid nie gasi kafelka lokalnego. */
export function entriesForTitle(title: string): SteamEntry[] {
  const wanted = title.trim().toLowerCase();
  const all = Array.from(apps()?.allApps ?? []) as any[];
  return all
    .filter((app) => (app.display_name ?? "").trim().toLowerCase() === wanted)
    .map((app) => {
      const ids = (Array.from(app.per_client_data ?? []) as any[]).map((c) => String(c.clientid));
      return {
        appid: app.appid,
        name: app.display_name,
        isShortcut: typeof app.BIsShortcut === "function" ? app.BIsShortcut() : false,
        isLocal: ids.includes("0"),
        remoteClientIds: ids.filter((id) => id !== "0"),
      };
    });
}

/** Ukrywanie jest lokalne — nie propaguje się na drugie urządzenie. */
export function setHidden(appid: number, hidden: boolean): void {
  const store = collections();
  // `typeof` zamiast gołego odczytu globalnej (ReferenceError), ale ciche pominięcie
  // byłoby awarią udającą sukces — wywołujący liczy nieudane kafelki i je pokazuje
  if (typeof store?.SetAppsAsHidden !== "function") throw new Error(t("log.steam_method_missing", { method: "collectionStore.SetAppsAsHidden" }));
  store.SetAppsAsHidden([appid], hidden);
}

export function isHidden(appid: number): boolean {
  return Boolean(collections()?.BIsHidden?.(appid));
}

// Typy grafik w Steamie: 0 = okładka pionowa, 1 = tło (hero), 2 = logo, 3 = okładka pozioma.
// Numeryczny enum ELibraryAssetType nie jest eksportowany z @decky/ui — stąd jedno rzutowanie.
const ASSET_TYPE = { grid_p: 0, hero: 1, logo: 2, grid_l: 3 } as unknown as Record<
  string,
  Parameters<typeof SteamClient.Apps.SetCustomArtworkForApp>[3]
>;

/** Ustawia grafiki kafelka. `toBase64` pobiera obraz po stronie backendu (frontend nie
 *  ma dostępu do sieci poza CEF), więc jedna nieudana grafika nie może przerwać reszty. */
export async function applyArtwork(
  appid: number,
  assets: Record<string, string>,
  toBase64: (url: string) => Promise<string>,
): Promise<string[]> {
  const failed: string[] = [];
  for (const [kind, url] of Object.entries(assets)) {
    const assetType = ASSET_TYPE[kind];
    if (assetType === undefined || !url) continue;
    try {
      const data = await toBase64(url);
      if (!data) throw new Error(t("ui.artwork_no_image"));
      await SteamClient.Apps.SetCustomArtworkForApp(appid, data, formatOf(url), assetType);
    } catch (err) {
      failed.push(`${kind}: ${err}`);
    }
  }
  return failed;
}

/** Rozszerzenie z adresu SGDB. Steam przyjmuje dokładnie "png" albo "jpg" (tyle
 *  dopuszcza sygnatura SetCustomArtworkForApp), a SGDB serwuje oba — sztywne "png"
 *  dla pliku JPEG było zgadywaniem. ZMIERZONE na Decku: „007 First Light" ma szeroką
 *  okładkę w .jpg i Steam zapisał ją jako `<appid>.jpg` — ze sztywnym "png" powstałby
 *  plik .png z zawartością JPEG. */
function formatOf(url: string): "png" | "jpg" {
  const ext = (url.split("?")[0].split(".").pop() ?? "").toLowerCase();
  return ext === "jpg" || ext === "jpeg" ? "jpg" : "png";
}

/** Przestawia ścieżkę pliku wykonywalnego kafelka.
 *
 *  Kafelek Steama trzyma ścieżkę NA SZTYWNO, a punkt montowania karty się zmienia:
 *  po przezwaniu karty („1281db6f-…" → „Karta 1") wszystkie jej kafelki wskazywały
 *  nieistniejący plik i gry przestały się uruchamiać (ZGŁOSZONE i ZMIERZONE na Decku:
 *  rejestr miał już nową ścieżkę i plik istniał, a `shortcuts.vdf` starą).
 *  Ta sama karta montuje się też różnie na różnych urządzeniach, więc jest to stan
 *  normalny, nie awaria — dlatego ścieżkę ustawiamy przy KAŻDYM skanie, bezwarunkowo.
 *
 *  `LaunchOptions` zostają nietknięte: użytkownik ma tam własne rzeczy
 *  (ZMIERZONE: „~/lsfg %command%" przy Split Fiction, „~/fgmod/fgmod" przy Gothicu). */
export function setShortcutPath(appid: number, exe: string, startDir: string): void {
  const client = SteamClient?.Apps;
  if (typeof client?.SetShortcutExe !== "function") {
    throw new Error(t("log.steam_method_missing", { method: "SteamClient.Apps.SetShortcutExe" }));
  }
  // ZMIERZONE na Decku i to jest asymetria, na której się przewróciłem: `AddShortcut`
  // sam otacza ścieżkę cudzysłowami (kafelki, które działają, mają w `shortcuts.vdf`
  // `Exe = "\"/run/.../gra.exe\""`), a `SetShortcutExe` zapisuje DOSŁOWNIE to, co
  // dostanie. Bez cudzysłowów ścieżka ze spacją („Karta 1", „Super Meat Boy 3D (2026)")
  // przestaje być jednym argumentem. Cudzysłowy dokładamy tylko, gdy ich nie ma —
  // podwójne byłyby tak samo złe.
  const quoted = exe.startsWith('"') ? exe : `"${exe}"`;
  client.SetShortcutExe(appid, quoted);
  // StartDir Steam trzyma bez cudzysłowów i z ukośnikiem na końcu (tak zapisał nasze
  // działające kafelki) — trzymamy tę samą formę
  const dir = startDir.endsWith("/") ? startDir : `${startDir}/`;
  client.SetShortcutStartDir?.(appid, dir);
}

/** Kolekcja Steama nazwana etykietą karty: gry z karty wjeżdżają, gry, których na
 *  niej już nie ma — wyjeżdżają.
 *
 *  Usuwamy WYŁĄCZNIE kafelki z naszego rejestru (`managed`). Gra ze Steama, którą
 *  użytkownik dorzucił do tej kolekcji ręcznie, nie jest naszą sprawą — wyrzucanie
 *  jej byłoby sprzątaniem w cudzej szufladzie.
 *
 *  ZMIERZONE na Decku (API jest nieudokumentowane, sygnatury są native, więc każdy
 *  krok sprawdzony osobno):
 *   - `NewUnsavedCollection(nazwa, null, [przeglądy])` — trzeci argument to LISTA GIER;
 *     wywołanie z samą nazwą rzuca „Cannot read properties of undefined (reading 'map')",
 *     a z listą w drugim argumencie tak samo;
 *   - `Save()` zwraca OBIETNICĘ i bez `await` kolekcja NIE pojawia się w
 *     `userCollections` — wygląda to jak cichy brak zapisu;
 *   - `AddApps`/`RemoveApps` przyjmują przeglądy gier, nie appidy;
 *   - `Delete()` też jest asynchroniczne. */
export async function syncCardCollection(
  name: string,
  onCard: number[],
  managed: number[],
): Promise<{ created: boolean; added: number; removed: number }> {
  const store = collections();
  if (typeof store?.NewUnsavedCollection !== "function") {
    throw new Error(t("log.steam_method_missing", { method: "collectionStore.NewUnsavedCollection" }));
  }
  const overview = (appid: number): any => apps()?.GetAppOverviewByAppID?.(appid);
  const present = onCard.map(overview).filter(Boolean);
  const existing = (store.userCollections ?? []).find((c: any) => c.displayName === name);
  if (!existing) {
    if (!present.length) return { created: false, added: 0, removed: 0 };
    const fresh = store.NewUnsavedCollection(name, null, present);
    await fresh.Save();
    return { created: true, added: present.length, removed: 0 };
  }
  if (existing.bIsEditable === false) {
    throw new Error(t("ui.collection_dynamic", { name }));
  }
  const inside = new Set<number>((existing.allApps ?? []).map((a: any) => a.appid));
  const wanted = new Set(onCard);
  const toAdd = present.filter((a: any) => !inside.has(a.appid));
  const toRemove = (existing.allApps ?? []).filter(
    (a: any) => managed.includes(a.appid) && !wanted.has(a.appid),
  );
  if (!toAdd.length && !toRemove.length) return { created: false, added: 0, removed: 0 };
  if (toAdd.length) existing.AddApps(toAdd);
  if (toRemove.length) existing.RemoveApps(toRemove);
  await existing.Save();
  return { created: false, added: toAdd.length, removed: toRemove.length };
}

/** Minuty, które STEAM naliczył temu kafelkowi (0, gdy nic nie wie).
 *
 *  Potrzebne przy przejmowaniu kafelka dodanego ręcznie: użytkownik ma tam
 *  historię (ZMIERZONE na Decku: „Gothic 1 Remake" 95,3 min), a nasz licznik
 *  startuje od zera — bez przejęcia tej liczby kafelek po przejęciu pokazałby
 *  0,0 min i wyglądałoby to na zgubiony czas. Liczba Steama siedzi w
 *  localconfig.vdf i żyje niezależnie od prefiksu, więc jest jedynym śladem
 *  sesji sprzed wtyczki. */
export function steamPlaytimeSeconds(appid: number): number {
  const raw = apps()?.GetAppOverviewByAppID?.(appid)?.minutes_playtime_forever;
  const minutes = parseFloat(String(raw ?? "0"));
  return Number.isFinite(minutes) && minutes > 0 ? Math.round(minutes * 60) : 0;
}

/** Nasze sumy, żywe MIĘDZY wywołaniami patchPlaytime — patch stora musi wiedzieć,
 *  co wstawić w przegląd, który Steam właśnie odtworzył z własnych danych. */
let ourSeconds: Record<string, number> = {};
let unpatchStore: (() => void) | null = null;

function applyOurs(overview: any): void {
  const seconds = ourSeconds[String(overview?.appid)];
  if (seconds === undefined) return;
  // SDH-PlayTime ustawia to TEKSTEM z jednym miejscem po przecinku — trzymamy kształt
  overview.minutes_playtime_forever = (seconds / 60).toFixed(1);
}

/** Trzyma naszą liczbę na kafelku na stałe.
 *
 *  Bez tego Steam po KAŻDYM własnym odświeżeniu przeglądu wracał do swojej liczby
 *  (ZMIERZONE: kafelek Animal Well pokazywał 2,8 min — czas jednej sesji ze Steama —
 *  choć wtyczka miała 428 s z dwóch urządzeń). Wyglądało to dokładnie jak „czas gry
 *  się nie sumuje", a suma była poprawna; migotał tylko podgląd.
 *
 *  Dwa punkty, oba jak w SDH-PlayTime (src/steam-ui/SteamPatches.ts):
 *  `appStore.m_mapApps.set` łapie przegląd dokładany do sklepu, a
 *  `AppOverview.InitFromProto` — przegląd odbudowywany w miejscu.
 *
 *  Odpięcie jest OBOWIĄZKOWE: przeładowanie wtyczki bez niego nakłada kolejną
 *  warstwę opakowań na te same metody (grabla „stare nasłuchy przeżywają
 *  przeładowanie" — tu skutkowałoby to łańcuchem wywołań rosnącym z każdym
 *  wdrożeniem). */
function patchStore(): void {
  if (unpatchStore) return;
  const store: any = typeof appStore === "undefined" ? null : appStore;
  const undo: Array<() => void> = [];
  try {
    const map = store?.m_mapApps;
    if (map && typeof map.set === "function") {
      const original = map.set;
      const own = Object.prototype.hasOwnProperty.call(map, "set");
      const patched = function (this: any, appid: any, overview: any) {
        // NASZA wartość idzie PO oryginale. ZMIERZONE na Decku: przy kolejności
        // odwrotnej `set` przywracał liczbę Steama (kafelek wracał do 2,8 min zamiast
        // trzymać naszą) — czyli oryginał dotyka przeglądu już po nas.
        const out = original.call(this, appid, overview);
        applyOurs(overview);
        return out;
      };
      (patched as any).__sdsync = true;   // znacznik do sprawdzenia z urządzenia
      map.set = patched;
      undo.push(() => { own ? (map.set = original) : delete map.set; });
    }
    // prototyp bierzemy z żywego przeglądu — klasy AppOverview nie ma w globalnych
    const sample = store?.allApps?.[0];
    const proto = sample ? Object.getPrototypeOf(sample) : null;
    if (proto && typeof proto.InitFromProto === "function") {
      const original = proto.InitFromProto;
      const patched = function (this: any, ...args: any[]) {
        const out = original.apply(this, args);
        applyOurs(this);
        return out;
      };
      (patched as any).__sdsync = true;
      proto.InitFromProto = patched;
      undo.push(() => { proto.InitFromProto = original; });
    }
  } catch (err) {
    // brak sklepu albo zmieniony kształt nie może zabić wtyczki — zostaje
    // dotychczasowe zachowanie (liczba poprawna do najbliższego odświeżenia)
    console.warn("NonSteam Sync: nie udało się zapatchować czasu gry w sklepie", err);
  }
  if (!undo.length) return;
  unpatchStore = () => {
    for (const step of undo.reverse()) {
      try { step(); } catch { /* Steam mógł już zniknąć */ }
    }
    unpatchStore = null;
  };
}

/** Zdejmuje patch stora. Woła to onDismount wtyczki. */
export function unpatchPlaytime(): void {
  unpatchStore?.();
  ourSeconds = {};
}

/** Podmienia czas gry na kafelkach Steama liczbą wtyczki (suma ze WSZYSTKICH
 *  urządzeń) i pilnuje, żeby tam została. Zwraca, ilu kafelków dotknęła.
 *
 *  Steam NIE MA API do zapisania czasu skrótu non-Steam — liczba siedzi w
 *  localconfig.vdf, który klient nadpisuje przy wyjściu. Dlatego podmieniamy wartość
 *  w obiekcie przeglądu i każemy sklepowi ogłosić zmianę. */
export function patchPlaytime(secondsByAppid: Record<string, number>): number {
  ourSeconds = { ...secondsByAppid };
  patchStore();
  const changed: any[] = [];
  for (const key of Object.keys(ourSeconds)) {
    const overview = apps()?.GetAppOverviewByAppID?.(Number(key));
    if (!overview) continue; // kafelek usunięty ręcznie — nie nasza sprawa
    applyOurs(overview);
    changed.push(overview);
  }
  // appInfoStore bywa jeszcze nieobecny, gdy Decky ładuje wtyczkę przed biblioteką;
  // sam odczyt niezadeklarowanej globalnej rzuca ReferenceError, a to zabiłoby start
  const store = typeof appInfoStore === "undefined" ? null : appInfoStore;
  if (changed.length) store?.OnAppOverviewChange?.(changed);
  return changed.length;
}

/** Rejestracje zdarzeń Steama. Wywołujący (events.ts) dostaje własne wywołania zwrotne
 *  i nie musi znać ani nazw metod, ani kształtu zdarzenia — inaczej zasada „steam.ts to
 *  jedyne miejsce dotykające API Steama" jest deklaracją, a nie faktem, i po zmianie
 *  nazw przez Valve trzeba szukać po całym froncie. Zwraca funkcję odrejestrowującą. */
export interface SteamEventHandlers {
  onStartupFinished: () => void;
  onConnectionStateUpdate: () => void;
  onInstallFolderChanges: () => void;
  /** `running=false` znaczy „gra właśnie się zakończyła". */
  onAppLifetime: (appid: number, running: boolean) => void;
  /** Deck idzie spać. Bez tego uśpienie z uruchomioną grą liczy się jako granie —
   *  ten sam wniosek ma SDH-PlayTime (src/app/middleware.ts). */
  onSuspend: () => void;
  onResume: () => void;
}

/** Rejestruje jedną subskrypcję albo zwraca null, gdy Steam takiej metody nie ma.
 *
 *  ZMIERZONE na Decku i BOLESNE: `SteamClient.System.RegisterForOnSuspendRequest` —
 *  nazwa wzięta z SDH-PlayTime — na tym SteamOS NIE ISTNIEJE. Wywołanie `undefined`
 *  rzucało TypeError w środku `registerEvents()`, przez co cała fabryka
 *  `definePlugin` padała: bez ekranu gier, bez `window.SDSync`, bez nasłuchu zdarzeń.
 *  Nieudokumentowane API Steama znika między wersjami, więc brak JEDNEJ metody może
 *  najwyżej wyłączyć JEDNĄ funkcję — nigdy całą wtyczkę. */
function subscribe(owner: any, method: string, handler: (...args: any[]) => void): any {
  const register = owner?.[method];
  if (typeof register !== "function") {
    console.warn(`NonSteam Sync: ${t("log.steam_method_unavailable", { method: `SteamClient.${method}` })}`);
    return null;
  }
  try {
    return register.call(owner, handler);
  } catch (err) {
    console.warn(`NonSteam Sync: SteamClient.${method} odmówiło rejestracji: ${err}`);
    return null;
  }
}

export function registerSteamEvents(handlers: SteamEventHandlers): () => void {
  const client = SteamClient as any;
  const subs = [
    subscribe(client.UI, "RegisterForStartupFinished", handlers.onStartupFinished),
    subscribe(client.System?.Network, "RegisterForConnectionStateUpdate", handlers.onConnectionStateUpdate),
    subscribe(client.InstallFolder, "RegisterForInstallFolderChanges", handlers.onInstallFolderChanges),
    subscribe(client.GameSessions, "RegisterForAppLifetimeNotifications", (event: any) =>
      handlers.onAppLifetime(event.unAppID, event.bRunning),
    ),
    // ZMIERZONE: nazw z SDH-PlayTime (`System.RegisterForOnSuspendRequest` /
    // `RegisterForOnResumeFromSuspend`) na tym urządzeniu nie ma. Te dwie są i to
    // one obsługują uśpienie; gdy zniknie także one, czas gry policzy noc na półce,
    // ale reszta wtyczki działa dalej.
    subscribe(client.User, "RegisterForPrepareForSystemSuspendProgress", handlers.onSuspend),
    subscribe(client.User, "RegisterForResumeSuspendedGamesProgress", handlers.onResume),
  ];
  return () =>
    subs.forEach((sub) => {
      try {
        sub?.unregister?.();
      } catch {
        // wyrejestrowanie nieudanej subskrypcji nie może wysypać odładowania wtyczki
      }
    });
}

/** Kandydaci na poziomą okładkę kafelka, w kolejności do wypróbowania.
 *
 *  ZMIERZONE na urządzeniu (SteamOS, 2026-08-20): `GetLandscapeImageURLForApp`,
 *  które jest w typach `@decky/ui` 4.12, na Decku NIE ISTNIEJE
 *  („appStore.GetLandscapeImageURLForApp is not a function"). Prawdziwy akcesor to
 *  `GetCustomLandcapeImageURLs` (literówka Valve w nazwie) i zwraca LISTĘ ścieżek
 *  (`.jpg` i `.png`) względem `https://steamloopback.host`. Na dysku leży tylko
 *  JEDNA z nich — dla naszych kafelków Steam zapisał `.jpg`, choć wysyłamy PNG —
 *  więc wywołujący musi próbować po kolei i przeżyć 404 na pierwszym.
 *  Pusta lista = gra nie ma własnej okładki (wtedy wiersz rysuje szare pole). */
export function landscapeArtwork(appid: number): string[] {
  return customArtwork(appid, "GetCustomLandcapeImageURLs");
}

/** Kandydaci na TŁO kafelka (grafika „hero"), w kolejności do wypróbowania.
 *
 *  ZMIERZONE na Decku 2026-08-22 (`Object.getOwnPropertyNames` na prototypie
 *  `appStore`): obok `GetCustomLandcapeImageURLs` istnieje `GetCustomHeroImageURLs`
 *  i zwraca listę w TYM SAMYM kształcie — `/customimages/<appid>_hero.jpg` oraz
 *  `…_hero.png`, wypełnioną dla kafelków, którym wgraliśmy grafiki (dla obcych pustą).
 *
 *  Na baner bierzemy JĄ, nie okładkę poziomą. ZGŁOSZONE z urządzenia: pozioma to
 *  kapsuła 460×215 z LOGIEM gry pośrodku, więc przycięcie jej do paska ucina logo
 *  w połowie. „Hero" jest z założenia tłem strony gry, więc znosi kadrowanie. */
export function heroArtwork(appid: number): string[] {
  return customArtwork(appid, "GetCustomHeroImageURLs");
}

/** Wspólne ciało obu akcesorów grafik. Przez `apps()`, nie przez gołe `appStore`:
 *  odczyt nieistniejącej globalnej rzuca ReferenceError, którego `?.` nie łapie
 *  (patrz komentarz przy `apps` na górze pliku). */
function customArtwork(appid: number, accessor: string): string[] {
  const store = apps();
  const app = store?.GetAppOverviewByAppID?.(appid);
  if (!app) return [];
  const urls = store?.[accessor]?.(app);
  return Array.isArray(urls) ? urls.map(String).filter(Boolean) : [];
}
