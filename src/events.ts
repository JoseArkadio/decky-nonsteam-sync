import { toaster } from "@decky/api";
import {
  SyncResult,
  addPlaytime,
  cardBadges,
  getUiSettings,
  markRunning,
  playtimeByAppid,
  pushAfterGame,
  syncAll,
  tileMetadata,
} from "./backend";
import { fromBackend, t } from "./i18n";
import { patchPlaytime, registerSteamEvents, unpatchPlaytime } from "./steam";
import { patchGameInfo, unpatchGameInfo } from "./steam-game-info";
import { Naroznik, refreshBadges, stopBadges } from "./steam-badges";

/** Jedyna brama do sync_all w całym froncie. Ludusavi i rclone pracują na żywych
 *  zapisach użytkownika — dwa równoległe przebiegi mogłyby przywracać z katalogu,
 *  który drugi przebieg właśnie nadpisuje. Dlatego blokada jest tutaj, a nie u
 *  wywołujących: strona pluginu i zdarzenia Steama przechodzą tą samą drogą. */
let busy = false;
// Start liczy się jak zakończony przebieg: przy ZEROWEJ wartości `Date.now() - 0` jest
// zawsze większe od odstępu, więc KAŻDE załadowanie wtyczki przepuszczało natychmiastową
// synchronizację w tle. Wdrożenie, przeładowanie interfejsu Steama i aktualizacja Decky'ego
// robią to po kilka razy pod rząd.
let lastFinished = Date.now();

// Zdarzenie sieci sypie się seriami (każda zmiana stanu połączenia), a jeden przebieg
// synchronizacji to kilkanaście sekund wywołań Ludusavi. Blokada zapobiega nakładaniu,
// odstęp — mieleniu tego samego w kółko.
//
// ZMIERZONE na Steam Machine 2026-08-23: odstęp JEDNEJ minuty hamował operację trwającą
// KILKA minut, więc każde drgnięcie sieci startowało kolejny pełny przebieg. W logu
// czternaście przebiegów w 2,5 godziny (19:09, 19:19, 19:24, 19:37, 20:22, 20:28, 20:40,
// 20:44, 21:16, 21:20, 21:27, 21:32), wszystkie z IDENTYCZNYM wynikiem „nic do zrobienia",
// przy load average 3,21 i żywym rclone — konsola zacinała się użytkownikowi. Dzień
// wcześniej, przy tej samej sieci, były DWA przebiegi.
// Odstęp musi być większy od operacji, którą hamuje, a nie mniejszy: 15 minut.
// ponytail: jeden globalny odstęp dla wszystkich zdarzeń; gdyby doszły wyzwalacze,
// które muszą działać natychmiast, przekaż `cooldown = false` jak przy starcie.
const COOLDOWN_MS = 900_000;

/** Konflikty i awarie dostają WŁASNY komunikat — użytkownik musi je zobaczyć,
 *  nawet gdy w tym samym przebiegu coś się udało. */
function announce(reason: string, result: SyncResult): void {
  const problems = result.errors ?? [];
  if (problems.length) {
    toaster.toast({
      title: t("qa.toast_sync_error_title"),
      body: problems.map(fromBackend).join("; "),
      duration: 15000,
    });
  }
  if (result.conflicts?.length) {
    toaster.toast({
      title: t("qa.toast_conflict_title"),
      body: t("qa.toast_conflict_body", { games: result.conflicts.join(", ") }),
      duration: 15000,
    });
  }
  if (result.restored?.length) {
    toaster.toast({
      title: "NonSteam Sync",
      body: t("qa.toast_restored_body", { games: result.restored.join(", "), reason }),
    });
  }
}

/** Zwraca wynik albo `null`, gdy synchronizacja już trwa (wywołujący ma o tym
 *  powiedzieć użytkownikowi — „null" nie znaczy „nic do zrobienia").
 *  `titleKeys` = null → cała biblioteka; lista → tylko wskazane gry. */
export async function syncNow(reason: string, titleKeys: string[] | null = null): Promise<SyncResult | null> {
  if (busy) return null;
  busy = true;
  try {
    const result = await syncAll(titleKeys);
    announce(reason, result);
    return result;
  } finally {
    busy = false;
    lastFinished = Date.now();
  }
}

function background(reason: string, cooldown = true): void {
  if (cooldown && Date.now() - lastFinished < COOLDOWN_MS) return;
  syncNow(reason).catch((err) =>
    // awaria w tle nie ma gdzie wypłynąć poza tostem — cisza wyglądałaby jak „brak zmian"
    toaster.toast({
      title: t("qa.toast_sync_failed_title"),
      body: t("qa.toast_sync_failed_body", { reason, detail: err instanceof Error ? err.message : String(err) }),
      duration: 15000,
    }),
  );
}

// --- czas gry ---
// Steam nie liczy czasu skrótów non-Steam w sposób, który dałoby się odczytać i
// przewieźć na drugie urządzenie, więc mierzymy sami: od „gra wystartowała" do
// „gra się skończyła", z przerwą na uśpienie Decka (inaczej noc na półce wchodzi
// w statystykę). Ten sam podział ma SDH-PlayTime (src/app/SessionPlayTime.ts).
let session: { appid: number; startedAt: number } | null = null;
let runningAppid: number | null = null;

