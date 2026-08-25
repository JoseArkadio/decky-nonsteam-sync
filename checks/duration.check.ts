// Sprawdzian czystej funkcji bez żadnego runnera:
//   node --experimental-strip-types plugin/checks/duration.check.ts
// Leży POZA `src/`, żeby nie wchodzić ani w `tsc` (include: src/**/*), ani w bundle.
import assert from "node:assert";
import { formatDuration } from "../src/duration.ts";

// Do godziny mówimy samymi minutami — „0 g 32 min" nikt tak nie czyta.
assert.equal(formatDuration(0, "g", "min"), "0 min");
assert.equal(formatDuration(59, "g", "min"), "0 min");
assert.equal(formatDuration(60, "g", "min"), "1 min");
assert.equal(formatDuration(3599, "g", "min"), "59 min");
// Pełna godzina bez reszty NIE dostaje „0 min" na końcu.
assert.equal(formatDuration(3600, "g", "min"), "1 g");
assert.equal(formatDuration(3660, "g", "min"), "1 g 1 min");
assert.equal(formatDuration(19920, "g", "min"), "5 g 32 min");
// Etykiety przychodzą z zewnątrz, bo są napisami interfejsu.
assert.equal(formatDuration(19920, "h", "min"), "5 h 32 min");
// Śmieci z rejestru nie mogą wyprodukować „NaN min" na ekranie użytkownika.
assert.equal(formatDuration(-5, "g", "min"), "0 min");
assert.equal(formatDuration(Number.NaN, "g", "min"), "0 min");
assert.equal(formatDuration(Number.POSITIVE_INFINITY, "g", "min"), "0 min");

console.log("duration: ok");
