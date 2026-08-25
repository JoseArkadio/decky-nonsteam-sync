import { useEffect, useState } from "react";

import { GameMetaRecord, fetchMetadata, gameMetadata } from "./backend";
import { fromBackend } from "./i18n";
import { refreshGameInfo } from "./events";
import { steamLocale } from "./steam";

/** Metadane gry ze sklepu Steama dla jednego wpisu rejestru.
 *
 *  Jedno miejsce dla DWÓCH ekranów (nasza lista gier i sekcja na ekranie gry Steama),
 *  bo zasada „na żądanie, nie w tle" i rozstrzyganie języka muszą być takie same
 *  w obu — dwie kopie rozjechałyby się przy pierwszej zmianie.
 *
 *  Pobiera tylko wtedy, gdy dla TEGO języka nie ma nic zapamiętanego. Opis, data
 *  premiery i gatunki przychodzą przetłumaczone, więc przełączenie języka interfejsu
 *  jest powodem, żeby zapytać ponownie — a jedynym momentem sieci zostaje wejście na
 *  grę, nigdy przemiatanie biblioteki. */
/** Kod języka dla zapytania do sklepu: Steam, zapas ze standardu przeglądarki, `en`.
 *  Nadpisanie z ustawień dokłada backend — tylko on czyta `ui.json`. Po etapie A
 *  wielojęzyczności to wywołanie zamienia się na `locale()` z `i18n/`. */
const langCode = () =>
  (steamLocale() || navigator.languages?.[0] || navigator.language || "en")
    .slice(0, 2)
    .toLowerCase();

export function useGameMeta(titleKey: string | null) {
  const [meta, setMeta] = useState<GameMetaRecord | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (key: string, force: boolean) => {
    const lang = langCode();
    setBusy(true);
    setProblem(null);
    try {
      if (!force) {
        const zapamietane = await gameMetadata(key, lang);
        if (zapamietane && (zapamietane.name || zapamietane.missing)) {
          setMeta(zapamietane);
          return;
        }
      }
      const swieze = await fetchMetadata(key, lang);
      // Awaria NIE może wyglądać jak „gra bez opisu": to dwa różne stany i tylko przy
      // pierwszym warto próbować ponownie.
      if (swieze.error) setProblem(fromBackend(swieze.error));
      else {
        setMeta(swieze);
        // Świeży opis ma trafić także do zakładki „Informacje o grze" Steama, a nie
        // tylko na naszą kartę — inaczej ta zakładka pokazywałaby stan sprzed pobrania
        // do następnego uruchomienia.
        void refreshGameInfo().catch(() => undefined);
      }
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setMeta(null);
    setProblem(null);
    if (titleKey) void load(titleKey, false);
  }, [titleKey]);

  return {
    meta,
    problem,
    busy,
    refresh: () => {
      if (titleKey) void load(titleKey, true);
    },
  };
}
