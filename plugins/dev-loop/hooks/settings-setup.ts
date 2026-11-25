// Pure helpers for the Setup section: parse `dl doctor --json` and decide auto-open.
// No engine calls live here, so every rule is unit-testable on its own. The row shape lives in
// the self-contained contract settings-state.d.ts (the manifest's `types`), imported back here.
import type { SetupRow, SetupStatus } from './settings-state'

export type { SetupRow, SetupStatus }

const STATUS: ReadonlySet<string> = new Set(['ok', 'red', 'optional'])

// One row only when it has the four string fields and a known status; anything else is dropped.
function asRow(value: unknown): SetupRow | null {
  if (typeof value !== 'object' || value === null) return null
  const row = value as Record<string, unknown>
  const strings = ['name', 'detail', 'fix'].every(key => typeof row[key] === 'string')
  if (!strings || !STATUS.has(row.status as string)) return null
  return {
    name: row.name as string, status: row.status as SetupStatus,
    detail: row.detail as string, fix: row.fix as string,
  }
}

export function parseRows(stdout: string): SetupRow[] {
  try {
    const value: unknown = JSON.parse(stdout)
    if (!Array.isArray(value)) return []
    return value.map(asRow).filter((row): row is SetupRow => row !== null)
  } catch {
    return []
  }
}

// A required row that is not ok. Optional rows (telemetry) are greyed, never red, so never here.
export const requiredReds = (rows: SetupRow[]): SetupRow[] =>
  rows.filter(row => row.status === 'red')

// Complete when at least one row was read and no required row is red.
export const isComplete = (rows: SetupRow[]): boolean =>
  rows.length > 0 && requiredReds(rows).length === 0

// Auto-open only when a required row is red; an optional-only gap never opens the pane.
export const shouldAutoOpen = (rows: SetupRow[]): boolean => requiredReds(rows).length > 0

// Changes exactly when the set of required reds changes, so the pane reopens only then.
export const redsSignature = (rows: SetupRow[]): string =>
  requiredReds(rows).map(row => row.name).sort().join(',')

// True when a Bash command ran `dl init`, so the pane re-checks right after setup.
export const ranDlInit = (command: string): boolean => /\bdl\s+init\b/.test(command)
