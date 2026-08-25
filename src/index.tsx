import {
  ButtonItem,
  DropdownItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
  staticClasses,
} from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaSdCard } from "react-icons/fa";
import {
  UiSettings,
  cloudConfigured,
  games,
  getUiSettings,
  hasSgdbKey,
  logAdd,
  logTail,
  scan,
  setSgdbKey,
  setUiSetting,
} from "./backend";
import { ScanSummary, addOne, describe, emptySummary, finishAdding } from "./add-game";
import { EventLog } from "./components/EventLog";
import { CATALOGS, fromBackend, setLang, t } from "./i18n";
import { fromEnvironment, useLang } from "./i18n/resolve";
import { refreshCardBadges, refreshPlaytime, registerEvents, syncNow } from "./events";
import { SdSyncPage } from "./pages/SdSyncPage";
import { About, KATEGORIE } from "./components/About";
import { ABOUT_ROUTE, SDSYNC_ROUTE, aboutRoute } from "./selection";
import { getPatchFailure, patchGamePage } from "./steam-page-patch";

/** Skan karty → dodanie każdej rozpoznanej gry. Sama logika dodawania siedzi w
 *  `add-game.ts`, bo ma teraz DWÓCH wywołujących: ten skan i ekran „Nierozpoznane
 *  gry", na którym tytuł podaje użytkownik. */
// bez "export": index.tsx jest wejściem rollupa i może eksportować tylko default
async function scanAndAdd(): Promise<ScanSummary> {
  const summary = emptySummary();
  const started = Date.now() / 1000;
  const candidates = await scan();
  const withArtwork = await hasSgdbKey();
  // cisza o braku klucza wyglądała jak „grafiki nie działają"; grafiki dla gier już
  // dodanych są pod przyciskiem „Pobierz grafiki" na ekranie gier
  summary.noArtworkKey = !withArtwork;

  if (candidates.length === 0) {
    // guarded() w backendzie zwraca [] także wtedy, gdy skan się WYWALIŁ — samo
    // „dodano 0" wyglądałoby jak sukces, więc dociągamy błędy z logu zdarzeń
    const failures = (await logTail(10)).filter((e) => e.kind === "error" && e.ts >= started);
    summary.errors.push(...failures.map((e) => `log: ${fromBackend(e)}`));
    summary.note = failures.length ? t("ui.scan_note_failed") : t("ui.scan_note_empty");
  }

  for (const candidate of candidates) {
    if (candidate.error) {
      summary.errors.push(`${candidate.folder}: ${fromBackend(candidate.error)}`);
      continue;
    }
    // tytuł jest w tym projekcie tożsamością gry i od niego zależy wykrywanie zapisów,
    // więc nie zgadujemy — folder ląduje na ekranie „Nierozpoznane gry"
    if (!candidate.title) {
      summary.needTitle.push(candidate.folder);
      continue;
    }
    await addOne(candidate, candidate.title, withArtwork, summary);
  }
  if (summary.added.length) await refreshPlaytime().catch(() => undefined);
  // Ogon dodawania na końcu: kolekcje potrzebują appidów, które właśnie powstały albo
  // zostały przejęte, a pytanie karty o zapis potrzebuje wpisu w rejestrze i redirectu.
  await finishAdding(summary);
  return summary;
}

/** Panel Quick Access: linia stanu, trzy akcje, ustawienia na dole i zwinięte.
 *  Kolejność nie jest przypadkowa — rzeczy robione często na górze, ustawienia
 *  dotykane raz w życiu na dole (etap 5, ustalenie 6). */
