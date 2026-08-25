# Contributing

Thanks for looking. A few things about this codebase that will save you time.

## Where logic is allowed to live

All decision logic is in Python under `py_modules/sdsync/` and is testable
without a device. **No engine module imports `decky`** — only `main.py` does.
Keep it that way; it is the reason the test suite runs on a laptop.

The frontend does only what exists solely in Steam's API (shortcuts, Proton,
hiding, artwork) and renders backend state.

## The four files allowed to touch undocumented Steam APIs

`src/steam.ts`, `src/steam-page-patch.tsx`, `src/steam-badges.tsx` and
`src/steam-game-info.ts`. Those are four *different* failure modes — a renamed
method, a rearranged game page, a rearranged library DOM, a changed store cache —
fixed in four different ways, which is why they are four files. Don't add a fifth
without the same justification.

The first three must fail loudly. `steam-game-info.ts` fails quietly on purpose:
it fills in Valve's own tab with information the plugin's own card already shows
more fully, so losing it costs decoration, not information.

## How changes get made

1. **A failing test first**, then the smallest fix. For a bug, the test must be
   one that would have caught it.
2. **Mutation-check anything in the save path**: break the fix in a copy of the
   code and confirm the test goes red. A green test that survives broken logic is
   worse than no test.
   Restore with `cp`, not `git checkout` — `git checkout` silently does nothing on
   an untracked file and leaves the mutation in place.
3. **Measure, don't guess**, for anything touching Steam, Ludusavi or the cloud.
   Never invent a tool's output format; capture a real one into `tests/fixtures/`.
4. **`pnpm build` passing is not proof the plugin loads.** It reports TypeScript
   problems as warnings and still exits 0. Read its output, and check
   `journalctl -u plugin_loader` on the device.

## Running things

```bash
pytest tests/ -q     # or: uvx pytest tests/ -q
pnpm install
pnpm build
pnpm typecheck       # types only, not behaviour
pnpm check           # the one runnable frontend check (Node 23+)
```

## Interface language

UI strings live in `src/i18n/{en,pl}.json` and are keyed, never inlined. English
is the reference catalogue; every key must exist in both.

`tests/test_i18n.py` enforces a budget in **characters** on button labels — 22 in
a two-column grid, 14 in a three-column one. This is not cosmetic: a label that
wraps makes its button half a line taller than its neighbour, and Steam's gamepad
navigation is spatial, so the wrap breaks navigation. Characters rather than
pixels is deliberate — the check has to fail on a laptop, before deployment.

## Commits

Code, identifiers and commit messages in English. Conventional-commit prefixes
(`feat:`, `fix:`, `docs:`) are used but not enforced.
