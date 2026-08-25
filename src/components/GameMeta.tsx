import { MetaFacts } from "./MetaFacts";
import { GameMetaRecord } from "../backend";
import { t } from "../i18n";

const MUTED = { fontSize: "0.85em", opacity: 0.7 };

interface Props {
  meta: GameMetaRecord | null;
  problem: string | null;
  busy: boolean;
}

/** Metadane gry ze sklepu Steama na naszym ekranie gier — SAMA TREŚĆ.
 *
 *  Przycisku „Odśwież opis" tu nie ma i to jest decyzja, nie przeoczenie. ZGŁOSZONE
 *  z urządzenia: „przycisk jest nieklikalny przy użyciu kontrolera, trzeba użyć myszki".
 *  Siedział w `Focusable` zagnieżdżonym w karcie informacji, a ta karta ma własne
 *  `onActivate` (jest przystankiem dla strzałki w górę — patrz GameDetails). Rodzic
 *  przyjmujący zaznaczenie staje się JEDYNYM przystankiem w swoim poddrzewie, więc
 *  do przycisku nie dało się dojechać. Dziś odświeżanie stoi w siatce akcji, gdzie
 *  żaden przodek zaznaczenia nie przechwytuje.
 *
 *  Stan pobierania przychodzi z góry (`useGameMeta` woła GameDetails), bo ten sam hak
 *  obsługuje teraz i tę kartę, i przycisk w akcjach — dwa wywołania znaczyłyby dwa
 *  zapytania do sklepu na jedno wejście na grę. */
export function GameMeta({ meta, problem, busy }: Props) {
  if (problem) {
    return (
      <div style={{ ...MUTED, color: "#ff6b6b" }}>
        {t("ui.meta.failed")} {problem}
      </div>
    );
  }
  if (!meta) return busy ? <div style={MUTED}>{t("ui.meta.loading")}</div> : null;
  if (meta.missing) return <div style={MUTED}>{t("ui.meta.missing")}</div>;

  // Opis przycięty, bo pod spodem stoją przyciski akcji — na ekranie gry Steama ten
  // sam blok idzie bez przycięcia, tam nic pod nim nie ma.
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <MetaFacts meta={meta} clamp={4} />
    </div>
  );
}
