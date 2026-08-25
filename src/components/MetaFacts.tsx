import { FaDesktop, FaSteam, FaVrCardboard } from "react-icons/fa";
import { SiSteamdeck } from "react-icons/si";

import { GameMetaRecord } from "../backend";
import { t } from "../i18n";
import { MetaScore } from "./MetaScore";

/** Fakty ze sklepu Steama w jednym układzie: data premiery i gatunki z oceną obok,
 *  potem opis i autor, a plakietki (tryby gry, chmura, osiągnięcia, zgodność ze
 *  sprzętem Valve) na samym DOLE. Kolejność jest z prośby użytkownika i ma sens:
 *  opis czyta się zdaniami, plakietki skanuje się wzrokiem — zdanie ma być pierwsze,
 *  a nie za trzema rzędami pastylek.
 *
 *  Jeden komponent dla DWÓCH miejsc (nasz ekran gier i karta na ekranie gry Steama),
 *  bo to ten sam zestaw informacji — dwie kopie rozjechałyby się przy pierwszej zmianie.
 *  Różni je tylko `clamp`: tam, gdzie pod spodem stoją przyciski, opis jest przycięty. */

const IKONY = { deck: SiSteamdeck, machine: FaDesktop, os: FaSteam, frame: FaVrCardboard };

function Ikona({ ksztalt }: { ksztalt: keyof typeof IKONY }) {
  const I = IKONY[ksztalt];
  return <I size={12} />;
}

/** Nazwy urządzeń są własne (Valve nie tłumaczy „Steam Deck"), więc nie idą przez
 *  katalog napisów — w przeciwieństwie do kategorii zgodności. */
const URZADZENIA: [keyof NonNullable<GameMetaRecord["compat"]>, string, keyof typeof IKONY][] = [
  ["deck", "Steam Deck", "deck"],
  ["machine", "Steam Machine", "machine"],
  ["steamos", "SteamOS", "os"],
  ["frame", "Steam Frame", "frame"],
];

/** 0 nietestowana, 1 niewspierana, 2 grywalna, 3 zweryfikowana — numeracja Valve.
 *  Kolor niesie tę samą informację co słowo, żeby dało się to przeczytać jednym
 *  spojrzeniem; samego koloru nigdzie nie zostawiamy bez słowa. */
const KATEGORIE: Record<number, { klucz: string; tlo: string }> = {
  0: { klucz: "ui.compat.untested", tlo: "#8b949e" },
  1: { klucz: "ui.compat.unsupported", tlo: "#ff6b6b" },
  2: { klucz: "ui.compat.playable", tlo: "#ffcc33" },
  3: { klucz: "ui.compat.verified", tlo: "#66cc33" },
};

/** Pastylka WYPEŁNIONA, nie obwiedziona — ZGŁOSZONE z urządzenia: obwiedzione zlewały
 *  się z pastylkami gatunków. Hierarchia jest teraz taka: gatunek to etykieta (słabsza),
 *  tryb gry to fakt (neutralne wypełnienie), zgodność to ostrzeżenie (kolor stanu).
 *  Kolor NIGDY nie jest jedynym nośnikiem — słowo zostaje w każdej pastylce, bo sam
 *  kolor nie działa dla części ludzi i ginie na zrzucie w skali szarości. */
function Plakietka({
  tekst,
  tlo,
  ikona,
}: {
  tekst: string;
  tlo?: string;
  ikona?: React.ReactNode;
}) {
  const kolorowa = !!tlo;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        fontSize: "0.78em",
        fontWeight: kolorowa ? 600 : 400,
        padding: "2px 8px",
        borderRadius: "10px",
        background: tlo ?? "rgba(255, 255, 255, 0.16)",
        color: kolorowa ? "#0a0f15" : "inherit",
        whiteSpace: "nowrap",
      }}
    >
      {ikona}
      {tekst}
    </span>
  );
}

