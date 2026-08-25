import type { ReactNode } from "react";

import { GameRecord } from "../backend";
import { formatDuration } from "../duration";
import { locale, t } from "../i18n";
import { cloudStatus, dotColor, tileState } from "../status";

/** Fakty o grze w siatce etykieta/wartość.
 *
 *  Powód istnienia tego komponentu jest wprost od użytkownika: cztery zdania `0.85em`
 *  jedno pod drugim, posklejane kropkami, czytały się jak formularz ustawień. Siatka
 *  daje oku punkt zaczepienia — etykiety w jednej kolumnie, wartości w drugiej.
 *
 *  Czas gry jest tu PIERWSZY raz w interfejsie poza kafelkiem Steama. Suma jest
 *  liczona z rejestru i z pliku wymiany na karcie, więc bez karty w czytniku pokazuje
 *  tylko to, co wie to urządzenie — ale nie maleje, bo rejestr trzyma dolną granicę
 *  (`playtime_total_seen`, patrz AGENTS.md). Rozbicie „u kogo ile" pokazujemy, bo bez
 *  niego suma z dwóch urządzeń wygląda jak pomyłka.
 *
 *  Jednostki czasu są SKRÓTAMI („5 g 32 min"), nie pełnymi słowami, i to jest decyzja
 *  o szerokości: kolumna wartości ma tu kilkaset pikseli i „5 godzin 32 minuty" zawija
 *  się do dwóch wierszy. Skrót nie odmienia się przez liczbę, więc nie potrzebuje
 *  liczby mnogiej — w odróżnieniu od liczby kafelków niżej, która ją ma. */
export function GameFacts({
  game,
  duplicates,
}: {
  game: GameRecord;
  duplicates: { visible: number[]; hidden: number[] };
}) {
  const disk = game.carrier === "disk";
  const backup = game.last_backup_ts;
  // Próg minuty, nie zera: ZMIERZONE na Decku, że gra z kilkoma sekundami w rejestrze
  // pokazywała „Czas gry: 0 min" — a to wygląda jak zepsuty licznik, nie jak „prawie
  // wcale nie grałem". Poniżej minuty nie ma czego pokazywać.
  const total = game.playtime_total ?? 0;
  const devices = Object.entries(game.playtime_devices ?? {}).sort((a, b) => b[1] - a[1]);
  const czas = (seconds: number) =>
    formatDuration(seconds, t("ui.duration.hour"), t("ui.duration.minute"));

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        columnGap: "14px",
        rowGap: "6px",
        fontSize: "0.85em",
        alignItems: "baseline",
      }}
    >
      <Label dot={dotColor(game)}>{disk ? t("ui.facts.disk") : t("ui.facts.card")}</Label>
      <Value>
        {disk
          ? game.available
            ? t("ui.facts.file_here")
            : t("ui.facts.file_gone")
          : `${game.card_label || "—"} · ${
              game.available ? t("ui.facts.card_here") : t("ui.facts.card_gone")
            }`}
      </Value>

      <Label>{t("ui.facts.saves")}</Label>
      <Value>
        {backup ? new Date(backup * 1000).toLocaleString(locale()) : t("ui.facts.no_backup")}
      </Value>

      <Label>{t("ui.facts.cloud")}</Label>
      <Value>{cloudStatus(game)}</Value>

      <Label>{t("ui.facts.tile")}</Label>
      <Value>
        {tileState(game)}
        {duplicates.visible.length > 0 &&
          ` · ${t("ui.facts.foreign", { count: duplicates.visible.length })}`}
        {duplicates.hidden.length > 0 &&
          ` · ${t("ui.facts.hidden", { count: duplicates.hidden.length })}`}
      </Value>

      <Label>{t("ui.facts.artwork")}</Label>
      <Value>{game.artwork_done ? t("ui.facts.artwork_done") : t("ui.facts.artwork_none")}</Value>

      {total >= 60 && (
        <>
          <Label>{t("ui.facts.playtime")}</Label>
          <Value>
            {czas(total)}
            {devices.length > 1 && (
              <div style={{ opacity: 0.6, marginTop: "2px" }}>
                {devices.map(([name, sec]) => `${name} ${czas(sec)}`).join(" · ")}
              </div>
            )}
          </Value>
        </>
      )}
    </div>
  );
}

function Label({ children, dot }: { children: ReactNode; dot?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "6px", opacity: 0.55, whiteSpace: "nowrap" }}>
      {dot && (
        <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: dot, flexShrink: 0 }} />
      )}
      {children}
    </div>
  );
}

function Value({ children }: { children: ReactNode }) {
  return <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>{children}</div>;
}