function Panel() {
  useLang(); // korzeń: przerysowanie po rozstrzygnięciu języka
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<ScanSummary | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [stat, setStat] = useState<{ count: number; conflicts: number; cards: string[] }>({
    count: 0,
    conflicts: 0,
    cards: [],
  });
  const [cloud, setCloud] = useState<boolean | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [key, setKey] = useState("");
  const [keyStored, setKeyStored] = useState<boolean | null>(null);
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [gamePage, setGamePage] = useState<UiSettings["game_page"]>("left");
  const [syncCloud, setSyncCloud] = useState<UiSettings["sync_cloud"]>("on");
  const [gamePageSteam, setGamePageSteam] = useState<UiSettings["game_page_steam"]>("on");
  const [badgePos, setBadgePos] = useState<UiSettings["badge_pos"]>("bottom-right");
  const [lang, setLangSetting] = useState<UiSettings["lang"]>("auto");
  const [gamePageNote, setGamePageNote] = useState<string | null>(null);

  const refresh = async () => {
    const [records, stored, remote, ui] = await Promise.all([
      games(),
      hasSgdbKey(),
      cloudConfigured(),
      getUiSettings(),
    ]);
    setGamePage(ui.game_page);
    setSyncCloud(ui.sync_cloud ?? "on");
    setGamePageSteam(ui.game_page_steam ?? "on");
    setBadgePos(ui.badge_pos ?? "bottom-right");
    setLangSetting(ui.lang ?? "auto");
    setStat({
      count: records.length,
      conflicts: records.filter((record) => record.conflict).length,
      cards: Array.from(new Set(records.filter((r) => r.available).map((r) => r.card_label).filter(Boolean))),
    });
    setKeyStored(stored);
    setCloud(remote.configured);
  };

  useEffect(() => {
    refresh().catch((err) =>
      setFailure(t("qa.state_load_failed", { detail: err instanceof Error ? err.message : String(err) })),
    );
  }, []);

  const status = [
    t("qa.game_count", { count: stat.count }),
    stat.conflicts ? t("qa.conflict_count", { count: stat.conflicts }) : null,
    stat.cards.length ? t("qa.card_label", { cards: stat.cards.join(", ") }) : t("status.no_card"),
  ]
    .filter(Boolean)
    .join(" · ");

  const patchFailure = getPatchFailure();

  return (
    // BEZ `title`: Decky rysuje nazwę wtyczki we własnym nagłówku panelu (nasz
    // `titleView`), więc `title` tutaj dawał ten sam napis dwa razy pod rząd —
    // „NonSteam Sync" i pod nim „NONSTEAM SYNC" (ZMIERZONE na zrzucie z Decka).
    // Ta sama grabla co na ekranie „O wtyczce".
    <PanelSection>
      <PanelSectionRow>
        <div style={{ fontSize: "0.85em", color: stat.conflicts ? "#ffb347" : undefined }}>{status}</div>
      </PanelSectionRow>
      {patchFailure && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", color: "#ffb347" }}>{t("qa.patch_failure")}</div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setSummary(null);
            try {
              const result = await syncNow(t("qa.reason_button"));
              // null = blokada, nie „nic do zrobienia": mówimy to wprost
              setFailure(result ? null : t("qa.sync_busy"));
            } catch (err) {
              setFailure(t("qa.sync_failed_detail", { detail: err instanceof Error ? err.message : String(err) }));
            } finally {
              setBusy(false);
              await refresh().catch(() => undefined);
            }
          }}
        >
          {busy ? t("qa.working") : t("qa.sync_now")}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setFailure(null);
            try {
              const result = await scanAndAdd();
              setSummary(result);
              toaster.toast({ title: "NonSteam Sync", body: describe(result), duration: result.errors.length ? 10000 : 5000 });
            } catch (err) {
              // skan padł w całości: to musi być widoczne, nie wyglądać na „nic do zrobienia"
              const message = err instanceof Error ? err.message : String(err);
              setSummary(null);
              setFailure(message);
              toaster.toast({ title: t("qa.toast_scan_failed_title"), body: message, duration: 10000 });
            } finally {
              setBusy(false);
              await refresh().catch(() => undefined);
            }
          }}
        >
          {busy ? t("qa.scanning") : t("qa.scan_button")}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => Navigation.Navigate(SDSYNC_ROUTE)}>
          {t("qa.open_game_list")}
        </ButtonItem>
      </PanelSectionRow>
      {(summary || failure) && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", whiteSpace: "pre-wrap" }}>
            {failure ?? describe(summary!)}
            {summary?.errors.map((line) => `\n• ${line}`)}
          </div>
        </PanelSectionRow>
      )}

      {/* Log NAD ustawieniami: czyta się go często (po każdej nieudanej synchronizacji),
          a w ustawienia wchodzi się raz w życiu. Był osobną stroną na ekranie NonSteam Sync —
          dlaczego stamtąd wyszedł, patrz komentarz w components/EventLog.tsx. */}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setShowLog((visible) => !visible)}>
          {showLog ? t("qa.log_hide") : t("qa.log_show")}
        </ButtonItem>
      </PanelSectionRow>
      {showLog && <EventLog />}

      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => Navigation.Navigate(aboutRoute(KATEGORIE[0]))}>
          {t("qa.about_button")}
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setShowSettings((visible) => !visible)}>
          {showSettings ? t("qa.settings_hide") : t("qa.settings_show")}
        </ButtonItem>
      </PanelSectionRow>
      {showSettings && (
        <>
          <PanelSectionRow>
            {/* najczęstsza przyczyna „niby działa, a nic się nie dzieje" — brak chmury.
                Trójstan, bo cloud_configured nie zgaduje: null znaczy „nie wiem". */}
            <div style={{ fontSize: "0.8em", color: cloud === false ? "#ff6b6b" : undefined }}>
              {cloud === true
                ? t("qa.cloud_configured")
                : cloud === false
                  ? t("qa.cloud_missing")
                  : t("qa.cloud_unknown")}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label={t("qa.badge_pos_label")}
              description={t("qa.badge_pos_desc")}
              rgOptions={[
                { data: "bottom-right", label: t("qa.badge_pos_bottom_right") },
                { data: "bottom-left", label: t("qa.badge_pos_bottom_left") },
                { data: "top-right", label: t("qa.badge_pos_top_right") },
                { data: "top-left", label: t("qa.badge_pos_top_left") },
                { data: "off", label: t("qa.option_off") },
              ]}
              selectedOption={badgePos}
              onChange={async (option) => {
                const chosen = option.data as UiSettings["badge_pos"];
                const previous = badgePos;
                setBadgePos(chosen);
                const saved = await setUiSetting("badge_pos", chosen);
                if (saved.error) {
                  setBadgePos(previous);
                  setGamePageNote(t("qa.save_failed", { detail: fromBackend(saved.error) }));
                  return;
                }
                // bez tego zmianę widać dopiero po najbliższym odpytaniu (10 s)
                await refreshCardBadges().catch(() => undefined);
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Chmura to KOPIA ZAPASOWA, nie transport — zapisy jeżdżą na karcie.
                Kto nie chce chmury, ma dostać działającą wtyczkę BEZ czekania na
                rclone (ZMIERZONE: 60–140 s na przebieg). To wybór, nie awaria, więc
                przy „tylko karta" nie ma o niej żadnego komunikatu o błędzie. */}
            <DropdownItem
              label={t("qa.backup_location_label")}
              description={t("qa.backup_location_desc")}
              rgOptions={[
                { data: "on", label: t("qa.backup_location_both") },
                { data: "off", label: t("qa.backup_location_card_only") },
              ]}
              selectedOption={syncCloud}
              onChange={async (option) => {
                const chosen = option.data as UiSettings["sync_cloud"];
                const previous = syncCloud;
                setSyncCloud(chosen);
                const saved = await setUiSetting("sync_cloud", chosen);
                if (saved.error) {
                  setSyncCloud(previous);
                  setGamePageNote(t("qa.save_failed", { detail: fromBackend(saved.error) }));
                }
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            {/* ekran gry jest wspólny dla wszystkich wtyczek — pozycję musi wybrać
                użytkownik, bo tylko on wie, co jeszcze ma tam zainstalowane */}
            <DropdownItem
              label={t("qa.game_page_pos_label")}
              description={t("qa.game_page_pos_desc")}
              rgOptions={[
                { data: "left", label: t("qa.game_page_pos_left") },
                { data: "right", label: t("qa.game_page_pos_right") },
                { data: "bar", label: t("qa.game_page_pos_bar") },
                { data: "off", label: t("qa.option_off") },
              ]}
              selectedOption={gamePage}
              onChange={async (option) => {
                const chosen = option.data as UiSettings["game_page"];
                const previous = gamePage;
                setGamePage(chosen);
                setGamePageNote(null);
                const saved = await setUiSetting("game_page", chosen);
                // odrzucona wartość nie może zostać w interfejsie jako wybrana
                if (saved.error) {
                  setGamePage(previous);
                  setGamePageNote(t("qa.save_failed", { detail: fromBackend(saved.error) }));
                }
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Karta na ekranach gier ZE STEAMA. Zmierzone, że Valve nie pokazuje tam
                daty premiery, oceny ani zgodności (trzyma je o zakładkę dalej), więc
                karta nie dubluje ekranu — ale ten ekran dzielisz z innymi wtyczkami,
                więc decyzja jest Twoja. Ta gra NIE wchodzi przez to pod opiekę wtyczki. */}
            <DropdownItem
              label={t("ui.steam_card.label")}
              description={t("ui.steam_card.desc")}
              rgOptions={[
                { data: "on", label: t("ui.steam_card.on") },
                { data: "off", label: t("ui.steam_card.off") },
              ]}
              selectedOption={gamePageSteam}
              onChange={async (option) => {
                const chosen = option.data as UiSettings["game_page_steam"];
                const previous = gamePageSteam;
                setGamePageSteam(chosen);
                setGamePageNote(null);
                const saved = await setUiSetting("game_page_steam", chosen);
                if (saved.error) {
                  setGamePageSteam(previous);
                  setGamePageNote(
                    t("ui.steam_card.save_failed", { detail: fromBackend(saved.error) }),
                  );
                }
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Ostatnia pozycja świadomie: wchodzi się tu raz w życiu, a wyżej stoją
                rzeczy, których człowiek szuka częściej. Nazwy WŁASNE języków biorą się
                z katalogów (`lang.own_name`), więc dołożenie de.json nie wymaga
                zmiany tego kodu. */}
            <DropdownItem
              label={t("qa.lang_label")}
              description={t("qa.lang_desc")}
              rgOptions={[
                { data: "auto", label: t("qa.lang_auto") },
                ...Object.keys(CATALOGS).map((code) => ({
                  data: code,
                  label: String(CATALOGS[code]["lang.own_name"] ?? code),
                })),
              ]}
              selectedOption={lang}
              onChange={async (option) => {
                const chosen = option.data as UiSettings["lang"];
                const previous = lang;
                setLangSetting(chosen);
                // natychmiast, bez czekania na RPC — inaczej napisy zmieniają się
                // z opóźnieniem sieci lokalnej i wygląda to jak zawieszenie
                setLang(chosen === "auto" ? fromEnvironment() : chosen);
                const saved = await setUiSetting("lang", chosen);
                if (saved.error) {
                  setLangSetting(previous);
                  setLang(previous === "auto" ? fromEnvironment() : previous);
                  setGamePageNote(t("qa.save_failed", { detail: fromBackend(saved.error) }));
                }
              }}
            />
          </PanelSectionRow>
          {gamePageNote && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#ff6b6b" }}>{gamePageNote}</div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <TextField
              label={t("qa.sgdb_key_label")}
              description={
                keyStored ? t("qa.sgdb_key_desc_stored") : t("qa.sgdb_key_desc_missing")
              }
              bIsPassword
              value={key}
              onChange={(event) => setKey(event.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await setSgdbKey(key);
                  const stored = await hasSgdbKey();
                  setKey("");
                  setKeyStored(stored);
                  setKeyNote(
                    stored
                      ? t("qa.sgdb_key_saved")
                      : key.trim()
                        ? t("qa.sgdb_key_not_saved")
                        : t("qa.sgdb_key_removed"),
                  );
                } catch (err) {
                  setKeyNote(
                    t("qa.sgdb_save_failed", { detail: err instanceof Error ? err.message : String(err) }),
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("qa.sgdb_save_button")}
            </ButtonItem>
          </PanelSectionRow>
          {keyNote && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em" }}>{keyNote}</div>
            </PanelSectionRow>
          )}
        </>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => {
  const unregisterEvents = registerEvents();
  // `:key?` — SidebarNavigation na ekranie NonSteam Sync jest route-driven i przy wyborze
  // gry robi `history.replace("/sdsync/<klucz>")`. Bez parametru w trasie ta nawigacja
  // wyprowadzała z naszego ekranu i wszystko znikało (ZMIERZONE na Decku). Parametr
  // musi być OPCJONALNY i na jednej trasie: dwie osobne trasy przemontowywałyby ekran
  // przy każdym kliknięciu w spis.
  routerHook.addRoute(`${SDSYNC_ROUTE}/:key?`, SdSyncPage, { exact: true });
  // „O wtyczce" ma WŁASNY ekran, nie pozycję w spisie gier: spis odpowiada na „co
  // z moimi grami", a About na „co ta wtyczka robi". `:cat?` z tego samego powodu co
  // `:key?` wyżej — SidebarNavigation w środku jest route-driven i bez istniejącej
  // trasy jego `history.replace` wyprowadza z naszego ekranu.
  // Parametr jest OPCJONALNY w trasie, ale wchodzimy ZAWSZE z konkretną kategorią
  // (przycisk w panelu celuje w KATEGORIE[0]). ZMIERZONE na dwóch urządzeniach:
  // przy wejściu na samo `/sdsync-about` żadna strona nie pasuje do adresu
  // (`pages.find(({route}) => matchPath(pathname, route))`), a wtedy podświetlenie
  // wypada gdzie indziej na każdym urządzeniu — Deck stanął na pierwszej kategorii,
  // Machine na ostatniej. Trasa zostaje opcjonalna, bo wejście bez parametru musi
  // dalej renderować ekran, a nie wyprowadzać z niego.
  routerHook.addRoute(`${ABOUT_ROUTE}/:cat?`, About, { exact: true });
  // sekcja na ekranie gry: gdy Steam przestawi układ, wstrzyknięcie się nie uda —
  // log zdarzeń i panel muszą to pokazać, cisza wyglądałaby jak „nie ma nic"
  // Patch trasy biblioteki jest tylko WYZWALACZEM: React nie daje nam kafelka do
  // opakowania, więc kropki dokłada obserwator DOM (patrz steam-badges.tsx).
  const unpatchLibrary = routerHook.addPatch("/library", (tree: unknown) => {
    void refreshCardBadges().catch(() => undefined);
    return tree;
  });
  const unpatchGamePage = patchGamePage((message) => {
    void logAdd("error", t("log.game_page_section", { detail: message }));
  });
  // ponytail: przepływy wystawione na window, żeby dały się wywołać zdalnie przez
  // tools/steam_eval.py — przyciski w Quick Access wymagają fizycznego kliknięcia
  // w Game Mode, a bez tego nie ma jak sprawdzić wtyczki na urządzeniu.
  (window as any).SDSync = {
    scanAndAdd,
    syncNow,
    openPage: () => Navigation.Navigate(SDSYNC_ROUTE),
    closePage: () => Navigation.NavigateBack(),
  };
  return {
    name: "NonSteam Sync",
    titleView: <div className={staticClasses.Title}>NonSteam Sync</div>,
    content: <Panel />,
    icon: <FaSdCard />,
    onDismount() {
      unregisterEvents();
      routerHook.removePatch("/library", unpatchLibrary);
      unpatchGamePage();
      routerHook.removeRoute(`${SDSYNC_ROUTE}/:key?`);
      routerHook.removeRoute(`${ABOUT_ROUTE}/:cat?`);
      delete (window as any).SDSync;
    },
  };
});
