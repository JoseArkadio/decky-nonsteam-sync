import { useEffect, useState } from "react";
import { getUiSettings } from "../backend";
import { steamLocale } from "../steam";
import { langNow, normalize, onLangChange, setLang } from "./index";

/** Kandydaci dostępni SYNCHRONICZNIE, w kolejności zaufania.
 *
 *  `navigator.languages` (ZMIERZONE: `["pl-PL","pl","en-US","en"]`) jest zapasem na
 *  wypadek, gdyby `LocalizationManager` zniknął z przyszłej wersji Steama — standard
 *  przeglądarki nie zniknie i nie potrzebuje mapki. NIEZMIERZONE, czy idzie za językiem
 *  interfejsu Steama, czy za locale systemu; przy nieobecnym `LocalizationManager`
 *  „locale systemu" jest i tak lepszym zgadnięciem niż `en`, a człowiek ma nadpisanie. */
export function fromEnvironment(): string | null {
  const candidates = [steamLocale(), ...(navigator.languages ?? [navigator.language])];
  for (const candidate of candidates) {
    const code = normalize(candidate);
    if (code) return code;
  }
  return null;
}

// Synchronicznie, PRZY ŁADOWANIU MODUŁU: pierwsze rysowanie ma już język Steama,
// więc przy domyślnym „auto" nie ma żadnego przemrugnięcia napisów.
setLang(fromEnvironment());

let started: Promise<void> | null = null;

/** Dociąga nadpisanie z `ui.json`. Obietnica na poziomie MODUŁU, nie `useRef`:
 *  komponent strony montuje się DWA razy (zmierzone), a każde montowanie ma własny
 *  ref — więc ref nie chroni przed dwoma wywołaniami RPC. */
export function startLanguageResolution(): Promise<void> {
  if (!started) {
    started = getUiSettings()
      .then((ui) => {
        if (ui?.lang && ui.lang !== "auto") setLang(ui.lang);
      })
      .catch(() => undefined); // brak ustawień = zostaje język ze środowiska
  }
  return started;
}

/** Wymusza jedno przerysowanie, gdy język się rozstrzygnie. Wołają to TRZY korzenie:
 *  panel Quick Access, ekran `/sdsync` i sekcja na ekranie gry. Komponenty niżej
 *  wołają `t()` wprost — przerysowanie korzenia załatwia całe drzewo. */
export function useLang(): string {
  const [, bump] = useState(0);
  useEffect(() => {
    startLanguageResolution();
    return onLangChange(() => bump((n) => n + 1));
  }, []);
  return langNow();
}
