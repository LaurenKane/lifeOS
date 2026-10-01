# Life OS — Frontend

React + TypeScript + Vite frontend for the Life OS personal-finance app.

## Commands

Run all commands from this directory (`frontend/`). This project uses **npm only**.

| Command             | What it does                                                        |
| ------------------- | ------------------------------------------------------------------- |
| `npm install`       | Install dependencies.                                               |
| `npm run dev`       | Vite dev server (default http://localhost:5173).                    |
| `npm run build`     | `tsc -b` typecheck, then production build into `dist/`.              |
| `npx tsc -b --noEmit` | The real typecheck. **Use this, not bare `npx tsc --noEmit`.**      |
| `npm run lint`      | oxlint.                                                              |
| `npm test`          | Vitest, single run.                                                  |
| `npm run test:watch`| Vitest in watch mode.                                                |
| `npm run test:coverage` | Vitest with V8 coverage.                                         |
| `npm run preview`   | Serve the built `dist/` locally (smoke test).                       |

### The `tsc` trap

`tsconfig.json` is a solution-style config with `files: []` and project
references. A bare `npx tsc --noEmit` therefore checks **nothing** and
exits 0 even when the app is broken. The only meaningful signals are:

```bash
npx tsc -b --noEmit   # or: npm run build
```

## Environment

| Variable              | Default | Notes                                                    |
| --------------------- | ------- | -------------------------------------------------------- |
| `VITE_API_BASE_URL`   | `/api`  | Root-relative path or absolute URL. No hardcoded host.    |

## Conventions

- Path alias `@/` → `src/`. Mirrored in `vite.config.ts`, `vitest.config.ts`
  and `tsconfig.app.json`; keep all three in sync.
- **Money is never a float.** Amounts are signed integer *minor units*
  (`amountMinor: z.number().int()`) alongside a `currency` code whose
  decimal count is the authoritative exponent. See `ARCHITECTURE.md` §6.
- `tsconfig.app.tsbuildinfo` is a build artifact and is not tracked.

## Structure

```
src/
  main.tsx                 entry point: createRoot + App
  routes/                  route table (index.tsx) and root layout
  features/finance/<name>/ per-feature stub: types / use-* hook / provider / page
  lib/apiClient.ts         typed fetch wrapper + shared Money schema
  lib/utils.ts             cn() className helper
  api/generated/           placeholder for OpenAPI-generated types
  components/ui/           shadcn components (added via `npx shadcn@latest add`)
tests/                     Vitest suites
```

## Adding a shadcn component

`components.json` is configured for the `new-york` style, CSS variables,
and the `@/` alias:

```bash
npx shadcn@latest add button
```

## Testing

Tests need no network and no running backend — `fetch` is stubbed per test.
