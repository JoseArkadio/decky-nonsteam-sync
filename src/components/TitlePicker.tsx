import { DialogButton, Focusable, ModalRoot, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { searchTitles } from "../backend";
import { t } from "../i18n";

/** Jedna pozycja wyniku. `value` idzie do wywołującego, `label` na przycisk —
 *  przy tytułach to to samo, przy grach ze sklepu `value` jest appidem. */
export interface Hit {
  value: string;
  label: string;
}

interface Props {
  /** Tekst, od którego zaczyna pole — nazwa folderu albo obecny tytuł gry. */
  initial?: string;
  disabled?: boolean;
  /** Skąd biorą się wyniki. Domyślnie baza Ludusaviego. */
  search?: (text: string) => Promise<Hit[]>;
  onPick: (hit: Hit) => void;
  /** Napis przycisku „użyj tego, co wpisałem". Bez niego takiego przycisku nie ma —
   *  tam, gdzie wartość MUSI pochodzić z listy (appid gry ze sklepu), wpisany tekst
   *  nie jest równorzędną opcją. */
  typedLabel?: string;
  /** Napis nad polem. */
  label?: string;
  /** Komunikat przy zerze wyników. */
  noneLabel?: (wanted: string) => string;
  /** Napis nad listą wyników — „w bazie" nie pasuje do wyników ze sklepu Steama. */
  foundLabel?: string;
}

const titlesFromLudusavi = async (text: string): Promise<Hit[]> =>
  (await searchTitles(text, 20)).map((title) => ({ value: title, label: title }));

/** Szukanie i wybór z listy — wspólne dla tytułów z bazy Ludusaviego i gier ze sklepu.
 *
 *  ZGŁOSZONE z urządzenia i ZMIERZONE: wcześniej trzeba było trafić w tytuł CO DO
 *  ZNAKU. „Marvel Tōkon: Fighting Souls" ma makron, którego nie ma na klawiaturze
 *  ekranowej Steama — `find --normalized "Marvel Tokon"` odpowiadał `unknownGames`,
 *  a `--fuzzy --multiple` na to samo zwracał „Mall Tycoon" i „Marco Polo". Dziś
 *  backend szuka po FRAGMENCIE w manifeście na dysku (0,33 s, bez sieci), więc
 *  „tokon" albo „isaac" wystarczy.
 *
 *  Jeden komponent na cztery ekrany („Nierozpoznane gry", „Dodaj z dysku", zmiana
 *  tytułu i wskazanie gry w sklepie) — każda kopia pola z przyciskiem to kolejne
 *  miejsce, w którym wyszukiwarka wygląda i zachowuje się inaczej.
 */
export function TitlePicker({
  initial,
  disabled,
  search = titlesFromLudusavi,
  onPick,
  typedLabel,
  label,
  noneLabel,
  foundLabel,
}: Props) {
  const [typed, setTyped] = useState(initial ?? "");
  // null = jeszcze nie szukaliśmy pod TYM tekstem. Pusta lista = szukaliśmy i nie ma nic;
  // zlanie tych dwóch stanów pokazywałoby „baza nic nie zna" jeszcze przed szukaniem.
  const [found, setFound] = useState<Hit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const run = async () => {
    const wanted = typed.trim();
    if (!wanted) {
      setNote(t("ui.unnamed.type_first"));
      return;
    }
    setBusy(true);
    setNote(null);
    const hits = await search(wanted).catch(() => [] as Hit[]);
    setBusy(false);
    setFound(hits);
    if (!hits.length) {
      setNote(noneLabel ? noneLabel(wanted) : t("ui.unnamed.not_found", { wanted }));
    }
  };

  return (
    <>
      <TextField
        label={label ?? t("ui.unnamed.search_label")}
        value={typed}
        onChange={(event) => {
          setTyped(event.target.value);
          // wyniki dotyczyły innego zapytania — zostawienie ich na ekranie zachęcałoby
          // do wybrania pozycji, której nikt nie sprawdził
          setFound(null);
          setNote(null);
        }}
      />
      <Focusable style={{ display: "flex", gap: "8px" }}>
        <DialogButton disabled={disabled || busy} onClick={() => void run()}>
          {busy ? t("qa.working") : t("ui.unnamed.search_button")}
        </DialogButton>
        {typedLabel && (
          <DialogButton
            disabled={disabled || busy}
            onClick={() => onPick({ value: typed.trim(), label: typed.trim() })}
          >
            {typedLabel}
          </DialogButton>
        )}
      </Focusable>

      {note && <div style={{ fontSize: "0.8em", color: "#ffb347" }}>{note}</div>}

      {found !== null && found.length > 0 && (
        <>
          <div style={{ fontSize: "0.8em", opacity: 0.7 }}>
            {foundLabel ?? t("ui.unnamed.found_label")}
          </div>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {found.map((hit) => (
              <DialogButton key={hit.value} disabled={disabled || busy} onClick={() => onPick(hit)}>
                {hit.label}
              </DialogButton>
            ))}
          </Focusable>
        </>
      )}
    </>
  );
}

/** Ten sam wybór, ale w OKNIE — tak, jak robi to decky-steamgriddb.
 *
 *  ZGŁOSZONE: „żeby wyświetlała taki pop z listą opcji do wyboru". Na ekranie gry nie
 *  ma miejsca na trzecią rozwijaną sekcję, a lista dwudziestu pozycji rozpychałaby
 *  prawą kolumnę; okno daje jej całą wysokość i własne zaznaczenie dla pada.
 *
 *  `Close()` wołamy PO wybraniu, nie w `onOK`: nasze przyciski są w treści okna, a nie
 *  w jego stopce. */
export function openPicker(opts: {
  title: string;
  intro?: string;
  initial?: string;
  label?: string;
  foundLabel?: string;
  typedLabel?: string;
  extra?: (close: () => void) => any;
  search?: (text: string) => Promise<Hit[]>;
  noneLabel?: (wanted: string) => string;
  onPick: (hit: Hit) => void;
}): void {
  const modal = showModal(
    <ModalRoot bAllowFullSize onCancel={() => modal.Close()} onEscKeypress={() => modal.Close()}>
      <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "4px 0" }}>
        <div style={{ fontWeight: "bold", fontSize: "1.1em" }}>{opts.title}</div>
        {opts.intro && <div style={{ fontSize: "0.85em", opacity: 0.7 }}>{opts.intro}</div>}
        <TitlePicker
          initial={opts.initial}
          label={opts.label}
          foundLabel={opts.foundLabel}
          typedLabel={opts.typedLabel}
          search={opts.search}
          noneLabel={opts.noneLabel}
          onPick={(hit) => {
            modal.Close();
            opts.onPick(hit);
          }}
        />
        {opts.extra?.(() => modal.Close())}
        <Focusable>
          <DialogButton onClick={() => modal.Close()}>{t("ui.close_button")}</DialogButton>
        </Focusable>
      </div>
    </ModalRoot>,
  );
}
