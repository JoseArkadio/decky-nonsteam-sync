import { useEffect, useState } from "react";
import { syncStage } from "./backend";
import { CATALOGS, FALLBACK, langNow, t } from "./i18n";

/** Nazwy etapów z `sync.py` to KODY, nie napisy — tłumaczenie siedzi w katalogach
 *  pod `stage.<kod>`. Nieznany etap pokazujemy surowy: nowy etap w backendzie ma się
 *  pojawić w interfejsie, a nie zniknąć pod „Pracuję…". Pytamy KATALOG wprost (nie
 *  `t()`, które przy braku hasła oddaje sam klucz `stage.<kod>` — to też by przeszło
 *  test „nieznany", tylko brzydziej), więc lista etapów żyje w jednym miejscu:
 *  w plikach tłumaczeń, a nie duplikatem tutaj.*/
function isKnownStage(name: string): boolean {
  const key = `stage.${name}`;
  return key in (CATALOGS[langNow()] ?? {}) || key in (CATALOGS[FALLBACK] ?? {});
}

/** Etykieta przycisku w trakcie synchronizacji.
 *
 *  ZMIERZONE na Decku: przebieg trwa 20–120 s i prawie całość to czekanie rclone na
 *  chmurę — nie da się tego skrócić (pięć próbek `stan_chmury`: 17–29 s, rozrzut
 *  sieci ±6 s; zrównoleglenie podglądów nie dało nic dowodliwego). Skoro czasu nie
 *  ruszymy, to niech przynajmniej widać, na co się czeka.
 *
 *  Odpytywanie, nie zdarzenia: robota siedzi w wątku backendu, a jedno pytanie na
 *  sekundę jest tańsze niż zakładanie kanału zdarzeń dla jednego napisu. */
export function useSyncStage(busy: boolean): string {
  const [stage, setStage] = useState("");
  useEffect(() => {
    if (!busy) {
      setStage("");
      return;
    }
    let alive = true;
    const tick = () =>
      syncStage()
        .then((name) => alive && setStage(name))
        .catch(() => undefined); // brak etapu nie może zgasić przycisku
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [busy]);
  if (!stage) return t("qa.working");
  return isKnownStage(stage) ? t(`stage.${stage}`) : stage;
}
