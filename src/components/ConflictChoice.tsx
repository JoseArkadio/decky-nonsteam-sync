import { DialogButton, Focusable } from "@decky/ui";
import { FaCloudDownloadAlt, FaCloudUploadAlt, FaSdCard } from "react-icons/fa";
import { useEffect, useState } from "react";

import { ConflictOptions, conflictOptions } from "../backend";
import { locale, t } from "../i18n";

/** Skąd wolno wziąć zapis. `local` = to, co jest W GRZE (i pojedzie na kartę,
 *  a potem do chmury); `card` = kopia, którą przywiozła karta; `cloud` = kopia
 *  zapasowa z chmury. */
type Skad = "local" | "card" | "cloud";

const OPISY: Array<{ skad: Skad; ikona: any; przycisk: string; opis: string }> = [
  { skad: "local", ikona: FaCloudUploadAlt, przycisk: "ui.conflict.send_mine", opis: "ui.conflict.send_mine_desc" },
  { skad: "card", ikona: FaSdCard, przycisk: "ui.conflict.take_card", opis: "ui.conflict.take_card_desc" },
  { skad: "cloud", ikona: FaCloudDownloadAlt, przycisk: "ui.conflict.take_cloud", opis: "ui.conflict.take_cloud_desc" },
];

/** Data kopii po ludzku. TRZY stany, bo tyle ma backend: znacznik / „nie ma kopii" /
 *  „nie wiem". Pokazanie niewiedzy jako braku kopii kazałoby wybierać w przekonaniu,
 *  że gdzieś nic nie ma — a mógłby tam leżeć najnowszy zapis. */
function kiedy(when: string | null | undefined): string {
  if (when === null || when === undefined) return t("ui.conflict.when_unknown");
  if (!when) return t("ui.conflict.when_none");
  const czas = new Date(when);
  return Number.isNaN(czas.getTime()) ? when : czas.toLocaleString(locale());
}

/** Wybór, z której kopii wziąć zapis — TRZY możliwości, nie dwie.
 *
 *  ZGŁOSZONE: „save może jeszcze nie być wgrany do gry, ale leżeć na karcie SD".
 *  Do tej pory dało się wybrać tylko między swoim zapisem a chmurą, więc kopia,
 *  którą przywiozła karta z drugiego urządzenia, nie była do wybrania wcale —
 *  a to ona jest w tym projekcie transportem i najczęściej to o nią chodzi.
 *
 *  ZGŁOSZONE też: „nie wiem, który jest nowszy". Stąd data przy każdym przycisku
 *  i wyróżniona ta, którą zrobiono NAJPÓŹNIEJ. Wyróżnienie jest podwójne — ramka
 *  i podpis — bo sam kolor nie jest informacją dla każdego, a to jest wybór,
 *  którego nie da się cofnąć jednym kliknięciem.
 *
 *  Daty pobieramy przy WEJŚCIU na grę z konfliktem, nie w tle: pytanie o chmurę
 *  to rclone (ZMIERZONE: 4,4 s), a konflikt ma jedna gra na dwadzieścia. */
export function ConflictChoice({
  titleKey,
  busy,
  onChoose,
  firstRef,
}: {
  titleKey: string;
  busy: boolean;
  onChoose: (skad: Skad) => void;
  /** Zaznaczenie po wejściu z ekranu gry ma wylądować na PIERWSZEJ akcji. Przy grze
   *  z rozjazdem pierwszą akcją są te przyciski, a nie „Synchronizuj tę grę" —
   *  tamtego wtedy w ogóle nie ma na ekranie. */
  firstRef?: { current: HTMLDivElement | null };
}) {
  const [opcje, setOpcje] = useState<ConflictOptions | null>(null);

  useEffect(() => {
    let zywy = true;
    setOpcje(null);
    conflictOptions(titleKey)
      .then((wynik) => {
        if (zywy) setOpcje(wynik);
      })
      // cisza wyglądałaby jak „wciąż pytam"; bez dat przyciski i tak muszą działać
      .catch(() => {
        if (zywy) setOpcje(null);
      });
    return () => {
      zywy = false;
    };
  }, [titleKey]);

  // Chmura WYŁĄCZONA w ustawieniach („tylko karta") znaczy, że tej drogi nie ma —
  // pokazywanie martwego przycisku obiecywałoby wybór, którego nie ma. Dopóki nie znamy
  // odpowiedzi z backendu, pokazujemy wszystkie trzy: znikający przycisk wyglądałby
  // gorzej niż przycisk, który przez chwilę czeka na datę.
  const widoczne = OPISY.filter(({ skad }) => !(skad === "cloud" && opcje?.cloud.enabled === false));

  return (
    <>
      <div style={{ color: "#ffb347", fontWeight: "bold" }}>⚠ {t("ui.conflict_banner.heading")}</div>
      <div style={{ fontSize: "0.85em", marginTop: "4px" }}>{t("ui.conflict_banner.desc")}</div>
      <Focusable
        style={{
          display: "grid",
          // `auto-fit` liczy kolumny z tego, ILE pozycji naprawdę jest: przy chmurze
          // wyłączonej zostają dwie i zajmują całą szerokość, zamiast zostawiać pustą
          // trzecią kolumnę.
          gridTemplateColumns: `repeat(${widoczne.length}, minmax(0, 1fr))`,
          gap: "10px",
          marginTop: "10px",
        }}
      >
        {widoczne.map(({ skad, ikona: Ikona, przycisk, opis }, indeks) => {
          const dane = opcje?.[skad];
          const najnowsza = !!opcje && opcje.newest === skad;
          // Karty nie ma w czytniku → z karty nie ma jak wziąć. Przycisk zostaje
          // widoczny (żeby było wiadomo, że taka droga istnieje), ale nieklikalny.
          const nieczynny = skad === "card" && opcje?.card.present === false;
          return (
            <div key={skad} style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <DialogButton
                ref={indeks === 0 ? (firstRef as any) : undefined}
                style={{
                  width: "100%",
                  minWidth: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                  border: najnowsza ? "1px solid #6fcf6f" : "1px solid transparent",
                }}
                disabled={busy || nieczynny}
                onClick={() => onChoose(skad)}
              >
                <Ikona /> {t(przycisk)}
              </DialogButton>
              <div style={{ fontSize: "0.75em", opacity: 0.7, textAlign: "center" }}>
                {t(opis)}
              </div>
              <div
                style={{
                  fontSize: "0.78em",
                  textAlign: "center",
                  color: najnowsza ? "#6fcf6f" : undefined,
                  opacity: najnowsza ? 1 : 0.75,
                }}
              >
                {opcje ? kiedy(dane?.when) : t("ui.conflict.when_checking")}
                {najnowsza && ` · ${t("ui.conflict.newest")}`}
              </div>
            </div>
          );
        })}
      </Focusable>
    </>
  );
}
