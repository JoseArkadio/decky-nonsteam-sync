import { DialogButton, Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { addOne, emptySummary, finishAdding } from "../add-game";
import { Candidate, hasSgdbKey, scan, resolveTitle } from "../backend";
import { TitlePicker } from "./TitlePicker";
import { fromBackend, t } from "../i18n";

/** Skan w locie, wspólny dla wszystkich montowań komponentu.
 *
 *  ZMIERZONE na Steam Machine: jedno wejście na tę stronę dawało DWA wpisy
 *  „znaleziono 3 gier" w odstępie 3 s. To nie podwójny efekt w jednym montowaniu
 *  (blokada na `useRef` nic nie dała) — komponent montuje się dwa razy, a każde
 *  montowanie ma własny ref. Jeden skan to chodzenie po karcie plus wywołanie
 *  Ludusaviego na każdy folder, więc drugi jest czystym kosztem. Obietnica jest
 *  modułowa, żeby oba montowania trafiły w to samo wywołanie.
 *
 *  ponytail: bez cache z czasem życia — po zakończeniu skanu obietnica znika, więc
 *  „Szukaj ponownie" zawsze pyta kartę na nowo. Gdyby to okazało się za drogie,
 *  dopiszcie TTL. */
let pending: Promise<Candidate[]> | null = null;

function scanOnce(): Promise<Candidate[]> {
  if (!pending) {
    pending = scan().finally(() => {
      pending = null;
    });
  }
  return pending;
}

/** Gry z karty, których Ludusavi nie rozpoznał — tu użytkownik podaje tytuł.
 *
 *  Bez tego ekranu takiej gry NIE DA SIĘ dodać: skan wypisywał tylko „N bez
 *  rozpoznanego tytułu" i na tym się kończyło. `design.md` §5.3 mówi wprost, że
 *  plugin ma zapytać i pokazać propozycje, a nie zgadywać — tytuł jest w tym
 *  projekcie tożsamością gry i od niego zależy wykrywanie zapisów przez Ludusavi.
 *
 *  Skanujemy przy wejściu na tę stronę, nie przy otwarciu ekranu: skan chodzi po
 *  karcie i woła Ludusaviego dla KAŻDEGO folderu (kilka sekund na sztukę).
 *  SidebarNavigation renderuje treść tylko aktywnej strony (ZMIERZONE na Decku:
 *  w DOM jest wyłącznie panel zaznaczonej pozycji), więc koszt płaci ten, kto tu wejdzie.
 */
export function UnnamedGames({ onChanged }: { onChanged: () => void }) {
  const [list, setList] = useState<Candidate[] | null>(null); // null = jeszcze szukamy
  const [broken, setBroken] = useState<Candidate[]>([]);
  const [total, setTotal] = useState(0);
  const [failure, setFailure] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Osobno od treści `note`: kolor notatki nie może zależeć od dopasowania tekstu
  // (ten tekst idzie teraz przez `t()` i w innym języku nie zaczyna się tak samo).
  const [noteIsError, setNoteIsError] = useState(false);
  const [busy, setBusy] = useState(false);

  const look = async () => {
    setFailure(null);
    try {
      const found = await scanOnce();
      setTotal(found.length);
      setList(found.filter((c) => !c.error && !c.title));
      setBroken(found.filter((c) => !!c.error));
    } catch (err) {
      setList([]);
      setFailure(t("ui.unnamed.scan_failed", { detail: err instanceof Error ? err.message : String(err) }));
    }
  };

  useEffect(() => {
    void look();
  }, []);

  const add = (candidate: Candidate, title: string) => async () => {
    const wanted = title.trim();
    if (!wanted) {
      setNote(t("ui.unnamed.empty_title"));
      setNoteIsError(false);
      return;
    }
    setBusy(true);
    setNote(null);
    setNoteIsError(false);
    // Tytuł MUSI być tą nazwą, którą zna Ludusavi — od niego zależy całe wykrywanie
    // zapisów. ZMIERZONE na Decku: wpisane „Baba is You" wobec bazowego „Baba Is You"
    // dawało „Brak informacji dla tych gier", czyli grę bez obsługi zapisów.
    const znane = await resolveTitle(wanted).catch(() => ({ title: null, candidates: [] }));
    const canonical = znane.title ?? wanted;
    // addOne łapie własne wyjątki i wpisuje je do summary, więc tu nie ma try/catch:
    // milcząca porażka jest niemożliwa, bo błędy trafiają na ekran niżej
    const summary = emptySummary();
    await addOne(candidate, canonical, await hasSgdbKey().catch(() => false), summary);
    // Kolekcja karty MUSI zostać odświeżona TUTAJ, nie tylko po skanie z panelu.
    // ZMIERZONE na Decku 2026-08-24: „Marvel Tōkon: Fighting Souls" był w rejestrze
    // z appidem i etykietą „Karta 1", a w kolekcji „Karta 1" go NIE BYŁO — bo gra
    // dodana z tego ekranu wołała samo addOne(), a syncCollections() wisiało
    // wyłącznie na przycisku „Skanuj kartę" w panelu Quick Access.
    await finishAdding(summary);
    setBusy(false);
    if (summary.errors.length) {
      setNote(t("ui.unnamed.add_failed", { detail: summary.errors.join("; ") }));
      setNoteIsError(true);
      await look();
      onChanged();
      return;
    }
    setNote(
      znane.title === null
        ? t("ui.unnamed.added_unknown", { title: wanted })
        : znane.title !== wanted
          ? t("ui.unnamed.added_renamed", { title: znane.title })
          : t("ui.unnamed.added_ok", { title: wanted }),
    );
    await look();
    onChanged();
  };

  if (list === null) {
    return (
      <PanelSection title={t("ui.unnamed.title")}>
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", opacity: 0.7 }}>{t("ui.unnamed.scanning")}</div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <PanelSection title={t("ui.unnamed.title")}>
      <PanelSectionRow>
        <div style={{ fontSize: "0.85em", opacity: 0.7 }}>{t("ui.unnamed.intro")}</div>
      </PanelSectionRow>

      {failure && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", color: "#ff6b6b" }}>{failure}</div>
        </PanelSectionRow>
      )}
      {note && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", color: noteIsError ? "#ff6b6b" : undefined }}>{note}</div>
        </PanelSectionRow>
      )}

      {list.length === 0 && !failure && (
        <PanelSectionRow>
          {/* „skan nic nie zwrócił" i „wszystko ma tytuł" to DWA różne stany. Bez tego
              rozróżnienia brak karty wyglądał jak „nie ma tu nic do zrobienia" — czyli
              awaria (albo po prostu wyjęta karta) udawała sukces. */}
          {total === 0 ? (
            <div style={{ fontSize: "0.85em", color: "#ffb347" }}>{t("ui.unnamed.scan_empty")}</div>
          ) : (
            <div style={{ fontSize: "0.85em", opacity: 0.7 }}>
              {/* liczba przed rzeczownikiem wymagałaby odmiany (3 gry / 5 gier) — stawiamy
                  ją po dwukropku i problem znika */}
              {t("ui.unnamed.all_recognized", { total })}
            </div>
          )}
        </PanelSectionRow>
      )}

      {list.map((candidate) => (
        <PanelSectionRow key={candidate.folder}>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "10px 0" }}>
            <div style={{ fontWeight: "bold" }}>{candidate.folder}</div>
            <div style={{ fontSize: "0.8em", opacity: 0.7, overflowWrap: "anywhere" }}>{candidate.exe_abs}</div>

            {candidate.candidates.length > 0 ? (
              <>
                <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{t("ui.unnamed.proposals_label")}</div>
                <Focusable style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {candidate.candidates.map((proposal) => (
                    <DialogButton key={proposal} disabled={busy} onClick={add(candidate, proposal)}>
                      {proposal}
                    </DialogButton>
                  ))}
                </Focusable>
              </>
            ) : (
              <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{t("ui.unnamed.no_proposals")}</div>
            )}

            <TitlePicker
              initial={candidate.folder}
              disabled={busy}
              typedLabel={t("ui.unnamed.add_typed_button")}
              onPick={(hit) => void add(candidate, hit.value)()}
            />
          </div>
        </PanelSectionRow>
      ))}

      {broken.map((candidate) => (
        <PanelSectionRow key={`err-${candidate.folder}`}>
          {/* folder, którego skan w ogóle nie umiał przetworzyć (np. brak pliku .exe) —
              to inna awaria niż brak tytułu, ale przemilczenie jej wyglądałoby jak „nic tu nie ma" */}
          <div style={{ fontSize: "0.85em", color: "#ff6b6b" }}>
            {candidate.folder}: {fromBackend(candidate.error)}
          </div>
        </PanelSectionRow>
      ))}

      <PanelSectionRow>
        <DialogButton disabled={busy} onClick={() => void look()}>
          {t("ui.unnamed.search_again_button")}
        </DialogButton>
      </PanelSectionRow>
    </PanelSection>
  );
}
