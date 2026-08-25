/** Kropka na kafelku gry: ZIELONA, gdy karta z tą grą jest w urządzeniu, BIAŁA, gdy
 *  grę obsługujemy, ale jej karty nie ma w czytniku.
 *
 *  TRZECIE (i ostatnie) miejsce w tym projekcie dotykające nieudokumentowanego Steama,
 *  i celowo osobny plik: `steam.ts` psuje się, gdy Valve zmieni nazwę metody,
 *  `steam-page-patch.tsx` — gdy zmieni układ ekranu gry, a to psuje się, gdy zmieni
 *  DOM biblioteki. Trzy różne awarie, trzy różne naprawy.
 *
 *  Mechanizm zmierzony na Decku (tak samo robi to decky-nonsteam-badges, jedyna znana
 *  działająca droga):
 *   - widoczna biblioteka żyje w INNYM oknie CEF niż SharedJSContext, w którym biegnie
 *     wtyczka; okno znajdujemy przez `DFL.getGamepadNavigationTrees()` i wybieramy to,
 *     którego dokument ma `div[role="gridcell"]`;
 *   - patch trasy `/library` jest tylko WYZWALACZEM (React nie daje nam kafelka do
 *     opakowania), a robotę wykonuje MutationObserver po DOM;
 *   - React przemontowuje kafelki przy przewijaniu, więc kropkę trzeba dokładać
 *     ponownie — stąd obserwator, a nie jednorazowe przejście.
 *
 *  Awaria jest tu bezpieczna: brak kropek to brak ozdoby, nie utrata danych. Dlatego
 *  wszystko siedzi w try/catch — ten plik nie ma prawa wywalić wtyczki.
 */

const KLASA = "sdsync-badge";
const STYL_ID = "sdsync-badge-style";

export type Naroznik = "bottom-right" | "bottom-left" | "top-right" | "top-left" | "off";

/** Narożnik wybrany przez użytkownika w ustawieniach. Kod może go ODBIĆ w pionie dla
 *  konkretnego kafelka, gdy wybrany róg wypadłby w uciętej części (dopasujNaroznik). */
let naroznik: Naroznik = "bottom-right";

/** appid → stan kropki. Pusty zbiór = nic nie rysujemy. */
let stan: Record<string, "green" | "white"> = {};
let obserwator: MutationObserver | null = null;
let dokument: Document | null = null;

declare const DFL: any;

function bigPictureDocument(): Document | null {
  try {
    const trees = DFL?.getGamepadNavigationTrees?.();
    if (!trees) return null;
    for (const tree of trees) {
      const doc: Document | undefined = tree?.m_window?.document;
      if (doc?.querySelector('div[role="gridcell"], div[role="listitem"]')) return doc;
    }
  } catch {
    // brak DFL albo zamknięte okno — nie nasza sprawa
  }
  return null;
}

/** appid kafelka. Kolejność od najpewniejszego: propsy React niosą go wprost,
 *  `data-id` mają wirtualizowane listy ekranu głównego, a adres obrazka jest
 *  ostatnią deską ratunku (kafelek bez grafiki go nie ma). */
function appidOf(capsule: Element): string | null {
  const dataId = capsule.getAttribute("data-id");
  if (dataId && !dataId.startsWith("placeholder")) return dataId;
  try {
    for (const el of [capsule, ...Array.from(capsule.querySelectorAll("*"))]) {
      const klucz = Object.keys(el).find((k) => k.startsWith("__reactProps$"));
      const props = klucz ? (el as any)[klucz] : null;
      const kandydat =
        props?.appid ?? props?.app?.appid ?? props?.overview?.appid ?? props?.children?.props?.app?.appid;
      if (kandydat) return String(kandydat);
    }
  } catch {
    // kształt propsów Steama się zmienił — zostaje obrazek
  }
  const src = capsule.querySelector("img")?.getAttribute("src") ?? "";
  return src.match(/\/(\d{6,})[p._-]?[a-z]*\.(jpg|jpeg|png|webp)/i)?.[1] ?? null;
}

/** Ikonka karty SD. `currentColor` po to, żeby kolor brał się z klasy stanu —
 *  jeden kształt, dwa stany, zero duplikatu. */