function openSession(appid: number): void {
  // start gry B bez zdarzenia końca gry A po cichu gubił cały odcinek A
  if (session && session.appid !== appid) void closeSession();
  session = { appid, startedAt: Date.now() };
}

/** Zamyka bieżący odcinek i dopisuje go w backendzie. Cicha porażka jest tu w
 *  porządku wyłącznie dlatego, że backend zapisuje ją w logu zdarzeń — czas gry
 *  to statystyka, a nie zapis użytkownika, i jego utrata nie może przerwać
 *  wysyłki zapisów, która idzie zaraz po niej. */
async function closeSession(): Promise<void> {
  const ended = session;
  session = null;
  if (!ended) return;
  const seconds = Math.round((Date.now() - ended.startedAt) / 1000);
  if (seconds <= 0) return; // zegar cofnięty przez NTP — nie odejmujemy czasu
  // Tania siatka pod każdą wersją błędu „sesja została otwarta": doba grania bez ani
  // jednego zdarzenia Steama jest mniej prawdopodobna niż zgubione zdarzenie końca.
  if (seconds > 24 * 3600) {
    console.warn(`NonSteam Sync: odcinek ${seconds}s dla appid ${ended.appid} odrzucony jako zawieszony`);
    return;
  }
  await addPlaytime(ended.appid, seconds);
}

/** Wciska nasze sumy w kafelki Steama. Wołane po każdej sesji i przy starcie —
 *  Steam po własnym odświeżeniu wraca do swojej liczby. */
export async function refreshPlaytime(): Promise<number> {
  return patchPlaytime(await playtimeByAppid());
}

/** Wypełnia zakładkę „Informacje o grze" Steama opisem i autorem naszych gier —
 *  dla kafelka non-Steam Valve nie ma tam nic i pisze „Nie znaleziono opisu".
 *  Wołane przy starcie i po każdym pobraniu metadanych; samo nie sięga sieci. */
export async function refreshGameInfo(): Promise<number> {
  // Pusty język znaczy „daj, co masz". Świadomie, nie z lenistwa: ta zakładka jest
  // Steama i ozdobna, a nasza karta obok pokazuje wszystko i tak — opis w nieco
  // starszym języku jest lepszym stanem niż zakładka pusta, która wygląda jak awaria.
  return patchGameInfo(await tileMetadata(""));
}

/** Kropki na kafelkach: zielona = karta z tą grą jest w urządzeniu, biała = grę
 *  obsługujemy, ale karty nie ma. Wołane tam, gdzie stan mógł się zmienić: przy
 *  starcie, po sesji, po zdarzeniu nośnika i przy wejściu do biblioteki. */
export async function refreshCardBadges(): Promise<number> {
  // Narożnik wybiera użytkownik: kafelek jest wspólny z innymi wtyczkami, a każda
  // rysuje swoje w innym rogu — tylko on wie, co mu się nakłada. Czytamy go przy
  // każdym odświeżeniu (mały plik JSON), żeby nie mieć drugiego źródła prawdy.
  const [stan, ui] = await Promise.all([
    cardBadges(),
    getUiSettings().catch(() => null),
  ]);
  return refreshBadges(stan, (ui?.badge_pos as Naroznik) ?? "bottom-right");
}

/** Kropki muszą zgasnąć ZARAZ po wyjęciu karty, a wyjęcie nie zawsze daje zdarzenie
 *  Steama (a `InstallFolderChanges` przychodzi z opóźnieniem albo wcale). Dlatego
 *  odpytujemy: `card_badges` to sam rejestr i `os.path.isfile`, bez Ludusavi i bez
 *  czytania karty, więc dziesięć sekund jest tu tanie. ZGŁOSZONE z urządzenia:
 *  „ikonki są, ale nie zmieniają koloru po wyjęciu karty". */
const BADGE_INTERVAL_MS = 10_000;
let badgeTimer: ReturnType<typeof setInterval> | null = null;

export function startBadgePolling(): void {
  if (badgeTimer) return;
  badgeTimer = setInterval(() => {
    refreshCardBadges().catch(() => undefined);
  }, BADGE_INTERVAL_MS);
}

export function stopBadgePolling(): void {
  if (badgeTimer) clearInterval(badgeTimer);
  badgeTimer = null;
}

