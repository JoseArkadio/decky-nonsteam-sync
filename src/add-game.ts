import { Candidate, GameRecord, games, registerGame, seedPlaytime, setAppid, syncAll } from "./backend";
import { fetchAndApplyArtwork } from "./artwork";
import { fromBackend, t } from "./i18n";
import {
  addShortcut,
  entriesForTitle,
  nameOf,
  removeShortcut,
  setCompatTool,
  setShortcutName,
  setShortcutPath,
  steamPlaytimeSeconds,
  syncCardCollection,
} from "./steam";

const PROTON = "proton_experimental";

export interface ScanSummary {
  added: Array<{ title: string; appid: number }>;
  /** Gry, dla których PRZEJĘLIŚMY kafelek użytkownika zamiast robić drugi. */
  adopted: Array<{ title: string; appid: number }>;
  /** Gry z WIĘCEJ NIŻ JEDNYM istniejącym kafelkiem na ten sam plik — nie zgadujemy,
   *  w którym prefiksie jest postęp, więc decyzja wraca do człowieka. */
  ambiguous: Array<{ title: string; appids: number[] }>;
  /** Co się stało z kolekcjami kart: „SD512: +3 −1". */
  collections: string[];
  /** Klucze gier, które WŁAŚNIE weszły do rejestru — dla nich trzeba zapytać kartę
   *  o zapis. ZGŁOSZONE: gra dodana na drugim urządzeniu startowała od zera, choć
   *  kopia leżała obok niej na karcie. */
  fresh: string[];
  /** Gry, którym przywrócono zapis zaraz po dodaniu, i te z rozjazdem do rozstrzygnięcia. */
  restored: string[];
  conflicts: string[];
  /** Gry, których nie ma już na WŁOŻONEJ karcie — kafelek można zdjąć. */
  goneFromCard: string[];
  unchanged: string[];
  renamed: string[];
  needTitle: string[];
  alreadyInLibrary: string[];
  errors: string[];
  noArtworkKey?: boolean;
  note?: string;
}

export function emptySummary(): ScanSummary {
  return { added: [], adopted: [], ambiguous: [], collections: [], fresh: [], restored: [], conflicts: [], goneFromCard: [], unchanged: [], renamed: [], needTitle: [], alreadyInLibrary: [], errors: [] };
}

/** Dodaje JEDNĄ grę z karty: rejestr → kafelek → Proton → appid w rejestrze → grafiki.
 *
 *  Kolejność nie jest przypadkowa: appid trafia do rejestru zaraz po utworzeniu
 *  kafelka, bo kafelek bez wpisu w rejestrze jest sierotą (kolejny skan zrobiłby
 *  drugi). Grafiki są na końcu i ich brak nie unieważnia dodanej gry.
 *
 *  Wynik dopisuje do `summary` zamiast go zwracać, bo wywołujący sumują wiele gier
 *  w jeden komunikat. `title` jest osobnym argumentem, a nie brany z `candidate`:
 *  przy grze nierozpoznanej przez Ludusaviego podaje go użytkownik.
 */