export function MetaFacts({
  meta,
  clamp,
  onlyDevice,
}: {
  meta: GameMetaRecord;
  clamp?: number;
  /** Pokaż zgodność TYLKO tego urządzenia („deck" / „machine" / „steamos").
   *
   *  ZGŁOSZONE: „napisy w pastylkach zgodności zajmują dużo miejsca, może lepiej zrobić
   *  jedną?". Trzy pastylki po ~24 znaki zjadały pół szerokości karty na ekranie gry,
   *  a człowiek stoi przy JEDNYM urządzeniu — więc tam pokazujemy jego odpowiedź.
   *  Pełne rozbicie zostaje na naszym ekranie, gdzie miejsce jest i gdzie było
   *  uzasadnieniem istnienia tej karty (cztery urządzenia zamiast jednego wiersza Valve). */
  onlyDevice?: "deck" | "machine" | "steamos";
}) {
  const naglowek = [meta.release_date, (meta.genres || []).join(", ")]
    .filter(Boolean)
    .join(" · ");
  // Bez powtórzeń: ZMIERZONE na 007 First Light, gdzie autor i wydawca to ta sama
  // firma — wychodziło „IO Interactive A/S · IO Interactive A/S".
  const autorzy = Array.from(
    new Set([...(meta.developers || []), ...(meta.publishers || [])].filter(Boolean)),
  );
  // Przy `onlyDevice` bierzemy JEGO wiersz, a gdy Valve nie ma danych dla tego
  // urządzenia — SteamOS jako odpowiedź ogólną. Nie zmyślamy: gdy nie ma i tego,
  // pastylki po prostu nie ma.
  const urzadzenia = onlyDevice
    ? URZADZENIA.filter(([pole]) => pole === onlyDevice).concat(
        typeof meta.compat?.[onlyDevice] === "number"
          ? []
          : URZADZENIA.filter(([pole]) => pole === "steamos"),
      )
    : URZADZENIA;
  const zgodnosc = urzadzenia.map(([pole, nazwa, ksztalt]) => {
    const kat = meta.compat?.[pole];
    return typeof kat === "number" && KATEGORIE[kat]
      ? { nazwa, ksztalt, ...KATEGORIE[kat] }
      : null;
  }).filter(Boolean) as {
    nazwa: string;
    ksztalt: keyof typeof IKONY;
    klucz: string;
    tlo: string;
  }[];

  const plakietki: { tekst: string }[] = [
    ...(meta.modes || []).map((tryb) => ({ tekst: tryb })),
    ...(meta.cloud ? [{ tekst: "Steam Cloud" }] : []),
    ...(typeof meta.achievements === "number"
      ? [{ tekst: t("ui.meta.achievements", { count: meta.achievements }) }]
      : []),
  ];

  return (
    <>
      {(naglowek || typeof meta.metacritic === "number") && (
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {naglowek && <div style={{ fontSize: "0.9em", opacity: 0.85 }}>{naglowek}</div>}
          <MetaScore score={meta.metacritic} />
        </div>
      )}

      {meta.description && (
        <div
          style={{
            fontSize: "0.85em",
            opacity: 0.8,
            lineHeight: 1.35,
            ...(clamp
              ? {
                  display: "-webkit-box",
                  WebkitLineClamp: clamp,
                  WebkitBoxOrient: "vertical" as const,
                  overflow: "hidden",
                }
              : {}),
          }}
        >
          {meta.description}
        </div>
      )}

      {autorzy.length > 0 && (
        <div style={{ fontSize: "0.85em", opacity: 0.7 }}>{autorzy.join(" · ")}</div>
      )}
      {/* Tryby gry i zgodność ze sprzętem to DWA rzędy, ale jedna myśl („czym to jest
          i czy pójdzie"), więc trzymają się bliżej siebie niż reszty faktów. Bez tego
          wpadały w rytm akapitów i wyglądały jak dwie osobne sekcje. */}
      {(plakietki.length > 0 || zgodnosc.length > 0) && (
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          {plakietki.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
              {plakietki.map((p) => (
                <Plakietka key={p.tekst} tekst={p.tekst} />
              ))}
            </div>
          )}
          {zgodnosc.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
              {zgodnosc.map((z) => (
                <Plakietka
                  key={z.nazwa}
                  tekst={`${z.nazwa}: ${t(z.klucz)}`}
                  tlo={z.tlo}
                  ikona={<Ikona ksztalt={z.ksztalt} />}
                />
              ))}
            </div>
          )}
        </div>
      )}

    </>
  );
}