async function afterGame(appid: number, running: boolean): Promise<void> {
  // Lokalna księgowość NIE może stać za awaitem na RPC: gdy markRunning padnie
  // (zerwany websocket Decky'ego), runningAppid zostałby na stałe, a otwarta sesja
  // dopisałaby grze przy najbliższym uśpieniu cały czas zegarowy od jej wyłączenia.
  if (running) {
    runningAppid = appid;
    openSession(appid);
  } else {
    runningAppid = null;
    await closeSession();
  }
  // dopiero teraz stan gry do backendu: SyncService sprawdza go tuż przed nadpisaniem
  // zapisów, więc ta informacja musi dotrzeć wcześniej niż jakikolwiek pull
  await markRunning(appid, running);
  if (running) return;
  refreshPlaytime().catch(() => undefined);
  refreshCardBadges().catch(() => undefined);
  refreshGameInfo().catch(() => undefined);
  startBadgePolling(); // kafelek to kosmetyka, nie blokuje wysyłki
  const result = await pushAfterGame(appid);
  if (!result.title) return; // nie nasza gra (albo appid poza rejestrem)
  if (result.ok && !result.conflict) {
    toaster.toast({ title: "NonSteam Sync", body: t("qa.toast_push_ok_body", { title: result.title }) });
    return;
  }
  const detail = result.error ? ` (${fromBackend(result.error)})` : "";
  toaster.toast({
    title: result.ok ? t("qa.toast_conflict_title") : t("qa.toast_push_failed_title"),
    body: result.ok
      ? t("qa.toast_push_conflict_body", { title: result.title })
      : t("qa.toast_push_failed_body", { title: result.title, detail }),
    duration: 15000,
  });
}

/** Zjada PIERWSZE wywołanie nasłuchu.
 *
 *  ZMIERZONE na Steam Machine: `RegisterForStartupFinished` i
 *  `RegisterForInstallFolderChanges` wołają callback **17 ms po rejestracji** —
 *  odtwarzają ostatnią wartość zamiast czekać na zmianę. Dla nas to znaczy, że KAŻDE
 *  załadowanie wtyczki (wdrożenie, aktualizacja Decky'ego, przeładowanie interfejsu
 *  Steama) odpalało pełną synchronizację. A po przeładowaniu zbiór uruchomionych gier
 *  w backendzie jest pusty, więc taki przebieg mógł przywrócić zapisy na grę, która
 *  właśnie chodzi (zasada 4 z AGENTS.md).
 *
 *  `RegisterForConnectionStateUpdate` w tym samym pomiarze NIE odtworzyło niczego, więc
 *  jego pierwszego wywołania nie zjadamy — byłaby to prawdziwa zmiana stanu sieci. */
function skipReplay(handler: () => void): () => void {
  let replayed = false;
  return () => {
    if (!replayed) {
      replayed = true;
      return;
    }
    handler();
  };
}

export function registerEvents(): () => void {
  const unregister = registerSteamEvents({
    onStartupFinished: skipReplay(() => background(t("qa.reason_startup"), false)),
    onConnectionStateUpdate: () => background(t("qa.reason_network")),
    onInstallFolderChanges: skipReplay(() => background(t("qa.reason_card"))),
    onAppLifetime: (appid, running) => {
      afterGame(appid, running).catch((err) =>
        toaster.toast({
          title: t("qa.toast_push_failed_title"),
          body: t("qa.toast_push_error_body", { appid, detail: err instanceof Error ? err.message : String(err) }),
          duration: 15000,
        }),
      );
    },
    onSuspend: () => {
      // uśpienie z uruchomioną grą to nie granie; odcinek zamykamy tu, a nie po
      // przebudzeniu, bo wtedy do sumy weszłaby cała noc na półce
      closeSession().catch(() => undefined);
    },
    onResume: () => {
      // RegisterForResumeSuspendedGamesProgress to zdarzenie POSTĘPU — przychodzi
      // serią. Bez warunku każde kolejne wywołanie zerowałoby startedAt i kasowało
      // cały dotychczasowy odcinek bieżącej sesji.
      if (runningAppid !== null && !session) openSession(runningAppid);
    },
  });

  // TU NIE MA pierwszego pulla i to jest decyzja, nie przeoczenie — stało tu
  // `background("załadowanie wtyczki", false)`. Powód ten sam, co przy `skipReplay`
  // wyżej: `_running_set` w main.py żyje w pamięci procesu wtyczki i wypełniają go
  // WYŁĄCZNIE zdarzenia `markRunning`, więc po przeładowaniu jest pusty i obie bramki
  // w `sync.py` (linie 103 i 165) przepuszczają przywrócenie na zapisy chodzącej gry.
  //
  // Wariant awaryjny narzucony przez plan naprawy (Zadanie 17, krok 2): dopóki nie ma
  // ZMIERZONEGO sposobu odczytania „które gry teraz chodzą" bez zdarzeń, wygoda ustępuje.
  // Zostaje: powrót sieci, PRAWDZIWA zmiana nośnika (włożenie karty), przycisk.
  refreshPlaytime().catch(() => undefined);
  refreshCardBadges().catch(() => undefined);
  refreshGameInfo().catch(() => undefined);
  startBadgePolling();

  return () => {
    // Decky przeładowuje wtyczkę przy KAŻDEJ aktualizacji. Bez tego „gram, wtyczka się
    // przeładowała, gram dalej godzinę" gubi całą godzinę i nie zostawia śladu w logu.
    closeSession().catch(() => undefined);
    unregister();
    stopBadgePolling();
    stopBadges();
    // Patch sklepu trzyma naszą liczbę na kafelku, ale MUSI zostać zdjęty: bez tego
    // każde wdrożenie nakłada kolejną warstwę opakowań na te same metody Steama.
    unpatchPlaytime();
    unpatchGameInfo();
  };
}
