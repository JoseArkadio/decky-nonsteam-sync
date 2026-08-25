import { routerHook } from "@decky/api";
import { afterPatch, wrapReactType } from "@decky/ui";
import { ReactElement } from "react";
import { GamePageSection } from "./components/GamePageSection";
import { t } from "./i18n";

// DRUGIE (obok steam.ts) i OSTATNIE miejsce dotykające nieudokumentowanego Steama.
// Tamten plik psuje się, gdy Valve zmieni nazwę metody; ten — gdy zmieni układ ekranu
// gry albo klasy CSS swoich komponentów. Wzorzec zdjęty z hulkrelax/hltb-for-deck
// (ostatnia zmiana XII.2023, komentarz autora nad tą funkcją: „I hate this method"),
// dlatego zawartość sekcji jest minimalna, a porażka WIDOCZNA: wpis w logu zdarzeń
// i linia w panelu Quick Access.
const MARKER = "sdsync-game-section";

/** Poszerzenie kolumny spisu w SidebarNavigation (nasz ekran gier). Kolumna ma w
 *  Steamie min-width: 240px, a komponent nie przyjmuje ŻADNEJ szerokości — więc
 *  celujemy w klasę CSS Steama `.PageListColumn` zawierającą nasze wiersze
 *  (oznaczone atrybutem data-sdsync-item w GameListItem); reszta interfejsu
 *  zostaje nietknięta. `:has()` sprawdzone na urządzeniu. Gdy Valve zmieni nazwę
 *  klasy, lista po prostu wraca do wąskiej kolumny — tytuły się zawijają, nic
 *  się nie psuje na twardo. */
export function SidebarColumnWidth() {
  return <style>{`.PageListColumn:has([data-sdsync-item]) { min-width: 34vw !important; }`}</style>;
}

let failure: string | null = null;
/** Gra, której ekran właśnie się rysuje. Ustawia ją zewnętrzny patch przy KAŻDYM
 *  renderze — patrz komentarz przy nim. */
let biezacyAppid = 0;

/** Komunikat o nieudanym wstrzyknięciu albo null. Panel Quick Access to pokazuje —
 *  cisza wyglądałaby jak „nie ma nic do pokazania" (zasada nr 1 z AGENTS.md). */
export function getPatchFailure(): string | null {
  return failure;
}

export function patchGamePage(onFailure: (message: string) => void): () => void {
  const patch = routerHook.addPatch(
    "/library/app/:appid",
    (props: { path: string; children: ReactElement }) => {
      afterPatch(props.children.props, "renderFunc", (_: any[], ret1: ReactElement) => {
        const appid: number = (ret1 as any)?.props?.children?.props?.overview?.appid;
        if (!appid) return ret1;
        // Numer gry idzie przez zmienną MODUŁOWĄ, a nie przez domknięcie wewnętrznego
        // patcha — i to jest poprawka błędu, nie ozdoba.
        //
        // ZMIERZONE na Decku: po przejściu z gry Steama na naszą karta pokazywała
        // POPRZEDNIĄ grę (jedna sekcja w DOM, `#sdsync-game-section`, z treścią
        // „112 Operator" na ekranie Marvel Tōkon). `wrapReactType` opakowuje typ tylko
        // RAZ, więc każda kolejna nawigacja dokłada do niego kolejny `afterPatch`,
        // a każdy z nich niesie appid z chwili SWOJEJ rejestracji. Wszystkie się
        // wykonują i o wyniku decyduje ten, który biegnie ostatni — czyli najstarszy.
        // Zewnętrzny patch odpala się przy każdym renderze ekranu, więc zapisany tu
        // numer jest zawsze bieżący, niezależnie od tego, ile starych domknięć wisi.
        biezacyAppid = appid;
        wrapReactType((ret1 as any).props.children);
        afterPatch((ret1 as any).props.children.type, "type", (_1: any[], ret2: ReactElement) => {
          const list = (ret2 as any)?.props?.children?.[1]?.props?.children?.props?.children;
          if (!Array.isArray(list)) {
            // Steam przestawił ekran gry. Nie udajemy, że sekcja jest — mówimy to.
            failure = t("ui.patch_slot_missing");
            onFailure(failure);
            return ret2;
          }
          const existing = list.findIndex((child: any) => child?.props?.id === MARKER);
          const section = (
            <div id={MARKER} key={MARKER} style={{ position: "relative" }}>
              <GamePageSection appid={biezacyAppid} key={biezacyAppid} />
            </div>
          );
          // podmiana, nie wstawianie drugiej: ta funkcja odpala się przy każdym
          // przerysowaniu ekranu, więc splice bez sprawdzenia mnożyłby sekcje
          if (existing >= 0) {
            list.splice(existing, 1, section);
            failure = null;
            return ret2;
          }
          // Miejsce wstawienia: PRZED blokiem treści ekranu gry. Indeks 0 (pierwsza
          // wersja) wchodził w pasek nagłówka — panel nachodził na tytuł gry i na
          // przycisk GRAJ (ZMIERZONE na urządzeniu). Rozpoznanie bloku treści po
          // zestawie właściwości zdjęte z hulkrelax/hltb-for-deck.
          const anchor = list.findIndex(
            (child: any) =>
              child?.props?.childFocusDisabled !== undefined &&
              child?.props?.navRef !== undefined &&
              child?.props?.children?.props?.details !== undefined &&
              child?.props?.children?.props?.overview !== undefined,
          );
          if (anchor < 0) {
            failure = t("ui.patch_anchor_missing");
            onFailure(failure);
            return ret2;
          }
          list.splice(anchor, 0, section);
          failure = null;
          return ret2;
        });
        return ret1;
      });
      return props;
    },
  );
  return () => routerHook.removePatch("/library/app/:appid", patch);
}
