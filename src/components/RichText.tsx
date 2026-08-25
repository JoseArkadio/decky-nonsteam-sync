import { bloki } from "../richtext";

/** Renderowanie treści z katalogu tłumaczeń: akapity, nagłówki grup, listy punktowane
 *  i kroki numerowane.
 *
 *  Zwykły `div`, nie `Focusable`, i to jest świadome: rodzic przyjmujący zaznaczenie
 *  jest JEDYNYM przystankiem w swoim poddrzewie (grabla z AGENTS.md), a to jest tekst
 *  do czytania — nie ma tu czego aktywować. Zaznaczenie zbierają przystanki wokół
 *  (spis kategorii po lewej, przyciski ekranu).
 *
 *  Odstęp MIĘDZY grupami jest wyraźnie większy niż wewnątrz grupy i to ta różnica, nie
 *  sama wielkość odstępu, robi porządek — ta sama reguła, która wyszła z „wygląda
 *  średnio, popracuj nad odstępami" przy MetaFacts. */

const ODSTEP_MIEDZY = "16px";

export function RichText({ tekst }: { tekst: string }) {
  const czesci = bloki(tekst);
  if (czesci.length === 0) return null;

  return (
    <div style={{ fontSize: "0.95em", lineHeight: 1.6 }}>
      {czesci.map((blok, i) => {
        // Pierwszy akapit kategorii jest LEADEM: jaśniejszy i o włos większy, żeby
        // dało się przeczytać samą pierwszą linijkę i wiedzieć, o czym jest kategoria.
        const lead = i === 0 && blok.rodzaj === "akapit";

        if (blok.rodzaj === "naglowek") {
          return (
            <div
              key={i}
              style={{
                marginTop: i === 0 ? 0 : "22px",
                marginBottom: "10px",
                fontSize: "0.8em",
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                opacity: 0.55,
              }}
            >
              {blok.tekst}
            </div>
          );
        }

        if (blok.rodzaj === "akapit") {
          return (
            <p
              key={i}
              style={{
                margin: `0 0 ${ODSTEP_MIEDZY} 0`,
                opacity: lead ? 1 : 0.82,
                fontSize: lead ? "1.04em" : undefined,
              }}
            >
              {blok.tekst}
            </p>
          );
        }

        const kroki = blok.rodzaj === "kroki";
        return (
          <div
            key={i}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "9px",              // wewnątrz grupy
              margin: `0 0 ${ODSTEP_MIEDZY} 0`,  // między grupami — wyraźnie więcej
            }}
          >
            {blok.punkty.map((punkt, j) => (
              <div key={j} style={{ display: "flex", gap: "10px", opacity: 0.82 }}>
                {/* Znacznik w KOLUMNIE stałej szerokości, nie w tekście: inaczej
                    zawinięty punkt zaczynałby drugą linię pod kropką, a nie pod
                    treścią. `flexShrink: 0`, bo długi punkt inaczej ściska znacznik. */}
                <div
                  style={{
                    flexShrink: 0,
                    width: kroki ? "1.5em" : "0.9em",
                    textAlign: kroki ? "right" : "left",
                    fontVariantNumeric: "tabular-nums",
                    fontWeight: kroki ? 700 : 400,
                    opacity: kroki ? 0.75 : 0.5,
                  }}
                >
                  {kroki ? `${j + 1}.` : "•"}
                </div>
                <div>{punkt}</div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
