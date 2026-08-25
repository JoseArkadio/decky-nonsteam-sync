import { useEffect, useState } from "react";

import { artworkBase64, artworkFor, setFlag } from "./backend";
import { applyArtwork, heroArtwork, landscapeArtwork } from "./steam";

/** Pobiera grafiki dla tytułu i wgrywa je na kafelek. Zwraca listę problemów
 *  (pusta = komplet).
 *
 *  Jedno miejsce dla obu wywołujących: skanu (nowa gra) i ekranu gier (gra już
 *  zarejestrowana). Wcześniej grafiki umiał tylko skan, więc klucz SGDB wpisany
 *  PO dodaniu gier nie miał jak niczego zmienić — a to wyglądało dokładnie jak
 *  „grafiki nie działają mimo klucza".
 */
export async function fetchAndApplyArtwork(titleKey: string, title: string, appid: number): Promise<string[]> {
  const { error, ...assets } = await artworkFor(title);
  const problems: string[] = error ? [error] : [];
  problems.push(...(await applyArtwork(appid, assets, artworkBase64)));
  // odnotowujemy dopiero komplet: „grafiki pobrane" przy połowie obrazków
  // kazałoby użytkownikowi szukać przyczyny w Steamie zamiast w SGDB
  const done = Object.keys(assets).length > 0 && problems.length === 0;
  await setFlag(titleKey, "artwork_done", done).catch(() => undefined);
  return problems;
}

/** Pozioma okładka kafelka (kapsuła 460×215) — do wiersza spisu.
 *
 *  `landscapeArtwork` zwraca LISTĘ kandydatów (`.jpg` i `.png`), a na dysku leży
 *  tylko jeden — dla naszych kafelków Steam zapisał `.jpg`, choć wysyłamy PNG
 *  (ZMIERZONE, patrz komentarz przy `landscapeArtwork` w `steam.ts`). Wywołujący
 *  MUSI więc przeżyć 404 na pierwszym i przejść do następnego; `src === null` znaczy
 *  „nie ma czego pokazać" i wtedy rysuje się zastępstwo, nie pusty `<img>`. */
export function useLandscape(appid: number | null): Candidate {
  return useCandidate(appid, appid ? landscapeArtwork(appid) : []);
}

/** Grafika na BANER szczegółów: najpierw tło („hero"), dopiero potem kapsuła.
 *
 *  Kolejność jest z pomiaru i ze zgłoszenia: kapsuła ma logo gry POŚRODKU, więc
 *  przycięta do paska traci je w połowie — „hero" jest tłem strony gry i kadrowanie
 *  znosi. Kapsuła zostaje jako zapas, bo kafelek może mieć jedno bez drugiego.
 *  Obie listy sklejamy w jedną kolejkę, więc reguła 404 przechodzi przez wszystkie
 *  cztery adresy (hero.jpg → hero.png → .jpg → .png), zanim powie „nie ma nic". */
export function useCover(appid: number | null): Candidate {
  return useCandidate(appid, appid ? [...heroArtwork(appid), ...landscapeArtwork(appid)] : []);
}

interface Candidate {
  src: string | null;
  onError: () => void;
}

/** Licznik prób zeruje się przy zmianie gry: bez tego wejście na grę bez okładki
 *  zostawiałoby `attempt` poza listą kandydatów następnej. */
function useCandidate(appid: number | null, candidates: string[]): Candidate {
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setAttempt(0), [appid]);
  return {
    src: candidates[attempt] ?? null,
    onError: () => setAttempt((index) => index + 1),
  };
}
