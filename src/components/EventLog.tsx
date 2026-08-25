import { DialogButton, Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { FaSync } from "react-icons/fa";
import { LogEntry, logTail } from "../backend";
import { fromBackend, locale, t } from "../i18n";
import { useLang } from "../i18n/resolve";

/** Ile wpisów pokazujemy. Log jest plikiem, który rośnie bez końca, więc jakiś kres
 *  być musi — ale MÓWIMY o nim w interfejsie (`ui.events.showing`). Milcząco przycięta
 *  lista czyta się jak „tyle było zdarzeń", a to nieprawda. */
const ILE = 60;

/** Ekran „Zdarzenia" — dziennik wszystkiego, co wtyczka zrobiła.
 *
 *  Trzecia lokalizacja tego widoku i warto wiedzieć, czemu poprzednie dwie nie
 *  wystarczyły. Najpierw był pozycją w spisie na ekranie gier: ZMIERZONE na obu
 *  urządzeniach, że długa nieogniskowalna lista wciśnięta między „Nierozpoznane gry"
 *  a koniec spisu robiła ślepy zaułek dla pada — zaznaczenie zostawało w przewijanej
 *  treści i nie było jak wrócić w górę. Potem był rozwijaną sekcją w panelu Quick
 *  Access, co ten problem usuwało, ale panel jest wąski, a wiersze synchronizacji
 *  długie: każdy wpis zawijał się na trzy linie. ZGŁOSZONE: „przycisk »Zdarzenia«
 *  też mógłby mieć osobny widok".
 *
 *  Dziś jest osobną trasą, więc ma pełną szerokość, a wyjście jest przyciskiem B —
 *  nie ma spisu, do którego trzeba by wracać w górę. Co z tego zostaje: KAŻDY wiersz
 *  jest `Focusable` z `onActivate`, bo lista bez ani jednego celu zaznaczenia znów
 *  byłaby ślepym zaułkiem, a `Focusable` BEZ `onActivate` celem nie jest (renderuje
 *  się z `tabIndex === -1` — zmierzone). Wiersze nie są w ogniskowalnym RODZICU
 *  z tej samej przyczyny: rodzic przyjmujący zaznaczenie jest jedynym przystankiem
 *  w swoim poddrzewie. */
export function EventsPage() {
  useLang(); // korzeń w osobnym drzewie React — bez tego napisy się nie przerysują
  const [log, setLog] = useState<LogEntry[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      setLog(await logTail(ILE));
      setFailure(null);
    } catch (err) {
      // „log się nie wczytał" i „nie ma zdarzeń" to dwa różne stany i nie wolno ich
      // zlać: log jest jedynym miejscem, gdzie widać przyczynę awarii
      setLog([]);
      setFailure(t("log.load_failed", {
        detail: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    // Odstęp od góry jest KONIECZNY, nie kosmetyczny. Trasa bez SidebarNavigation nie
    // dostaje od Steama żadnego offsetu: ZMIERZONE na Decku, nagłówek „Events"
    // renderował się na `top: 0`, czyli SCHOWANY pod paskiem Steama, a pierwszy wiersz
    // treści zaczynał się na 41 px. Pasek ma stałe 40 px na Decku i na Machine (to
    // wysokość, nie proporcja — zmierzone na obu), więc 48 px kryje go z zapasem.
    <div style={{ paddingTop: "48px" }}>
      {/* Z `title`: tu NIE ma SidebarNavigation, które rysowałoby nagłówek samo (jak
          na ekranie „O wtyczce"), więc bez tego ekran byłby bez tytułu. */}
      <PanelSection title={t("ui.events.title")}>
      <PanelSectionRow>
        <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "6px" }}>
          <div style={{ flex: 1, fontSize: "0.85em", opacity: 0.6 }}>
            {log === null
              ? t("log.loading")
              : t("ui.events.showing", { count: log.length, cap: ILE })}
          </div>
          <div style={{ flexShrink: 0, minWidth: "150px" }}>
            <DialogButton disabled={busy} onClick={() => void load()}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
                <FaSync /> {t("ui.events.refresh")}
              </span>
            </DialogButton>
          </div>
        </div>
      </PanelSectionRow>

      {failure && (
        <PanelSectionRow>
          <div style={{ color: "#ff6b6b", fontSize: "0.9em" }}>{failure}</div>
        </PanelSectionRow>
      )}

      {log !== null && log.length === 0 && !failure && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.9em", opacity: 0.7 }}>{t("log.empty")}</div>
        </PanelSectionRow>
      )}

      {(log ?? []).map((entry, index) => (
        <PanelSectionRow key={`${entry.ts}-${index}`}>
          {/* onActivate pusty, ale OBOWIĄZKOWY: bez niego Focusable ma tabIndex -1
              i pad nie ma tu ani jednego przystanku, czyli nie da się przewinąć
              listy ani z niej wyjść. Wiersz nic nie robi — jest do czytania. */}
          <Focusable onActivate={() => undefined}>
            <div
              style={{
                display: "flex",
                gap: "12px",
                alignItems: "baseline",
                padding: "7px 10px",
                borderRadius: "4px",
                fontSize: "0.88em",
                lineHeight: 1.45,
                // błąd musi być widoczny kątem oka, nie po przeczytaniu wiersza
                background: entry.kind === "error" ? "rgba(255,107,107,0.10)" : "rgba(255,255,255,0.03)",
              }}
            >
              <div
                style={{
                  flexShrink: 0,
                  // ZMIERZONE: przy 5.5em kolumna miała 77 px i „8:48:06 AM" zawijało
                  // się na dwie linie (wysokość wiersza 41 px zamiast ~20). Format
                  // 12-godzinny z AM/PM jest dłuższy od polskiego „10:13:00", więc
                  // liczbę bierzemy z tego dłuższego. `nowrap` jest zabezpieczeniem
                  // na język, którego nie zmierzyliśmy.
                  width: "7em",
                  whiteSpace: "nowrap",
                  opacity: 0.55,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {new Date(entry.ts * 1000).toLocaleTimeString(locale())}
              </div>
              <div
                style={{
                  flexShrink: 0,
                  width: "5em",
                  fontSize: "0.85em",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  opacity: 0.5,
                  color: entry.kind === "error" ? "#ff8f8f" : undefined,
                }}
              >
                {entry.kind}
              </div>
              <div style={{ overflowWrap: "anywhere", opacity: 0.9 }}>{fromBackend(entry)}</div>
            </div>
          </Focusable>
        </PanelSectionRow>
      ))}
      </PanelSection>
    </div>
  );
}
