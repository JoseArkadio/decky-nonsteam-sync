// CZWARTE (i na dziś ostatnie) miejsce dotykające nieudokumentowanego API Steama.
// Osobny plik, bo psuje się INACZEJ niż tamte trzy i naprawia się inaczej:
//   steam.ts            — Valve zmienia nazwę metody
//   steam-page-patch.tsx — Valve zmienia układ ekranu gry
//   steam-badges.tsx     — Valve zmienia DOM biblioteki
//   ten plik             — Valve zmienia własny magazyn danych sklepowych
//
// Po co w ogóle: zakładka „Informacje o grze" na ekranie gry ISTNIEJE dla kafelka
// non-Steam, ale jest pusta — ZMIERZONE, mówi wprost „Nie znaleziono opisu", bo Valve
// nie ma o naszej grze żadnych danych sklepowych. Mamy je z metadanych, więc
// wypełniamy JEGO zakładkę, zamiast rysować obok drugą.
//
// Jak to zmierzono (i dlaczego nie zgadujemy): podmiana `appDetailsStore.GetDescriptions`
// na urządzeniu zmieniła treść zakładki z „Nie znaleziono opisu" na nasz tekst, a
// podmiana `GetAssociations` sprawiła, że Steam SAM dorysował wiersze „Producent:"
// i „Wydawca:", których wcześniej tam nie było.
//
// Awaria jest tu z założenia CICHA i to jest wybór: nasza własna karta na tym samym
// ekranie pokazuje wszystko, co ta zakładka, i więcej (cztery urządzenia zgodności
// zamiast jednego). Gdy Valve zmieni te metody, użytkownik traci ozdobę, nie informację
// — dlatego zostaje ostrzeżenie w konsoli, a nie wpis w logu zdarzeń.
declare const appDetailsStore: any;
declare const SP_REACT: any;

export interface GameInfo {
  description: string;
  developers: string[];
  publishers: string[];
}

/** Dane naszych gier, po appidzie. Trzymane MODUŁOWO, a nie w domknięciu opakowania:
 *  dzięki temu odświeżenie danych to podmiana wpisu w tej mapie, a nie kolejna warstwa
 *  opakowań na metodach Steama — ta grabla kosztowała nas już raz przy czasie gry. */
let nasze: Record<string, GameInfo> = {};
let oryginalne: { opisy: any; asocjacje: any } | null = null;

const sklep = (): any => (typeof appDetailsStore === "undefined" ? null : appDetailsStore);
const react = (): any => (typeof SP_REACT === "undefined" ? null : SP_REACT);

/** Steam trzyma tu ELEMENTY Reacta, nie napisy (zmierzone: `{key, ref, props:
 *  {children}}` z symbolem `$$typeof`, którego nie widać w JSON-ie). Element budujemy
 *  Reactem STEAMA — jego renderer nie przyjąłby elementu z innej instancji. */
const element = (tekst: string) => react().createElement("div", null, tekst);

const osoba = (nazwa: string) => ({ strName: nazwa, strURL: "" });

export function patchGameInfo(byAppid: Record<string, GameInfo>): number {
  const store = sklep();
  if (!store || !react()) {
    console.warn("NonSteam Sync: brak appDetailsStore albo SP_REACT — zakładka Steama zostaje pusta");
    return 0;
  }
  nasze = byAppid;
  if (oryginalne) return Object.keys(nasze).length; // opakowania już stoją

  oryginalne = {
    opisy: store.GetDescriptions.bind(store),
    asocjacje: store.GetAssociations.bind(store),
  };
  store.GetDescriptions = (appid: number) => {
    const dane = nasze[String(appid)];
    if (!dane?.description) return oryginalne!.opisy(appid);
    const el = element(dane.description);
    return { strFullDescription: el, strSnippet: el };
  };
  store.GetAssociations = (appid: number) => {
    const dane = nasze[String(appid)];
    if (!dane) return oryginalne!.asocjacje(appid);
    const wlasne = oryginalne!.asocjacje(appid) || {};
    return {
      ...wlasne,
      rgDevelopers: dane.developers.map(osoba),
      rgPublishers: dane.publishers.map(osoba),
      rgFranchises: wlasne.rgFranchises || [],
    };
  };
  return Object.keys(nasze).length;
}

/** OBOWIĄZKOWE w sprzątaniu wtyczki: bez tego każde wdrożenie nakłada kolejną warstwę
 *  opakowań na te same metody Steama (ta sama zasada co `unpatchPlaytime`). */
export function unpatchGameInfo(): void {
  const store = sklep();
  if (store && oryginalne) {
    store.GetDescriptions = oryginalne.opisy;
    store.GetAssociations = oryginalne.asocjacje;
  }
  oryginalne = null;
  nasze = {};
}
