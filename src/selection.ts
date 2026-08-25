// Kanał „otwórz ekran NonSteam Sync na TEJ grze". Wołany z sekcji na ekranie gry
// (GamePageSection), konsumowany przez SdSyncPage.
//
// ponytail: zamiast trasy z parametrem (/sdsync/:key) — zmienna modułowa plus
// powiadomienie. Parametr trasy wymagałby wygrzebania useParams z modułów Steama
// (react-router nie jest naszą zależnością), a jedynym wywołującym jest nasza sekcja
// z ekranu gry. Gdy pojawi się drugi wywołujący — wtedy trasa.
//
// ZMIERZONE na urządzeniu: sama zmienna NIE WYSTARCZA. Decky trzyma komponent trasy
// zamontowany, więc drugie wejście z ekranu gry nie odpala inicjalizatora useState
// i lista otwierała się na pierwszej grze zamiast na wybranej. Dlatego zamontowany
// ekran nasłuchuje zmiany.
let requested: string | null = null;
let requestedFocus = false;
const listeners = new Set<(titleKey: string) => void>();

/** Ustaw grę, na której ekran ma się otworzyć. Wołane PRZED Navigation.Navigate. */
export function selectGame(titleKey: string): void {
  requested = titleKey;
  requestedFocus = true;
  listeners.forEach((notify) => notify(titleKey));
}

/** Pobierz żądanie otwarcia i je skasuj — konsumowane raz, przy montowaniu ekranu.
 *  focus znaczy „otwórz z fokusem na pierwszej akcji" (wejście z ekranu gry). */
export function takeSelection(): { titleKey: string | null; focus: boolean } {
  const taken = { titleKey: requested, focus: requestedFocus };
  requested = null;
  requestedFocus = false;
  return taken;
}

/** Nasłuchuj kolejnych żądań (ekran pozostaje zamontowany — patrz komentarz wyżej). */
export function subscribeToSelection(notify: (titleKey: string) => void): () => void {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
}

// Trasy ekranu NonSteam Sync. Nie kosmetyka: SidebarNavigation Steama JEST route-driven
// (ZMIERZONE — zrzut źródła komponentu z urządzenia, patrz AGENTS.md), przy każdej
// zmianie strony robi `history.replace(route)`. Dlatego klucz gry musi być
// PRAWDZIWĄ trasą, którą router zna — inaczej ekran po prostu znika.
export const SDSYNC_ROUTE = "/sdsync";

/** Trasa strony jednej gry. `title_key` jest z [a-z0-9-] (registry.title_key), więc
 *  nadaje się do adresu bez kodowania. Strony nie-gier mają wiodący myślnik —
 *  title_key nigdy się tak nie zaczyna (`strip("-")`), więc kolizja jest niemożliwa. */
export const gameRoute = (titleKey: string): string => `${SDSYNC_ROUTE}/${titleKey}`;
