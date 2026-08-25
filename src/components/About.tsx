import { PanelSection, PanelSectionRow, SidebarNavigation } from "@decky/ui";
import { useEffect, useState } from "react";
import { about, AboutInfo } from "../backend";
import { aboutRoute } from "../selection";
import { RichText } from "./RichText";
import { SidebarColumnWidth } from "../steam-page-patch";
import { t } from "../i18n";
import { useLang } from "../i18n/resolve";

/** Kategorie w kolejności, w jakiej człowiek ich potrzebuje: najpierw „jak zacząć",
 *  potem co wtyczka robi, na końcu co zrobić, gdy nie działa.
 *
 *  Klucze haseł są WYLICZANE z tej nazwy (`ui.about.cat_X` / `ui.about.body_X`),
 *  tak samo jak w `stage.ts` — dołożenie kategorii to jeden wpis tutaj plus dwa
 *  hasła w KAŻDYM katalogu. Pilnuje tego `tests/test_i18n.py`, bo brakujące hasło
 *  w OBU katalogach naraz nie złamałoby testu na ich zgodność, a w interfejsie
 *  wypisałoby goły klucz. Nazwa jest też SEGMENTEM ADRESU (`aboutRoute`), więc
 *  wolno w niej tylko [a-z_]. */
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

/** Ekran „O wtyczce": co wtyczka robi, pogrupowane, plus tutoriale konfiguracji.
 *
 *  WŁASNY widok pod własną trasą, a nie pozycja w spisie gier. Spis gier odpowiada na
 *  „co z moimi grami", a to odpowiada na „co ta wtyczka w ogóle robi" — dwa różne
 *  pytania, więc dwa ekrany. Efekt uboczny, który jest tu wartością: kategorie nie
 *  muszą się zwijać, bo nie konkurują o miejsce z listą gier.
 *
 *  `SidebarNavigation` jest ROUTE-DRIVEN i to nie jest ozdoba: przy każdej zmianie
 *  strony robi `history.replace(route)`, więc trasa musi ISTNIEĆ w routerze, inaczej
 *  nawigacja wyprowadza z naszego ekranu i wszystko znika (ZMIERZONE na Decku przy
 *  ekranie gier — patrz AGENTS.md). Stąd `${ABOUT_ROUTE}/:cat?` w `index.tsx`
 *  i `aboutRoute()` na każdej pozycji. `page` NIE podajemy: trasa jest jedynym
 *  źródłem prawdy, a drugie rozjeżdżało się z nią przy wejściu bez parametru. */
export function About() {
  useLang(); // korzeń w osobnym drzewie React — bez tego napisy tu nie przerysują się
  const [info, setInfo] = useState<AboutInfo | null>(null);

  useEffect(() => {
    // wersja jest przy zgłaszaniu błędu najważniejszą pojedynczą informacją, ale jej
    // brak nie może zabrać treści ekranu — stąd cichy zapas w `version_unknown`
    about()
      .then(setInfo)
      .catch(() => undefined);
  }, []);

  const pages = KATEGORIE.map((nazwa) => ({
    title: t(`ui.about.cat_${nazwa}`),
    route: aboutRoute(nazwa),
    content: (
      // BEZ `title`: SidebarNavigation rysuje tytuł strony sam, więc `title` tutaj
      // dawał ten sam napis trzy razy pod rząd (pozycja w spisie, nagłówek strony,
      // nagłówek sekcji) — ZMIERZONE na zrzucie z Decka.
      <PanelSection>
        {nazwa === "start" && (
          <PanelSectionRow>
            {/* Blok wstępny jest ODDZIELONY kreską i wyraźnie większym odstępem od
                treści kategorii. Nie kosmetyka: bez tego wersja czytała się jako
                pierwsze zdanie tutoriala (ZMIERZONE na zrzucie z Decka). Odstęp
                MIĘDZY grupami musi być wyraźnie większy niż wewnątrz grupy. */}
            <div
              style={{
                fontSize: "0.9em",
                lineHeight: 1.45,
                opacity: 0.85,
                paddingBottom: "14px",
                marginBottom: "16px",
                borderBottom: "1px solid rgba(255,255,255,0.12)",
              }}
            >
              {t("ui.about.tagline")}
              <div style={{ fontSize: "0.85em", opacity: 0.6, marginTop: "8px" }}>
                {info
                  ? t("ui.about.version", { version: info.version, decky: info.decky })
                  : t("ui.about.version_unknown")}
              </div>
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          {/* Treść kategorii jest JEDNYM hasłem (osiem kategorii rozbitych na klucze
              per punkt to ~80 haseł w każdym katalogu zamiast 16), a strukturę —
              akapity, nagłówki grup, punkty, kroki — czyta z niego `RichText` po
              konwencjach, których hasła i tak już używały. Wcześniej było tu
              `white-space: pre-wrap`, czyli jeden ciąg bez żadnej hierarchii. */}
          <RichText tekst={t(`ui.about.body_${nazwa}`)} />
        </PanelSectionRow>
      </PanelSection>
    ),
  }));

  return (
    <>
      <SidebarColumnWidth />
      {/* disableRouteReporting: nasze trasy wstrzykuje Decky, Steam nie ma ich u siebie */}
      <SidebarNavigation title={t("ui.about.title")} pages={pages} disableRouteReporting />
    </>
  );
}
