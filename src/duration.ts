/** Czas gry po ludzku: „5 g 32 min".
 *
 *  Bez importów — dzięki temu funkcję da się sprawdzić poleceniem
 *  `node --experimental-strip-types plugin/checks/duration.check.ts`, bez runnera
 *  i bez nowej zależności w `package.json`.
 *
 *  Etykiety jednostek przychodzą z zewnątrz, bo są napisami interfejsu i pojadą
 *  do katalogów wielojęzyczności — funkcja ma zostać czysta i niezależna od języka.
 *
 *  Wejście spoza zakresu (ujemne, `NaN`, nieskończoność) daje „0 <minuty>", a nie
 *  „NaN min": rekord bez zmierzonego czasu nie może wyglądać na awarię. */
export function formatDuration(seconds: number, hourLabel: string, minuteLabel: string): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds / 60)) : 0;
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (hours === 0) return `${minutes} ${minuteLabel}`;
  if (minutes === 0) return `${hours} ${hourLabel}`;
  return `${hours} ${hourLabel} ${minutes} ${minuteLabel}`;
}
