/** Rozbiór tekstu z katalogu tłumaczeń na bloki do wyrenderowania.
 *
 *  Po co to istnieje: treść kategorii ekranu „O wtyczce" jest JEDNYM hasłem w każdym
 *  katalogu (osiem kategorii rozbitych na osobne klucze per punkt to ~80 haseł zamiast
 *  16). Wcześniej renderowaliśmy to jako `white-space: pre-wrap`, czyli jeden ciąg
 *  tekstu — czytelny, ale bez żadnej struktury: punkty, kroki tutoriala i akapity
 *  wyglądały identycznie. ZGŁOSZONE: „opisy zrób ładniejsze, bardziej sformatowane".
 *
 *  Konwencje są te, których hasła i tak już używały, więc katalogów nie trzeba było
 *  przepisywać:
 *    „• "         → punkt listy
 *    „1. ", „2) " → krok numerowany
 *    linia z „:"  → nagłówek grupy (dwukropek zdejmujemy, nagłówek go nie potrzebuje)
 *    pusta linia  → koniec akapitu
 *
 *  Klasyfikacja idzie PO LINII, a nie po całym bloku, i to jest konieczne: tutoriale
 *  mają w haśle nagłówek i kroki BEZ pustej linii między nimi
 *  („Jak skonfigurować:\n1. …\n2. …"), więc reguła „cały blok jest listą" gubiłaby
 *  nagłówek albo rozbijała listę. */

export type Blok =
  | { rodzaj: "akapit"; tekst: string }
  | { rodzaj: "naglowek"; tekst: string }
  | { rodzaj: "lista"; punkty: string[] }
  | { rodzaj: "kroki"; punkty: string[] };

const PUNKT = /^[•·*]\s+/;
const KROK = /^\d+[.)]\s+/;

type Rodzaj = "akapit" | "naglowek" | "lista" | "kroki";

function rodzajLinii(linia: string): Rodzaj {
  if (PUNKT.test(linia)) return "lista";
  if (KROK.test(linia)) return "kroki";
  // Nagłówkiem jest KRÓTKA linia kończąca się dwukropkiem. Warunek długości nie jest
  // ozdobą: zdanie „Do wyboru są: zapis w grze, kopia na karcie…" też kończy się
  // dwukropkiem w środku, ale jako nagłówek wyglądałoby absurdalnie.
  if (linia.endsWith(":") && linia.length <= 60 && !PUNKT.test(linia)) return "naglowek";
  return "akapit";
}

function bezZnacznika(linia: string, rodzaj: Rodzaj): string {
  if (rodzaj === "lista") return linia.replace(PUNKT, "");
  if (rodzaj === "kroki") return linia.replace(KROK, "");
  if (rodzaj === "naglowek") return linia.slice(0, -1);
  return linia;
}

/** Tekst → bloki. Nigdy nie rzuca i nigdy nie gubi treści: linia, której nie umiemy
 *  zaklasyfikować, zostaje akapitem. Puste wejście daje pustą listę, więc wywołujący
 *  nie musi sprawdzać niczego przed pętlą. */
export function bloki(tekst: string): Blok[] {
  const out: Blok[] = [];
  for (const akapit of String(tekst ?? "").split(/\n\s*\n/)) {
    const linie = akapit.split("\n").map((l) => l.trim()).filter(Boolean);
    let biezacy: { rodzaj: Rodzaj; linie: string[] } | null = null;

    const domknij = () => {
      if (!biezacy) return;
      const { rodzaj, linie: zebrane } = biezacy;
      if (rodzaj === "lista") out.push({ rodzaj: "lista", punkty: zebrane });
      else if (rodzaj === "kroki") out.push({ rodzaj: "kroki", punkty: zebrane });
      else if (rodzaj === "naglowek") out.push({ rodzaj: "naglowek", tekst: zebrane[0] });
      // akapit: linie zawinięte w źródle scalamy spacją w jedno zdanie
      else out.push({ rodzaj: "akapit", tekst: zebrane.join(" ") });
      biezacy = null;
    };

    for (const linia of linie) {
      const rodzaj = rodzajLinii(linia);
      const tresc = bezZnacznika(linia, rodzaj);
      // nagłówek nigdy się nie skleja z sąsiadem: jest zawsze osobnym blokiem
      if (!biezacy || biezacy.rodzaj !== rodzaj || rodzaj === "naglowek") {
        domknij();
        biezacy = { rodzaj, linie: [tresc] };
      } else {
        biezacy.linie.push(tresc);
      }
    }
    domknij();
  }
  return out;
}
