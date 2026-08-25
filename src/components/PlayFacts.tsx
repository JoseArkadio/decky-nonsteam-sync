import { useEffect, useState } from "react";

import { HltbTimes, hltbTimes, playerCount, storeHltbTimes } from "../backend";
import { locale, t } from "../i18n";

const ETYKIETA = {
  fontSize: "0.8em",
  opacity: 0.5,
  letterSpacing: "0.05em",
  textTransform: "uppercase" as const,
};

/** Wiersz faktów o graniu: czas przejścia z HowLongToBeat, a po prawej — ilu gra teraz.
 *
 *  ZGŁOSZONE dwa razy, o dwie różne wtyczki: „fajnie byłoby zobaczyć, ile trwa gra
 *  bezpośrednio na naszej karcie" oraz „PlayCount pokazuje, ile osób gra teraz, ale
 *  działa tylko dla gier ze Steama — sprawdź, czy da się to u nas". Da się i dla obu
 *  rodzajów gier: liczba graczy idzie po APPIDZIE ZE SKLEPU, a ten mamy w metadanych
 *  także dla gry z karty SD (ZMIERZONE: Marvel Tōkon, 3389 grających).
 *
 *  Pytamy przy WEJŚCIU na ekran gry, nie w tle — dojście do HLTB to cztery żądania
 *  (patrz `sdsync/hltb.py`), a odpowiedź jest zapamiętana, więc drugie wejście na tę
 *  samą grę jest darmowe.
 *
 *  Trzy stany backendu widać na ekranie jako trzy różne rzeczy, nie jako jedną ciszę:
 *  liczby / „HLTB nie zna tej gry" / nic (gdy nie udało się zapytać — bo to stan
 *  przejściowy i następne wejście spróbuje ponownie). */
export function PlayFacts({
  titleKey,
  appid,
  steamAppid,
  name,
  compact,
}: {
  /** Nasza gra — klucz rejestru. Wyklucza się z `appid`. */
  titleKey?: string;
  /** Gra ze Steama, której nie mamy w rejestrze. */
  appid?: number;
  /** Gra w sklepie Steama — po niej pytamy, ilu gra teraz. Dla naszej gry z karty
   *  bierze się z metadanych, więc ta liczba działa także dla gier spoza Steama. */
  steamAppid?: number;
  name?: string;
  /** Karta na ekranie gry jest ciasna: liczby idą wtedy w jeden wiersz bez etykiet. */
  compact?: boolean;
}) {
  const [times, setTimes] = useState<HltbTimes | null>(null);
  const [busy, setBusy] = useState(false);
  // null = nie wiemy (jeszcze albo wcale). Zero jest PRAWDZIWĄ odpowiedzią i wygląda
  // inaczej niż brak, więc te dwa stany nie mogą się zlać.
  const [players, setPlayers] = useState<number | null>(null);

  useEffect(() => {
    let zywy = true;
    setPlayers(null);
    if (!steamAppid) return;
    playerCount(steamAppid)
      .then((wynik) => {
        if (zywy && typeof wynik.players === "number") setPlayers(wynik.players);
      })
      .catch(() => undefined); // liczba graczy to ozdoba: cisza jest tu proporcjonalna
    return () => {
      zywy = false;
    };
  }, [steamAppid]);

  useEffect(() => {
    let zywy = true;
    setTimes(null);
    if (!titleKey && !appid) return;
    setBusy(true);
    const pytanie = titleKey ? hltbTimes(titleKey) : storeHltbTimes(appid as number, name ?? "");
    pytanie
      .then((wynik) => {
        if (zywy) setTimes(wynik);
      })
      .catch(() => {
        if (zywy) setTimes(null); // „nie udało się" — nie udajemy, że gra nie ma czasu
      })
      .finally(() => {
        if (zywy) setBusy(false);
      });
    return () => {
      zywy = false;
    };
  }, [titleKey, appid]);

  const pozycje = times && !times.error && !times.missing
    ? [
        { klucz: "main", wartosc: times.main, etykieta: t("ui.hltb.main") },
        { klucz: "plus", wartosc: times.plus, etykieta: t("ui.hltb.plus") },
        { klucz: "full", wartosc: times.full, etykieta: t("ui.hltb.full") },
      ].filter((p) => typeof p.wartosc === "number")
    : [];

  /** Kolumna ONLINE. Po PRAWEJ i w tej samej linii co „Czas przejścia" (ZGŁOSZONE):
   *  nagłówek w tym samym stylu, pod nim biała liczba. Rysuje się także wtedy, gdy
   *  HowLongToBeat nie zna gry — to dwa różne źródła i jedno nie zależy od drugiego. */
  const online = players === null ? null : (
    <div style={{ textAlign: "right", flex: "0 0 auto" }}>
      <div style={ETYKIETA}>{t("ui.players.label")}</div>
      <div style={{ fontSize: compact ? "1.05em" : "1.15em", fontWeight: 600, color: "#fff" }}>
        {players.toLocaleString(locale())}
      </div>
    </div>
  );

  if (busy && !pozycje.length && players === null) {
    return <div style={{ opacity: 0.55, fontSize: "0.9em" }}>{t("ui.hltb.checking")}</div>;
  }
  // Awaria pytania NIE rysuje niczego: to stan przejściowy, a wiersz „nie udało się"
  // przy każdej grze bez sieci byłby szumem. Brak w bazie to co innego — to fakt o grze,
  // i mówimy o nim, ale tylko gdy nie ma czego postawić obok.
  if (!pozycje.length && players === null) {
    return times && times.missing
      ? <div style={{ opacity: 0.45, fontSize: "0.9em" }}>{t("ui.hltb.missing")}</div>
      : null;
  }

  return (
    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "18px" }}>
      {pozycje.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: compact ? "3px" : "5px", minWidth: 0 }}>
          <div style={ETYKIETA}>{t("ui.hltb.label")}</div>
          {/* ZGŁOSZONE: „po prawej jest wystarczająco miejsca, więc można by zwiększyć
              odstępy". Trzy liczby obok siebie czytają się jako jedna wartość, dopóki nie
              mają wokół siebie miejsca — a mają być trzema odpowiedziami na trzy pytania. */}
          <div style={{ display: "flex", gap: compact ? "22px" : "28px", flexWrap: "wrap" }}>
            {pozycje.map((p) => (
              <div key={p.klucz} style={{ display: "flex", flexDirection: "column", lineHeight: 1.15 }}>
                <span style={{ fontSize: compact ? "1.05em" : "1.15em", fontWeight: 600 }}>
                  {t("ui.hltb.hours", { hours: String(p.wartosc) })}
                </span>
                <span style={{ fontSize: "0.75em", opacity: 0.55 }}>{p.etykieta}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ opacity: 0.45, fontSize: "0.9em" }}>
          {times && times.missing ? t("ui.hltb.missing") : ""}
        </div>
      )}
      {online}
    </div>
  );
}