const IKONA_SD =
  // Wypełniony kształt karty SD: korpus ze ściętym narożnikiem i cztery styki.
  // Rysowany u nas, a nie wzięty z Font Awesome — ich darmowe ikony wymagają
  // atrybucji (CC BY 4.0), a odtwarzanie cudzej ścieżki z pamięci to zgadywanie.
  // `currentColor` po to, żeby kolor brał się z klasy stanu: jeden kształt, dwa stany.
  '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">' +
  '<path d="M10 2.5h7.5A2.5 2.5 0 0 1 20 5v14a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 19V8.5z"' +
  ' fill="currentColor"/>' +
  '<g fill="rgba(0,0,0,.5)">' +
  '<rect x="10.4" y="4.4" width="1.4" height="3.4" rx=".7"/>' +
  '<rect x="12.9" y="4.4" width="1.4" height="3.4" rx=".7"/>' +
  '<rect x="15.4" y="4.4" width="1.4" height="3.4" rx=".7"/>' +
  '<rect x="17.9" y="6.2" width="0" height="0" rx=".7"/>' +
  '</g></svg>';

function wstawStyl(doc: Document): void {
  if (doc.getElementById(STYL_ID)) return;
  const styl = doc.createElement("style");
  styl.id = STYL_ID;
  // Forma wzięta z decky-nonsteam-badges (ZMIERZONA w jego bundlu): rozmyte,
  // prawie przejrzyste tło, zaokrąglony róg, wejście animacją. Nie z podobania się,
  // a po to, żeby dwa badge na jednym kafelku nie kłóciły się wyglądem.
  // Pozycja zależy od WIDOKU i to jest pomiar, nie gust. ZMIERZONE na Decku
  // (okno 985×616): na ekranie głównym kafelek karuzeli ma 253 px wysokości i sięga
  // y=643, a widoczny pasek kończy się na y=616 — dolne ~27 px jest poza widokiem.
  // Prawy dolny róg lądował dokładnie tam, więc ikonki „nie było". W siatce
  // biblioteki (`gridcell`) dolny róg jest widoczny i tam zostaje.
  // Na ekranie głównym idziemy w LEWY górny: prawy górny zajmuje badge
  // decky-nonsteam-badges (u niego y=394…430 na tym samym kafelku).
  // Rozmiar dobrany do sąsiada: decky-nonsteam-badges rysuje 36×36 px, a nasze
  // 19×19 było przy nim ledwo widoczne (ZGŁOSZONE, widać to na zrzucie z urządzenia).
  // `backdrop-filter: blur(10px)` ZOSTAJE i to jest sprawdzone, nie założone.
  // ZMIERZONE 2026-08-23 na Steam Machine (3840x2160): renderer Steama wywalał się
  // z SIGTRAP co ~80 s, Decky wstrzykiwał frontend od nowa (95 razy w jeden dzień
  // wobec 0 na Decku), CSS Loader raportował 1015 przekroczeń 5 s i cała konsola
  // stawała. Pierwsza diagnoza brzmiała „to rozmycie" i BYŁA BŁĘDNA — użytkownik
  // wskazał, że decky-nonsteam-badges rysuje na tym samym urządzeniu kropkę z
  // DOKŁADNIE tym samym `blur(10px)` i nie sprawia problemów. Kontrast z jej bundlem:
  // ma jedno `getBoundingClientRect` i jedno `requestAnimationFrame`, my mieliśmy dwa
  // odczyty NA KROPKĘ i zero sklejania.
  // Winna była więc KOŁOWROTKA, nie warstwa: przejście po kafelkach biegło po KAŻDEJ
  // mutacji DOM, a każda kropka wymuszała synchroniczne przeliczenie układu, więc
  // warstwy rozmycia powstawały i ginęły bez końca. Rozmycie kosztuje raz; tworzenie
  // go setki razy na sekundę kosztuje bez końca.
  // Lek jest niżej: jedno przejście na klatkę i jeden odczyt prostokąta na przejście.
  styl.textContent = `.${KLASA}{position:absolute;display:flex;
    align-items:center;justify-content:center;padding:5px;border-radius:5px;
    background:#0000002e;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
    z-index:9999;pointer-events:none;line-height:0}
    .${KLASA}[data-pos="bottom-right"]{bottom:6px;right:6px}
    .${KLASA}[data-pos="bottom-left"]{bottom:6px;left:6px}
    .${KLASA}[data-pos="top-right"]{top:6px;right:6px}
    .${KLASA}[data-pos="top-left"]{top:6px;left:6px}
    .${KLASA}[data-sdsync="green"]{color:#5ba32b}
    .${KLASA}[data-sdsync="white"]{color:#e9e9e9}
`;
  doc.head?.appendChild(styl);
}

/** Czarno-biała okładka = „karty nie ma". Filtr zakładamy na OBRAZEK kafelka, nie na
 *  kafelek: pod nim jest tytuł i nasza ikonka, a te mają zostać kolorowe. */
function przemaluj(capsule: Element, szare: boolean): void {
  const img = capsule.querySelector("img") as HTMLElement | null;
  if (!img) return;
  const chcemy = szare ? "grayscale(1)" : "";
  if (img.style.filter !== chcemy) img.style.filter = chcemy;
}

