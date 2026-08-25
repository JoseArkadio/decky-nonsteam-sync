import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { about, AboutInfo } from "../backend";
import { t } from "../i18n";

/** Kategorie w kolejności, w jakiej człowiek ich potrzebuje: najpierw „jak zacząć",
 *  potem co wtyczka robi, na końcu co zrobić, gdy nie działa.
 *
 *  Klucze haseł są WYLICZANE z tej nazwy (`ui.about.cat_X` / `ui.about.body_X`),
 *  tak samo jak w `stage.ts` — dołożenie kategorii to jeden wpis tutaj plus dwa
 *  hasła w KAŻDYM katalogu. Pilnuje tego `tests/test_i18n.py`, bo brakujące hasło
 *  w OBU katalogach naraz nie złamałoby testu na ich zgodność, a w interfejsie
 *  wypisałoby goły klucz. */
export const KATEGORIE = [
  "start",
  "tiles",
  "saves",
  "cloud",
  "art",
  "info",
  "playtime",
  "trouble",
] as const;

/** Ekran „O wtyczce": co robi, pogrupowane, plus tutoriale konfiguracji.
 *
 *  Harmonijka, nie jedna długa strona, i to nie jest kwestia gustu. ZMIERZONE na
 *  urządzeniu przy logu zdarzeń (patrz komentarz w `EventLog.tsx`): długa treść bez
 *  celów zaznaczenia w pozycji spisu robi ślepy zaułek dla pada — zaznaczenie
 *  zostaje w przewijanej treści i nie ma jak wrócić w górę do spisu. Tutaj każda
 *  kategoria jest przyciskiem, czyli celem zaznaczenia, a otwarta jest najwyżej
 *  jedna, więc strona nigdy nie rośnie na tyle, żeby powrót wymagał przewijania. */
export function About() {
  const [otwarta, setOtwarta] = useState<string | null>("start");
  const [info, setInfo] = useState<AboutInfo | null>(null);

  useEffect(() => {
    // wersja jest tu najważniejszą pojedynczą informacją przy zgłaszaniu błędu,
    // ale jej brak nie może zabrać całej treści ekranu — stąd cichy zapas niżej
    about()
      .then(setInfo)
      .catch(() => undefined);
  }, []);

  return (
    <>
      <PanelSection title={t("ui.about.title")}>
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", lineHeight: 1.45, opacity: 0.85 }}>
            {t("ui.about.tagline")}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", opacity: 0.6 }}>
            {info
              ? t("ui.about.version", { version: info.version, decky: info.decky })
              : t("ui.about.version_unknown")}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t("ui.about.sections")}>
        {KATEGORIE.map((nazwa) => {
          const otwarte = otwarta === nazwa;
          return (
            <div key={nazwa}>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={() => setOtwarta(otwarte ? null : nazwa)}
                >
                  {(otwarte ? "▾ " : "▸ ") + t(`ui.about.cat_${nazwa}`)}
                </ButtonItem>
              </PanelSectionRow>
              {otwarte && (
                <PanelSectionRow>
                  {/* pre-wrap, bo treść kategorii jest JEDNYM hasłem z własnym
                      łamaniem wierszy: osiem kategorii rozbitych na osobne klucze
                      per punkt to ~80 haseł w każdym katalogu zamiast 16, a układ
                      i tak niesie sam tekst (• dla funkcji, 1. dla kroków) */}
                  <div
                    style={{
                      fontSize: "0.85em",
                      lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                      opacity: 0.9,
                    }}
                  >
                    {t(`ui.about.body_${nazwa}`)}
                  </div>
                </PanelSectionRow>
              )}
            </div>
          );
        })}
      </PanelSection>
    </>
  );
}
