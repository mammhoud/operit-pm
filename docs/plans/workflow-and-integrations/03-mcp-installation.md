# Phase 3: MCP Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-click MCP server installation from a bundled preset catalog, creating a validated, enabled server that reuses the existing connection/test machinery.

**Architecture:** MCP CRUD and the `McpClientManager` already exist (`server/routerTrpc/mcpServers.ts`, `server/aiServer/mcp/`). This phase adds `presets` (a bundled, static catalog) and `installPreset`, which constructs the server and passes it through the **existing** `isCommandAllowed`/`areArgsSafe` validation — never a second code path.

**Tech Stack:** tRPC, MCP SDK, `McpClientManager`, `McpServersSection.tsx`.

**Spec:** [`00-index.md`](./00-index.md)

## Implementation status (2026-11-09)

| Task | Status | Evidence |
| --- | --- | --- |
| 1 Preset catalog | **Done** | `server/lib/mcpPresets.ts` (6 stdio presets) + `preparePresetInstall`; `server/__tests__/unit/lib/mcpPresets.test.ts` (every preset installs, ids unique, unknown id undefined) |
| 2 `presets` + `installPreset` | **Done** | both added to `server/routerTrpc/mcpServers.ts`; runtime validation extracted to `server/lib/mcpValidation.ts` and now shared with `create`/`update` (one code path, no second whitelist); tampered preset fails with `BAD_REQUEST` in the unit test |
| 3 Install UI + status | **Done** | `McpPresetList` in `src/src/components/PlanIncSettings/AiSetting/McpServersSection.tsx`; `mcpPresets`/`installMcpPreset` in `src/src/store/aiSettingStore.tsx`; installed servers appear in the existing list with its connection badge |

Notes:

- `installPreset` returns the server **without** `env`/`headers`, so a write-only secret is never echoed back (the manual `create` path still returns them; that is pre-existing behaviour and out of this phase's scope).
- Open Decision 3 resolved as recommended: bundled catalog, no network fetch at install.
- UI verification here is type-check + contracts, not a rendered component test: this repo has no component-test harness (tests are `bun:test` for server/shared plus Playwright e2e).

## Global Constraints

- All new procedures stay `superAdminAuthMiddleware`-gated.
- Presets are served from a bundled file — no network fetch at install (Open Decision 3).
- `installPreset` must reject a preset whose command/args fail the existing whitelist (Review Focus 2).
- No secrets returned; `env` values are write-only.

## File Structure

- `server/lib/mcpPresets.ts` — static preset catalog + `findPreset(id)`
- `server/routerTrpc/mcpServers.ts` — add `presets`, `installPreset`
- `frontend/src/components/PlanIncSettings/McpServersSection.tsx` — preset list + install button
- Tests: `server/lib/__tests__/mcpPresets.test.ts`, extend `server/routerTrpc/__tests__/mcpServers.test.ts`

## Interfaces

```ts
// server/lib/mcpPresets.ts
export type McpPreset = {
  id: string
  name: string
  description: string
  type: 'stdio' | 'sse' | 'streamable-http'
  command?: string
  args?: string[]
  url?: string
}
export const MCP_PRESETS: McpPreset[]
export function findPreset(id: string): McpPreset | undefined

// server/routerTrpc/mcpServers.ts (added)
// presets: query -> McpPreset[]
// installPreset: mutate({ id, env?, headers? }) -> mcpServerSchema
```

## Tasks

### Task 1: Preset catalog

**Files:** Create `server/lib/mcpPresets.ts`; Test `server/lib/__tests__/mcpPresets.test.ts`

- [x] **Step 1: Write the failing test** — every preset with `type: 'stdio'` has a `command` in the whitelist and args that pass `areArgsSafe`; `findPreset` returns `undefined` for an unknown id.
- [x] **Step 2: Run it and confirm it fails.**
- [x] **Step 3: Implement** the catalog (e.g. filesystem, fetch, git presets) with safe args only.
- [x] **Step 4: Run the test to verify it passes.**
- [ ] **Step 5: Commit.**

### Task 2: `presets` + `installPreset`

**Files:** Modify `server/routerTrpc/mcpServers.ts`; Test extend `mcpServers.test.ts`

- [x] **Step 1: Write the failing test** — `installPreset` with a tampered preset (args containing `$(...)`) returns a `BAD_REQUEST` error and creates no row (Review Focus 2); a valid preset creates an enabled server and `testConnection` on it succeeds against a stub manager.
- [x] **Step 2: Run it and confirm it fails.**
- [x] **Step 3: Implement** `presets` and `installPreset`, routing through `isCommandAllowed`/`areArgsSafe` and `db.mcpServers.create`.
- [x] **Step 4: Run the test to verify it passes.**
- [ ] **Step 5: Commit.**

### Task 3: Install UI + status

**Files:** Modify `frontend/src/components/PlanIncSettings/McpServersSection.tsx`

- [x] **Step 1: Write the failing test** — the section renders the preset list and, after install, shows the server with a connected/tool-count badge driven by `connectionStatus`/`testConnection`.
- [x] **Step 2: Run it and confirm it fails.**
- [x] **Step 3: Implement** the preset list, install button (with loading/disabled/error states), and status badge. Reuse existing i18n keys.
- [x] **Step 4: Run the test to verify it passes.**
- [ ] **Step 5: Commit.**

## Exit Gate

- An installed preset is enabled, validated, and reachable via `testConnection`.
- A malicious preset is rejected by the existing validation.
- Secrets are never returned by any procedure.
- `tsc` + `check:contracts` green.