export async function addOne(
  candidate: Candidate,
  title: string,
  withArtwork: boolean,
  summary: ScanSummary,
): Promise<void> {
  try {
    const record = await registerGame(
      candidate.folder,
      title,
      candidate.exe_abs,
      candidate.card_label,
      candidate.steam_appid_file !== null,
    );
    if (record.error || !record.title_key) {
      throw new Error(record.error ? fromBackend(record.error) : t("ui.registry_no_entry"));
    }

    // nasz kafelek już istnieje — nic nie dublujemy (sprawdzamy po appid z rejestru,
    // nie po nazwie: kafelek pod cudzą nazwą trzeba poprawić, a nie dodać drugi raz)
    const ourName = record.appid ? nameOf(record.appid) : null;
    if (ourName !== null) {
      if (ourName !== title) {
        setShortcutName(record.appid as number, title);
        summary.renamed.push(title);
      } else {
        summary.unchanged.push(title);
      }
      return;
    }
    // Kafelek na TEN SAM plik .exe już istnieje (użytkownik dodał go ręcznie, może
    // dawno) → PRZEJMUJEMY go zamiast robić drugi. To nie kosmetyka: nowy kafelek
    // dostaje od Steama nowy appid, więc i nowy prefiks Protona — a postęp gracza
    // siedzi w prefiksie tego STAREGO. Drugi kafelek zostawiłby zapisy na boku.
    // Warunek `nameOf(...) !== null` jest konieczny: appid może pochodzić z martwego
    // katalogu `userdata` po migracji konta, a wtedy kafelka nie ma i przejęlibyśmy
    // wpis, którego Steam nie zna.
    // Tylko JEDEN pasujący kafelek wolno przejąć bez pytania. Przy kilku nie ma
    // sygnału, który rozstrzyga, w którym prefiksie siedzi postęp — ZMIERZONE na
    // Decku: świeżo odtworzony prefiks Gothica miał 1914 plików, a ten z prawdziwymi
    // zapisami 606, więc „większy prefiks" ani „nowszy" niczego nie dowodzą.
    // Zgadnięcie znaczyłoby kopiowanie i przywracanie zapisów z cudzego prefiksu.
    const live = (candidate.adopt ?? []).filter((entry) => nameOf(entry.appid) !== null);
    if (live.length > 1) {
      summary.ambiguous.push({ title, appids: live.map((entry) => entry.appid) });
    }
    const adoptable = live.length === 1 ? live[0] : undefined;
    if (adoptable) {
      const stored = await setAppid(record.title_key, adoptable.appid);
      if (stored.error) {
        throw new Error(t("ui.adopt_tile_failed", { appid: adoptable.appid, detail: fromBackend(stored.error) }));
      }
      // Nazwa MUSI być kanoniczna: Ludusavi rozwiązuje prefiks gry non-Steam po
      // nazwie ze `shortcuts.vdf` (ZMIERZONE), więc kafelek nazwany po swojemu
      // znaczy „nie znajdę zapisów tej gry".
      if (nameOf(adoptable.appid) !== title) {
        setShortcutName(adoptable.appid, title);
        summary.renamed.push(title);
      }
      // Czas gry przejmujemy RAZEM z kafelkiem: to jedyny ślad sesji sprzed wtyczki
      // (licznik Steama żyje w localconfig.vdf, niezależnie od prefiksu). Bez tego
      // kafelek po przejęciu pokazuje 0,0 min, bo nasz patch nadpisuje liczbę Steama
      // naszą — i wygląda to na zgubioną historię.
      const before = steamPlaytimeSeconds(adoptable.appid);
      if (before > 0) await seedPlaytime(adoptable.appid, before);
      summary.adopted.push({ title, appid: adoptable.appid });
      summary.fresh.push(record.title_key);
      return;
    }
    // cudze kafelki o tej samej NAZWIE, ale wskazujące inny plik (inna kopia gry,
    // skrót ogłoszony przez drugie urządzenie) zostają nietknięte — plugin rusza
    // wyłącznie wpisy z własnego rejestru — ale użytkownik musi wiedzieć, że
    // zobaczy tę grę w bibliotece więcej niż raz
    if (entriesForTitle(title).length > 0) summary.alreadyInLibrary.push(title);

    const exe = candidate.exe_abs;
    const appid = await addShortcut(title, exe, exe.substring(0, exe.lastIndexOf("/")));
    if (!appid) throw new Error(t("ui.steam_no_appid"));
    setCompatTool(appid, PROTON);
    const stored = await setAppid(record.title_key, appid);
    if (stored.error) {
      // appid tylko w Steamie = kafelek-sierota, którego następny skan zdubluje;
      // cofamy własny kafelek, żeby nie zostawić stanu w połowie
      removeShortcut(appid);
      throw new Error(
        t("ui.appid_not_saved_reverted", { appid, detail: fromBackend(stored.error) }),
      );
    }
    summary.added.push({ title, appid });
    summary.fresh.push(record.title_key);

    if (withArtwork) {
      const problems = await fetchAndApplyArtwork(record.title_key, title, appid);
      if (problems.length) {
        summary.errors.push(t("ui.artwork_problems", { title, detail: problems.join("; ") }));
      }
    }
  } catch (err) {
    summary.errors.push(`${title}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/** Po skanie: odświeżenie ścieżek kafelków i kolekcje kart — jeden przebieg po
 *  rejestrze, bo oba potrzebują tej samej listy.
 *
 *  Ścieżka kafelka MUSI być odświeżana, bo Steam trzyma ją NA SZTYWNO, a punkt
 *  montowania karty się zmienia — po przezwaniu karty („1281db6f-…" → „Karta 1")
 *  wszystkie jej gry przestały się uruchamiać (ZGŁOSZONE z urządzenia). Robimy to
 *  po REJESTRZE, nie po kandydatach ze skanu: kandydatem nie zostaje gra, której
 *  Ludusavi nie nazywa po folderze (tytuł podał człowiek) — a takie trzy gry
 *  zostały wtedy ze starym kafelkiem.
 *
 *  Kolekcja na kartę: nazwa = etykieta karty. Gry z karty wjeżdżają, nasze gry,
 *  których na niej nie ma — wyjeżdżają.
 *
 *  Kartę NIEOBECNĄ w czytniku pomijamy w całości. Inaczej samo wyjęcie karty
 *  opróżniałoby jej kolekcję, a przy włożeniu trzeba by ją odbudować — i wyglądałoby
 *  to jak gubienie gier z biblioteki. */
export async function finishAdding(summary: ScanSummary): Promise<void> {
  await syncCollections(summary);
  await restoreFreshSaves(summary);
}

/** Zapis z karty dla gier, które WŁAŚNIE zostały dodane.
 *
 *  ZGŁOSZONE z urządzenia: „dodałem grę na Decku, pograłem, potem dodałem ją na Machine
 *  i save się nie zsynchronizował — zacząłem od nowa". Rejestr i kafelek powstawały, ale
 *  nikt nie pytał karty, czy obok gry leży kopia — a to jest DOKŁADNIE ten moment,
 *  w którym trzeba zapytać: prefiks Protona jest świeży, więc nie ma czego stracić,
 *  a karta jest w czytniku, bo właśnie z niej skanowaliśmy.
 *
 *  Robotę wykonuje zwykły przebieg synchronizacji na wskazanych grach, a nie własna
 *  ścieżka: to on ma tabelę decyzyjną, kopię bezpieczeństwa przed przywróceniem
 *  i bramkę uruchomionej gry. Druga kopia tej logiki rozjechałaby się przy pierwszej
 *  zmianie. Faza karty idzie bez sieci i w typowym przypadku kończy robotę, więc gra
 *  z kopią na karcie nie czeka na chmurę.
 *
 *  Wynik dopisujemy do `summary`, bo cisza po przywróceniu wygląda tak samo jak cisza
 *  po jego braku — a to różnica między „masz swój postęp" a „zaczynasz od zera". */
export async function restoreFreshSaves(summary: ScanSummary): Promise<void> {
  if (!summary.fresh.length) return;
  try {
    const out = await syncAll(summary.fresh);
    summary.restored.push(...(out.restored ?? []));
    summary.conflicts.push(...(out.conflicts ?? []));
    if (out.errors?.length) {
      summary.errors.push(...out.errors.map((e) => fromBackend(e)));
    }
  } catch (err) {
    summary.errors.push(
      t("ui.fresh_sync_failed", { detail: err instanceof Error ? err.message : String(err) }),
    );
  }
}

async function syncCollections(summary: ScanSummary): Promise<void> {
  let records: GameRecord[];
  try {
    records = await games();
  } catch (err) {
    summary.errors.push(
      t("ui.collections_list_failed", { detail: err instanceof Error ? err.message : String(err) }),
    );
    return;
  }
  const managed = records.map((r) => r.appid).filter((a): a is number => !!a);
  for (const record of records) {
    // tylko gry, których karta jest w czytniku: ścieżka do gry na wyjętej karcie
    // nie istnieje i wpisanie jej do kafelka nic nie naprawia
    if (!record.appid || !record.available || !record.exe_abs) continue;
    const dir = record.exe_abs.substring(0, record.exe_abs.lastIndexOf("/"));
    try {
      setShortcutPath(record.appid, record.exe_abs, dir);
    } catch (err) {
      summary.errors.push(
        t("ui.tile_path_refresh_failed", {
          title: record.title,
          detail: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  }
  const cards = new Map<string, number[]>();
  for (const record of records) {
    if (!record.appid || !record.card_label || !record.card_present) continue;
    const onCard = cards.get(record.card_label) ?? [];
    if (record.available) onCard.push(record.appid);
    else summary.goneFromCard.push(record.title);
    cards.set(record.card_label, onCard);
  }
  for (const [label, onCard] of cards) {
    try {
      const out = await syncCardCollection(label, onCard, managed);
      if (out.created) summary.collections.push(t("ui.collection_created", { label, added: out.added }));
      else if (out.added || out.removed) {
        summary.collections.push(`${label}: +${out.added} −${out.removed}`);
      }
    } catch (err) {
      // cisza wyglądałaby jak „kolekcje nie są obsługiwane"; to API Steama jest
      // nieudokumentowane i musi być widać, kiedy Valve je zmieni
      summary.errors.push(
        t("ui.collection_failed", { label, detail: err instanceof Error ? err.message : String(err) }),
      );
    }
  }
}

export function describe(s: ScanSummary): string {
  const parts = [t("ui.summary.added", { count: s.added.length })];
  if (s.adopted.length) {
    parts.push(t("ui.summary.adopted", { count: s.adopted.length }));
  }
  if (s.ambiguous.length) {
    parts.push(
      t("ui.summary.ambiguous", {
        count: s.ambiguous.length,
        list: s.ambiguous.map((a) => `${a.title} (${a.appids.join(", ")})`).join("; "),
      }),
    );
  }
  if (s.unchanged.length) parts.push(t("ui.summary.unchanged", { count: s.unchanged.length }));
  if (s.renamed.length) parts.push(t("ui.summary.renamed", { count: s.renamed.length }));
  if (s.needTitle.length) {
    parts.push(t("ui.summary.need_title", { count: s.needTitle.length, list: s.needTitle.join(", ") }));
  }
  if (s.alreadyInLibrary.length) {
    parts.push(t("ui.summary.already_in_library", { count: s.alreadyInLibrary.length }));
  }
  if (s.restored.length) {
    parts.push(t("ui.summary.restored", { count: s.restored.length, list: s.restored.join(", ") }));
  }
  if (s.conflicts.length) {
    parts.push(t("ui.summary.conflicts", { count: s.conflicts.length, list: s.conflicts.join(", ") }));
  }
  if (s.collections.length) parts.push(t("ui.summary.collections", { list: s.collections.join(", ") }));
  if (s.goneFromCard.length) {
    parts.push(
      t("ui.summary.gone_from_card", { count: s.goneFromCard.length, list: s.goneFromCard.join(", ") }),
    );
  }
  if (s.noArtworkKey) parts.push(t("ui.summary.no_artwork_key"));
  if (s.errors.length) parts.push(t("ui.summary.errors", { count: s.errors.length }));
  if (s.note) parts.push(s.note);
  return parts.join(", ");
}
