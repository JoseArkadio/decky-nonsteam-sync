// Uruchamialny sprawdzian rozbioru tekstu z katalogu tłumaczeń.
//
//   node --experimental-strip-types plugin/checks/richtext.check.ts
//
// Bez frameworka i bez nowej zależności — tak samo jak duration.check.ts, i z tego
// samego powodu: to jedyny sposób sprawdzić ZACHOWANIE czystej funkcji frontendu
// (`pnpm typecheck` sprawdza typy, nie zachowanie). Katalog `checks/` leży POZA
// `src/`, żeby nie wchodził w `tsc` i nie wymagał `allowImportingTsExtensions`.
import assert from "node:assert";
import { bloki } from "../src/richtext.ts";

// --- pusto ---
assert.deepEqual(bloki(""), [], "puste wejście → brak bloków");
assert.deepEqual(bloki("\n\n  \n"), [], "same białe znaki → brak bloków");

// --- akapit ---
assert.deepEqual(bloki("Zwykłe zdanie."), [{ rodzaj: "akapit", tekst: "Zwykłe zdanie." }]);

// --- dwa akapity rozdzielone pustą linią ---
assert.equal(bloki("Pierwszy.\n\nDrugi.").length, 2, "pusta linia kończy akapit");

// --- linie zawinięte w źródle scalają się w JEDEN akapit ---
assert.deepEqual(bloki("Pierwsza linia\ndruga linia"),
  [{ rodzaj: "akapit", tekst: "Pierwsza linia druga linia" }]);

// --- lista punktowana, znacznik zdjęty ---
assert.deepEqual(bloki("• jeden\n• dwa"),
  [{ rodzaj: "lista", punkty: ["jeden", "dwa"] }]);

// --- kroki numerowane, obie formy znacznika ---
assert.deepEqual(bloki("1. pierwszy\n2) drugi"),
  [{ rodzaj: "kroki", punkty: ["pierwszy", "drugi"] }]);

// --- SEDNO: nagłówek i kroki BEZ pustej linii między nimi ---
// Tak wyglądają wszystkie tutoriale w katalogach. Reguła „cały blok jest listą"
// gubiłaby tu nagłówek albo rozbijała listę.
assert.deepEqual(bloki("Jak skonfigurować:\n1. raz\n2. dwa"), [
  { rodzaj: "naglowek", tekst: "Jak skonfigurować" },
  { rodzaj: "kroki", punkty: ["raz", "dwa"] },
]);

// --- dwukropek W ŚRODKU długiego zdania to NIE nagłówek ---
const dlugie = "Do wyboru są trzy miejsca i każde ma własną datę, więc pytamy o każde osobno:";
assert.equal(bloki(dlugie)[0].rodzaj, "akapit", "długie zdanie z dwukropkiem zostaje akapitem");

// --- dwa nagłówki pod rząd nie sklejają się w jeden blok ---
assert.deepEqual(bloki("Pierwszy:\nDrugi:"), [
  { rodzaj: "naglowek", tekst: "Pierwszy" },
  { rodzaj: "naglowek", tekst: "Drugi" },
]);

// --- nic nie ginie: każde niepuste słowo wejścia musi być w wyjściu ---
const zrodlo = "Wstęp zdanie.\n\n• punkt jeden\n• punkt dwa\n\nJak zrobić:\n1. krok\n2. drugi krok\n\nKoniec.";
const zebrane = bloki(zrodlo)
  .map((b) => (b.rodzaj === "lista" || b.rodzaj === "kroki" ? b.punkty.join(" ") : b.tekst))
  .join(" ");
for (const slowo of ["Wstęp", "punkt", "dwa", "zrobić", "krok", "drugi", "Koniec."]) {
  assert.ok(zebrane.includes(slowo), `zgubione słowo: ${slowo}`);
}
assert.equal(bloki(zrodlo).length, 5, "wstęp, lista, nagłówek, kroki, zakończenie");

// --- funkcja nie rzuca na śmieciach ---
for (const paskudztwo of [undefined, null, 123, {}] as unknown[]) {
  assert.ok(Array.isArray(bloki(paskudztwo as string)), "zawsze tablica");
}

console.log("richtext: ok");