/** Odbija narożnik w pionie, gdy wybrany wypadłby POZA widocznym obszarem.
 *
 *  ZMIERZONE na Decku (okno 985×616): na ekranie głównym kafelek karuzeli sięga
 *  y=643, a widoczny pasek `ReactVirtualized__Grid` kończy się na y=616 — dolne
 *  ~27 px jest ucięte, więc ikonka w dolnym rogu BYŁA w DOM i była niewidoczna.
 *  Wybór użytkownika honorujemy wszędzie, gdzie się mieści; tylko tam, gdzie by
 *  zniknął, przechodzimy na przeciwną krawędź. Lepsze niż sztywna reguła „na home
 *  zawsze góra", bo działa też w widokach, których nie zmierzyliśmy. */
function dopasujNaroznik(znacznik: HTMLElement, b: DOMRect, o: DOMRect): void {
  try {
    const teraz = znacznik.getAttribute("data-pos") || "";
    if (b.bottom > o.bottom && teraz.startsWith("bottom")) {
      znacznik.setAttribute("data-pos", teraz.replace("bottom", "top"));
    } else if (b.top < o.top && teraz.startsWith("top")) {
      znacznik.setAttribute("data-pos", teraz.replace("top", "bottom"));
    }
  } catch {
    // kafelek właśnie odmontowany — zostaje wybrany narożnik
  }
}

function oznacz(doc: Document): number {
  // Kropki wstawione w tym przejściu. Geometrię czytamy DOPIERO PO wszystkich zapisach
  // (niżej), bo przeplatanie „zapisz kropkę / odczytaj układ" wymusza synchroniczne
  // przeliczenie układu przy KAŻDEJ kropce. ZMIERZONE na Steam Machine (3840x2160): tak
  // ułożone przejście trzymało wątek JS zajęty na tyle, że CSS Loader raportował ~12
  // przekroczeń 5 s na minutę. Kontrast: decky-nonsteam-badges ma na całą wtyczkę
  // JEDEN `getBoundingClientRect` i jedno `requestAnimationFrame` — i na tym samym
  // urządzeniu, z tym samym `blur(10px)`, nie sprawia problemów.
  const doSprawdzenia: HTMLElement[] = [];
  let ile = 0;
  for (const capsule of Array.from(doc.querySelectorAll('div[role="gridcell"], div[role="listitem"]'))) {
    const appid = appidOf(capsule);
    const stanGry = appid ? stan[appid] : undefined;
    const istniejaca = capsule.querySelector(`.${KLASA}`);
    if (!stanGry) {
      if (istniejaca) {
        // gra wypadła z rejestru — cofamy WSZYSTKO, co zrobiliśmy temu kafelkowi
        przemaluj(capsule, false);
        istniejaca.remove();
      }
      continue;
    }
    przemaluj(capsule, stanGry === "white");
    // Celem jest pudełko OKŁADKI, nie cały kafelek. ZMIERZONE na Decku (okno 985×616,
    // ekran główny): kafelek `listitem` obejmuje też miejsce na tytuł pod okładką
    // i sięga y=643, a widoczny pasek kończy się na y=611 — dolne 32 px jest ucięte.
    // Pudełko okładki to y=390…591, czyli całe w widoku. Dlatego badge przypięty do
    // kafelka „ginął" w dolnym rogu, a ten sam róg na okładce jest widoczny (tak samo
    // celuje decky-nonsteam-badges). Element musi też być POZYCJONOWANY, inaczej
    // ikonka ucieka na krawędź ekranu.
    const obrazek = capsule.querySelector("img");
    const cel = ((obrazek && obrazek.parentElement) ??
      capsule.querySelector("div") ?? capsule) as HTMLElement;
    if (getComputedStyle(cel).position === "static") cel.style.position = "relative";
    const pozycja = naroznik;
    if (istniejaca) {
      // React przemontowuje kafelki przy przewijaniu — ikonka może wisieć przy CUDZYM
      // elemencie, mieć nieaktualny stan albo pozycję z innego widoku
      if (
        istniejaca.parentElement === cel &&
        istniejaca.getAttribute("data-sdsync") === stanGry &&
        istniejaca.getAttribute("data-pos") === pozycja
      ) {
        continue;
      }
      istniejaca.remove();
    }
    const znacznik = doc.createElement("div");
    znacznik.className = KLASA;
    znacznik.setAttribute("data-sdsync", stanGry);
    znacznik.setAttribute("data-pos", pozycja);
    znacznik.innerHTML = IKONA_SD;
    cel.appendChild(znacznik);
    doSprawdzenia.push(znacznik);
    ile += 1;
  }
  // FAZA ODCZYTU: wszystkie prostokąty naraz, więc układ liczy się RAZ, nie N razy.
  const pary: Array<[HTMLElement, DOMRect, DOMRect]> = [];
  const obcinacze = new Map<Element, DOMRect>();
  for (const znacznik of doSprawdzenia) {
    const obcinacz = znacznik.closest("[class*=ReactVirtualized]");
    if (!obcinacz) continue;
    let o = obcinacze.get(obcinacz);
    if (!o) { o = obcinacz.getBoundingClientRect(); obcinacze.set(obcinacz, o); }
    pary.push([znacznik, znacznik.getBoundingClientRect(), o]);
  }
  // FAZA ZAPISU: dopiero teraz przestawiamy narożniki.
  for (const [znacznik, b, o] of pary) dopasujNaroznik(znacznik, b, o);
  return ile;
}

