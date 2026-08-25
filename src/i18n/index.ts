import en from "./en.json";
import pl from "./pl.json";

export type Params = Record<string, string | number>;
type Forms = { other: string } & Partial<Record<Intl.LDMLPluralRule, string>>;
type Catalog = Record<string, string | Forms>;

/** Jedyne miejsce z listą języków. Dołożenie `de.json` to import plus wpis tutaj —
 *  nazwę własną („Deutsch") niesie sam plik pod `lang.own_name`, więc selektor
 *  w Ustawieniach nie wymaga żadnej zmiany. */
export const CATALOGS: Record<string, Catalog> = { pl, en };

export const FALLBACK = "en";

let current = FALLBACK;
const listeners = new Set<() => void>();

/** Kod języka bez regionu. ZMIERZONE na Decku, że region nie zmienia formatowania:
 *  `pl` i `pl-PL` dają `12.08.2025, 14:00:00`, `en` i `en-US` dają `8/12/2025,
 *  2:00:00 PM`. Obsługa regionu byłaby kodem bez skutku. */
export function normalize(code: string | null | undefined): string | null {
  const base = String(code ?? "").trim().toLowerCase().split(/[-_]/)[0];
  return base && base in CATALOGS ? base : null;
}

export function langNow(): string {
  return current;
}

/** Kod dla `toLocaleString` i `Intl.*`. Osobna nazwa, bo to inne pytanie niż
 *  „którym katalogiem mówimy" i wywołania daty nie mają wiedzieć o katalogach. */
export function locale(): string {
  return current;
}

export function setLang(code: string | null | undefined): void {
  const next = normalize(code);
  if (!next || next === current) return;
  current = next;
  // kopia zbioru: nasłuch może się odpiąć w trakcie powiadamiania (odmontowany
  // komponent), a mutacja Set w trakcie iteracji gubi pozostałe wpisy
  for (const fn of Array.from(listeners)) {
    try {
      fn();
    } catch {
      // jeden padnięty nasłuch nie może zablokować pozostałych
    }
  }
}

export function onLangChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function fill(text: string, params?: Params): string {
  if (!params) return text;
  // nieznane pole zostaje DOSŁOWNIE (`{foo}`): dziura w zdaniu ma być widoczna,
  // a nie wyglądać jak gotowy komunikat
  return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole);
}

function form(forms: Forms, params?: Params): string {
  const count = Number(params?.count);
  if (!Number.isFinite(count)) return forms.other;
  // ZMIERZONE na Decku: Intl.PluralRules("pl") daje 1→one, 2→few, 5→many, 22→few,
  // czyli „1 gra", „2 gry", „5 gier", „22 gry". Własnej reguły nie potrzebujemy.
  const rule = new Intl.PluralRules(current).select(count);
  return forms[rule] ?? forms.other;
}

function entryOf(key: string): string | Forms | undefined {
  return CATALOGS[current]?.[key] ?? CATALOGS[FALLBACK]?.[key];
}

/** Napis własny frontendu. Brak hasła oddaje KLUCZ — widocznie brzydki, bo cisza
 *  w tym miejscu wygląda jak „nie ma nic do pokazania". */
export function t(key: string, params?: Params): string {
  const entry = entryOf(key);
  if (entry === undefined) return key;
  return fill(typeof entry === "string" ? entry : form(entry, params), params);
}

/** Cokolwiek, co niesie kod, parametry albo gotowe zdanie.
 *
 *  Typ jest STRUKTURALNY, nie `Msg`, i to jest konieczne: `LogEntry` ma `code`
 *  OPCJONALNE (wpis sprzed wielojęzyczności go nie ma), więc nie jest przypisywalne
 *  do `Msg`, gdzie `code` jest wymagane. ZWERYFIKOWANE `tsc`: przy sygnaturze
 *  `Msg | string` kompilator odrzuca `fromBackend(entry)` z Taska 8 błędem TS2345
 *  („Types of property 'code' are incompatible"). */
export type Renderable = { code?: string; params?: Params; message?: string };

/** Komunikat z Pythona.
 *
 *  Kolejność jest tu istotna: sprawdzamy hasło w KATALOGU BIEŻĄCEGO języka, nie przez
 *  `t()`, bo `t()` spada na `en`, a `en.json` celowo nie ma haseł `err.*` — angielski
 *  dla nich jest w `messages.py` i przyjeżdża w `m.message`.
 *
 *  Napis zamiast obiektu to STARY wpis w logu (plik na urządzeniu użytkownika sprzed
 *  tej zmiany) albo wpis zgłoszony przez frontend przez RPC `log_add`. Obie drogi
 *  muszą zostać czytelne. */
export function fromBackend(m: Renderable | string | null | undefined): string {
  if (!m) return "";
  if (typeof m === "string") return m;
  const key = m.code ? `err.${m.code}` : "";
  if (key && CATALOGS[current]?.[key] !== undefined) return t(key, m.params);
  return m.message || m.code || "";
}
