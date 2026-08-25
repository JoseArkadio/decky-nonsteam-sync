import { PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { LogEntry, logTail } from "../backend";
import { fromBackend, locale, t } from "../i18n";

const HOW_MANY = 12;

/** Log zdarzeń w panelu Quick Access.
 *
 *  Wcześniej był osobną pozycją w spisie na ekranie NonSteam Sync (etapu 5 ustalenie 7)
 *  i to był błąd, ZMIERZONY przez użytkownika na obu urządzeniach: treść tej strony
 *  to długa, nieogniskowalna lista, więc padem nie dało się z niej wyjść w górę do
 *  spisu — zaznaczenie zostawało w przewijanej liście zdarzeń. Do tego pozycja
 *  wciśnięta między „Nierozpoznane gry" a koniec spisu powodowała przeskakiwanie
 *  tej pierwszej. Log jest informacją, nie miejscem pracy, więc mieszka teraz tam,
 *  gdzie inne rzeczy do przeczytania: w panelu, nad Ustawieniami.
 *
 *  Pobiera przy każdym rozwinięciu — komponent montuje się dopiero wtedy, więc nie
 *  ma tu żadnego odpytywania w tle. */
export function EventLog() {
  const [log, setLog] = useState<LogEntry[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    logTail(HOW_MANY)
      .then(setLog)
      .catch((err) => {
        // „log się nie wczytał" i „nie ma zdarzeń" to dwa różne stany i nie wolno
        // ich zlać: log jest jedynym miejscem, gdzie widać przyczynę awarii
        setLog([]);
        setFailure(t("log.load_failed", {
          detail: err instanceof Error ? err.message : String(err),
        }));
      });
  }, []);

  if (log === null) {
    return (
      <PanelSectionRow>
        <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{t("log.loading")}</div>
      </PanelSectionRow>
    );
  }

  return (
    <>
      {failure && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", color: "#ff6b6b" }}>{failure}</div>
        </PanelSectionRow>
      )}
      {log.length === 0 && !failure && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{t("log.empty")}</div>
        </PanelSectionRow>
      )}
      {log.map((entry, index) => (
        <PanelSectionRow key={`${entry.ts}-${index}`}>
          <div
            style={{
              fontSize: "0.75em",
              lineHeight: 1.35,
              // panel jest wąski, a wiersze synchronizacji długie — bez zawijania
              // widać wyłącznie godzinę
              whiteSpace: "normal",
              overflowWrap: "anywhere",
              color: entry.kind === "error" ? "#ff6b6b" : undefined,
            }}
          >
            {new Date(entry.ts * 1000).toLocaleTimeString(locale())} · {entry.kind} ·{" "}
            {fromBackend(entry)}
          </div>
        </PanelSectionRow>
      ))}
    </>
  );
}
