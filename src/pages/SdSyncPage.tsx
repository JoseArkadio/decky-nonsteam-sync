import { PanelSection, PanelSectionRow, SidebarNavigation } from "@decky/ui";
import { useEffect, useState } from "react";
import { GameRecord, games } from "../backend";
import { About } from "../components/About";
import { GameDetails } from "../components/GameDetails";
import { GameListItem } from "../components/GameListItem";
import { AddFromDisk } from "../components/AddFromDisk";
import { UnnamedGames } from "../components/UnnamedGames";
import { SDSYNC_ROUTE, gameRoute, subscribeToSelection, takeSelection } from "../selection";
import { SidebarColumnWidth } from "../steam-page-patch";
import { locale, t } from "../i18n";
import { useLang } from "../i18n/resolve";

/** Nośnik, po którym grupujemy spis. Dla gry z dysku konsoli etykieta karty jest pusta
 *  i „—" nic by nie powiedziało, więc dostaje własną nazwę grupy. */
const nosnik = (game: GameRecord): string =>
  game.carrier === "disk" ? t("ui.facts.disk") : game.card_label || "—";

export function SdSyncPage() {
  useLang(); // korzeń w osobnym drzewie React — bez tego napisy tu nie przerysują się
  const [list, setList] = useState<GameRecord[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  // Którą stronę pokazać, decyduje TRASA (patrz komentarz przy `route` niżej).
  // W stanie zostaje tylko to, czego trasa nie wie: na której grze postawić fokus
  // po wejściu z ekranu gry (ustalenie 10).
  const [initial] = useState(takeSelection);
  const [focusKey, setFocusKey] = useState<string | null>(initial.focus ? initial.titleKey : null);

  const reload = async () => {
    setList(await games());
  };

  useEffect(() => {
    const unsubscribe = subscribeToSelection((titleKey) => {
      setFocusKey(titleKey);
      reload().catch(() => undefined); // wracamy z ekranu gry: stan mógł się zmienić
    });
    reload().catch((err) => setFailure(t("ui.list_load_failed", { detail: String(err) })));
    return unsubscribe;
  }, []);

  // `route` NIE jest ozdobą i nie wolno go zastąpić `identifier`em ani sterowaniem
  // przez `page`. ZMIERZONE (zrzut źródła komponentu z urządzenia): SidebarNavigation
  // przepisuje strony na `{...rest, identifier: link || route}` — bez `route` każdy
  // identifier jest `undefined`, więc klik w spis wywołuje `onPageRequested(undefined)`,
  // które komponent połyka (`if (e != current.route)`) i panel stoi na pierwszej grze.
  // Z `route` klik wykonuje `history.replace(route)` ZAWSZE — więc trasa musi istnieć
  // (`/sdsync/:key?` w index.tsx), inaczej router wychodzi z naszego ekranu i wszystko
  // znika. I dlatego NIE podajemy `page`: trasa jest jedynym źródłem prawdy, a drugie
  // (własny stan) rozjeżdżało się z nią przy wejściu na `/sdsync` bez klucza.
  // Spis BEZ grupowania po kartach i to jest decyzja użytkownika, nie uproszczenie:
  // etykieta karty jest już w szczegółach gry i w kolekcjach Steama, a `SidebarNavigation`
  // nie zna pojęcia nagłówka grupy — jego pozycje to albo napis „separator", albo strona
  // z trasą (sprawdzone w źródle komponentu na urządzeniu). Każdy nagłówek trzeba więc
  // rysować WEWNĄTRZ kafelka gry, gdzie obejmuje go podświetlenie zaznaczenia i psuje
  // równość pudełek, od której zależy nawigacja pada. Trzecia informacja o tym samym
  // nie była warta tej ceny.
  const pages: any[] = [...list]
    .sort((a, b) => a.title.localeCompare(b.title, locale()))
    .map((game) => ({
      title: <GameListItem game={game} />,
      route: gameRoute(game.title_key),
      hideTitle: true,
      content: <GameDetails game={game} onChanged={() => void reload()} autofocusAction={focusKey === game.title_key} />,
    }));

  if (list.length === 0) {
    pages.push({
      title: t("ui.no_games_title"),
      route: `${SDSYNC_ROUTE}/-empty`,
      content: (
        <PanelSection title={t("ui.registry_empty_title")}>
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em", opacity: 0.7 }}>
              {t("ui.no_games_desc")}
            </div>
          </PanelSectionRow>
          {failure && <PanelSectionRow><div style={{ color: "#ff6b6b" }}>{failure}</div></PanelSectionRow>}
        </PanelSection>
      ),
    });
  }

  pages.push("separator");
  // stała pozycja, nie zależna od tego, czy coś tu jest: skan chodzi po karcie i woła
  // Ludusaviego dla każdego folderu, więc liczba w tytule kosztowałaby ten sam skan
  // przy KAŻDYM otwarciu ekranu. Treść ładuje się dopiero po wejściu.
  pages.push({
    title: t("ui.unnamed.title"),
    route: `${SDSYNC_ROUTE}/-unnamed`,
    content: (
      <>
        <UnnamedGames onChanged={() => void reload()} />
        <AddFromDisk onChanged={() => void reload()} />
      </>
    ),
  });

  // „O wtyczce" na końcu i w TYM spisie, a nie jako osobna trasa: wtyczka ma już
  // jeden ekran, a druga trasa znaczyłaby drugie miejsce do wejścia i drugi patch.
  // Treść jest harmonijką (patrz komentarz w components/About.tsx) właśnie dlatego,
  // że długa strona w tej pozycji raz już zepsuła nawigację pada — na logu zdarzeń.
  pages.push({
    title: t("ui.about.title"),
    route: `${SDSYNC_ROUTE}/-about`,
    content: <About />,
  });

  return (
    <>
      <SidebarColumnWidth />
      {/* disableRouteReporting: nasze trasy wstrzykuje Decky, Steam nie ma ich u siebie
          — zgłaszanie mu ich niczego nie daje, a jest wywołaniem w ścieżce kliknięcia. */}
      <SidebarNavigation title="NonSteam Sync" pages={pages} disableRouteReporting />
    </>
  );
}
