import { DialogButton, Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { FileSelectionType, openFilePicker } from "@decky/api";
import { useState } from "react";
import { addDiskGame, hasSgdbKey, setAppid } from "../backend";
import { TitlePicker } from "./TitlePicker";
import { fetchAndApplyArtwork } from "../artwork";
import { emptySummary, finishAdding } from "../add-game";
import { addShortcut, nameOf, setCompatTool, setShortcutName } from "../steam";
import { fromBackend, t } from "../i18n";

const PROTON = "proton_experimental";

/** Dodanie gry zainstalowanej na dysku konsoli.
 *
 *  Skan chodzi po katalogach `Games` na kartach, więc takiej gry nie ma jak zobaczyć —
 *  wskazuje ją człowiek. Tytuł MUSI przejść przez bazę Ludusavi, bo od niego zależy
 *  całe wykrywanie zapisów (ZMIERZONE: „Baba is You" wobec bazowego „Baba Is You"
 *  znaczyło grę bez obsługi zapisów).
 *
 *  Nośnikiem takiej gry jest katalog na dysku (patrz
 *  docs/superpowers/specs/2026-08-22-gry-z-dysku-design.md), a między urządzeniami
 *  przewozi ją chmura. */
export function AddFromDisk({ onChanged }: { onChanged: () => void }) {
  const [exe, setExe] = useState<string | null>(null);
  // Tekst startowy pickera: nazwa folderu wskazanego pliku. `key` na komponencie
  // przemontowuje go po wskazaniu innego pliku, więc pole nie zostaje z poprzednią nazwą.
  const [guess, setGuess] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Osobno od treści `note`: kolor notatki nie może zależeć od dopasowania tekstu
  // (ten tekst idzie teraz przez `t()` i w innym języku nie zaczyna się tak samo).
  const [noteIsError, setNoteIsError] = useState(false);

  const pick = async () => {
    setNote(null);
    setNoteIsError(false);
    // Start z katalogu domowego, nie z folderu gry: ZMIERZONE, że wybierak pokazuje
    // PUSTĄ listę, gdy ścieżka startowa nie istnieje — a tu jeszcze nic nie wiemy.
    const wybrane = await openFilePicker(FileSelectionType.FILE, "/home/deck", true, true,
                                         undefined, ["exe"], false, true).catch(() => null);
    const sciezka = wybrane?.realpath || wybrane?.path;
    if (!sciezka) return; // anulowane — to nie błąd
    setExe(sciezka);
    setGuess(sciezka.split("/").slice(-2, -1)[0] ?? "");
  };

  const add = (title: string) => async () => {
    if (!exe) return;
    setBusy(true);
    setNote(null);
    setNoteIsError(false);
    try {
      const record = await addDiskGame(exe, title);
      if (record.error || !record.title_key) {
        throw new Error(record.error ? fromBackend(record.error) : t("ui.registry_no_entry"));
      }
      // kafelek zakładamy tylko, gdy gra jeszcze go nie ma
      let appid = record.appid ?? 0;
      if (!appid || nameOf(appid) === null) {
        appid = await addShortcut(title, exe, exe.substring(0, exe.lastIndexOf("/")));
        if (!appid) throw new Error(t("ui.steam_no_appid"));
        setCompatTool(appid, PROTON);
        const stored = await setAppid(record.title_key, appid);
        if (stored.error) {
          throw new Error(t("ui.appid_not_saved", { appid, detail: fromBackend(stored.error) }));
        }
      } else if (nameOf(appid) !== title) {
        setShortcutName(appid, title);
      }
      if (await hasSgdbKey().catch(() => false)) {
        const problemy = await fetchAndApplyArtwork(record.title_key, title, appid);
        if (problemy.length) {
          setNote(t("ui.disk_added_artwork_problem", { title, detail: problemy.join("; ") }));
          setNoteIsError(false);
        }
      }
      // Ten sam ogon co po skanie karty: kolekcje, ścieżki kafelków i — najważniejsze —
      // pytanie o zapis dla świeżo dodanej gry. Dla gry z dysku odpowiada na nie chmura,
      // bo katalog lokalny nie jeździ między urządzeniami.
      const ogon = emptySummary();
      ogon.fresh.push(record.title_key);
      await finishAdding(ogon);
      if (ogon.errors.length) {
        setNote(t("ui.disk_add_failed", { detail: ogon.errors.join("; ") }));
        setNoteIsError(true);
        onChanged();
        return;
      }
      setNote((poprzednie) =>
        poprzednie ?? (ogon.restored.length
          ? t("ui.disk_added_restored", { title })
          : t("ui.disk_added", { title })));
      setNoteIsError(false);
      setExe(null);
      setGuess("");
      onChanged();
    } catch (err) {
      setNote(t("ui.disk_add_failed", { detail: err instanceof Error ? err.message : String(err) }));
      setNoteIsError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelSection title={t("ui.disk_panel_title")}>
      <PanelSectionRow>
        <div style={{ fontSize: "0.85em", opacity: 0.7 }}>{t("ui.disk_panel_desc")}</div>
      </PanelSectionRow>

      {note && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", color: noteIsError ? "#ff6b6b" : undefined }}>
            {note}
          </div>
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <Focusable>
          <DialogButton disabled={busy} onClick={pick}>
            {exe ? t("ui.disk_pick_exe_button_other") : t("ui.pick_exe_button")}
          </DialogButton>
        </Focusable>
      </PanelSectionRow>

      {exe && (
        <PanelSectionRow>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "6px 0" }}>
            <div style={{ fontSize: "0.8em", opacity: 0.7, overflowWrap: "anywhere" }}>{exe}</div>
            <TitlePicker
              key={exe}
              initial={guess}
              disabled={busy}
              typedLabel={t("ui.unnamed.add_typed_button")}
              onPick={(hit) => void add(hit.value)()}
            />
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