/** Ile kropek dorysowano przy tym przejściu (do sprawdzenia z urządzenia). */
export function refreshBadges(
  byAppid: Record<string, "green" | "white">,
  gdzie: Naroznik = "bottom-right",
): number {
  stan = { ...byAppid };
  naroznik = gdzie;
  if (gdzie === "off") {
    stopBadges(); // wybór „nie pokazuj" musi też ZDJĄĆ to, co już wisi
    return 0;
  }
  const doc = bigPictureDocument();
  if (!doc) return 0;
  dokument = doc;
  wstawStyl(doc);
  const ile = oznacz(doc);
  if (!obserwator) {
    // Interfejs Steama mutuje seriami — bez sklejenia `oznacz` biegło po KAŻDEJ
    // mutacji, wielokrotnie w jednej klatce. Jedno przejście na klatkę wystarcza:
    // wcześniej niż następne odrysowanie nikt kropki i tak nie zobaczy.
    let zaplanowane = 0;
    obserwator = new MutationObserver(() => {
      if (zaplanowane) return;
      zaplanowane = requestAnimationFrame(() => {
        zaplanowane = 0;
        try {
          oznacz(doc);
        } catch {
          // pojedyncze nieudane przejście nie może ubić obserwatora
        }
      });
    });
    obserwator.observe(doc.body, { childList: true, subtree: true });
  }
  return ile;
}

/** Czarno-biały ekran gry, gdy karty nie ma.
 *
 *  Robimy to REGUŁĄ CSS, nie ustawianiem `style.filter` na znalezionych elementach.
 *  ZMIERZONE na urządzeniu: tło ekranu gry to `<img>`, który dochodzi do DOM PÓŹNIEJ
 *  niż nasz panel — jednorazowe przejście po elementach nie zastawało go i efekt po
 *  cichu nie działał (zero przemalowanych elementów przy widocznym panelu). Reguła
 *  CSS obejmuje też to, co pojawi się potem, więc nie trzeba tu obserwatora.
 *
 *  Celujemy po APPID W ADRESIE grafiki: klasy Steama są zahaszowane i zmieniają się
 *  między wersjami, a `/customimages/<appid>_hero.png` nie. Dzięki temu szarzeje
 *  dokładnie ta jedna gra.
 */
export function greyGamePage(appid: number, szare: boolean): void {
  const doc = typeof document === "undefined" ? null : document;
  if (!doc) return;
  const id = `sdsync-grey-${appid}`;
  const stary = doc.getElementById(id);
  if (!szare) {
    stary?.remove();
    return;
  }
  if (stary) return;
  try {
    const styl = doc.createElement("style");
    styl.id = id;
    // `!important`, bo Steam ustawia na tych elementach własne filtry (rozmycie tła)
    styl.textContent =
      `img[src*="/customimages/${appid}"] { filter: grayscale(1) !important; }`;
    doc.head?.appendChild(styl);
  } catch {
    // brak head albo zamknięte okno — szarość jest ozdobą, nie może nic wywalić
  }
}

/** Zdjęcie kropek i obserwatora. Bez tego przeładowanie wtyczki zostawia obserwator
 *  na cudzym dokumencie — a te przeżywają przeładowanie (grabla z AGENTS.md). */
export function stopBadges(): void {
  try {
    obserwator?.disconnect();
    obserwator = null;
    // filtry zdejmujemy PRZED usunięciem kropek: po ich usunięciu nie mamy już jak
    // rozpoznać, które kafelki były nasze
    dokument?.querySelectorAll('div[role="gridcell"], div[role="listitem"]').forEach((c) => {
      if (c.querySelector(`.${KLASA}`)) przemaluj(c, false);
    });
    dokument?.querySelectorAll(`.${KLASA}`).forEach((b) => b.remove());
    dokument?.getElementById(STYL_ID)?.remove();
    dokument = null;
    stan = {};
  } catch {
    // okno mogło już zniknąć
  }
}
