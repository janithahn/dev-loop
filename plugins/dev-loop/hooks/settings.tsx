import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { OFF, TELEMETRY, isOn, patchEnv, readEnv } from './settings-env'
import {
  isComplete, parseRows, ranDlInit, redsSignature, shouldAutoOpen,
} from './settings-setup'
import type { SetupRow, View } from './settings-state'

const ID = 'dev-loop-settings'
const TITLE = 'dev-loop'
const ENDPOINT = 'OTEL_EXPORTER_OTLP_ENDPOINT'
const ATTRS = 'OTEL_RESOURCE_ATTRIBUTES'
// Editable leaves of dev-loop.yaml; the sections that are maps are shown, not edited.
const ROWS = ['host.org', 'host.project', 'host.repo', 'branches.integration',
  'branches.release', 'size.cap', 'size.advise', 'verify.runner', 'verify.affected']
const SHOWN = ['workers', 'build']
// v0.2: a toggle, a two-way choice and a read-only row, each written through `dl config set`.
const FAST = 'review.fast_track'
const BRIEFS = 'briefs.enforce'
const PROVIDER = 'host.provider'
const GH = 'gh login'

const EMPTY: View = {
  values: {}, drafts: {}, live: 'not run', note: '', telemetry: false, hasHeader: false,
  setup: [], openedSig: '',
}
// Change the tag when View's fields change: it declines what an earlier version stored.
const view = atom({ plugin: 'dev-loop', key: 'settings' } as const, EMPTY, {
  shape: 'view-2',
})
let timer: { cancel: () => void } | undefined

const dl = ($: EngineInterface, ...argv: string[]) =>
  $.process.run(['dl', ...argv]).catch(() => ({
    exitCode: 1, stdout: '', stderr: 'dl could not start',
  }))

// `dl doctor --json` only, so session.start can decide auto-open without the full refresh.
async function loadSetup($: EngineInterface): Promise<SetupRow[]> {
  const ran = await dl($, 'doctor', '--json')
  const setup = parseRows(ran.exitCode === 0 ? ran.stdout : '')
  await update($, view, v => ({ ...v, setup }))

  return setup
}

async function refresh($: EngineInterface): Promise<void> {
  await loadSetup($)
  const values: Record<string, string> = {}
  for (const key of [...ROWS, ...SHOWN, FAST, BRIEFS, PROVIDER]) {
    const ran = await dl($, 'config', 'get', key)
    values[key] = (ran.exitCode ? ran.stderr : ran.stdout).trim()
  }
  // Exit code only: `gh auth status` may print the token, so its output is dropped unread.
  if (values[PROVIDER] === 'github') {
    const gh = await $.process.run(['gh', 'auth', 'status']).catch(() => ({ exitCode: 1 }))
    values[GH] = gh.exitCode === 0 ? 'present' : 'absent'
  }
  const env = await readEnv($)
  values[ENDPOINT] = env[ENDPOINT] ?? ''
  values[ATTRS] = env[ATTRS] ?? ''
  const status = await dl($, 'status')
  const live = status.exitCode === 0 && status.stdout.trim() ? status.stdout.trim() : 'not run'
  // Presence only: the value is never kept, drawn or sent anywhere.
  const hasHeader = (await $.env.get('OTEL_EXPORTER_OTLP_HEADERS')) !== undefined
  await update($, view, v => ({ ...v, values, live, hasHeader, telemetry: isOn(env) }))
}

const say = ($: EngineInterface, note: string) => update($, view, v => ({ ...v, note }))

async function save($: EngineInterface, id: string): Promise<void> {
  const { drafts } = await read($, view)
  if (!(id in drafts)) return
  const isEnv = id.startsWith('OTEL_')
  if (isEnv) await patchEnv($, { [id]: drafts[id] || null })
  else {
    const ran = await dl($, 'config', 'set', id, drafts[id] ?? '')
    if (ran.exitCode) {
      await say($, ran.stderr.trim() || `${id}: refused`) // the refusal line, kept with the draft

      return
    }
  }
  await update($, view, v => ({
    ...v,
    drafts: Object.fromEntries(Object.entries(v.drafts).filter(([key]) => key !== id)),
  }))
  await refresh($)
  await say($, isEnv ? `${id} saved; applies next session` : `${id} saved`)
}

// A pressed choice is a draft saved at once: a refusal shows as any refused value does.
async function choose($: EngineInterface, id: string, value: string): Promise<void> {
  await update($, view, v => ({ ...v, drafts: { ...v.drafts, [id]: value } }))
  await save($, id)
}

// Each `dl status` line that counts debt, as "<change> debt open: <n>".
const debts = (live: string): string[] => live.split('\n').flatMap(line => {
  const found = /^([^:]+):.*; debt open: (\d+)/.exec(line)

  return found ? [`${found[1]} debt open: ${found[2]}`] : []
})

async function toggle($: EngineInterface): Promise<void> {
  const { telemetry } = await read($, view)
  await patchEnv($, telemetry ? OFF : TELEMETRY)
  await refresh($)
  await say($, 'Telemetry changes apply to the next Claude Code session.')
}

async function openPane($: EngineInterface): Promise<void> {
  await refresh($)
  timer?.cancel()
  timer = $.clock.every(30_000, () => void refresh($))
  await $.ui.open({ id: ID, title: TITLE })
}

// Open the pane when a required row is red, but only when its set of reds differs from the set
// the pane last opened on, so one session is not nagged twice over the same gap.
async function autoOpen($: EngineInterface, setup: SetupRow[]): Promise<void> {
  const sig = redsSignature(setup)
  const { openedSig } = await read($, view)
  if (!shouldAutoOpen(setup) || sig === openedSig) return
  await update($, view, v => ({ ...v, openedSig: sig }))
  await openPane($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'dev-loop-settings', description: 'Open the dev-loop settings pane' })
    // A fresh session may auto-open once; forget what an earlier session opened on.
    await update($, view, v => ({ ...v, openedSig: '' }))
    await autoOpen($, await loadSetup($))

    return next(e)
  })

  // Right after `dl init` runs in a Bash tool call, re-check and open the pane if a gap remains.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const out = await next(e)
    if (ranDlInit(e.command)) await autoOpen($, await loadSetup($))

    return out
  })

  on('command.run', { command: 'dev-loop-settings' }, async $ => {
    await openPane($)

    return { text: 'dev-loop settings opened.' }
  })

  on('ui.close', async ($, e, next) => {
    if (e.id === ID) timer?.cancel()

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: ID }, async ($, e) => {
    const { Box, Text, Input, Button } = $.ui.resolve(e)
    const v = await read($, view)
    const isFast = v.values[FAST] === 'true'
    const field = (id: string, label: string, placeholder?: string) => (
      <Box>
        <Input
          key={`f-${id}`}
          label={label}
          placeholder={placeholder}
          value={v.drafts[id] ?? v.values[id] ?? ''}
          onInput={text => void update($, view, s => ({ ...s, drafts: { ...s.drafts, [id]: text } }))}
          onSubmit={() => void save($, id)}
        />
        <Button key={`s-${id}`} onPress={() => void save($, id)}>Save</Button>
      </Box>
    )
    // Reds carry their fix; optional rows are greyed; ok rows are not listed one by one.
    const setupLine = (row: SetupRow) => row.status === 'optional'
      ? <Text key={`su-${row.name}`} dimColor>{`${row.name}: ${row.detail}`}</Text>
      : <Text key={`su-${row.name}`}>{`${row.name}: ${row.detail} — fix: ${row.fix}`}</Text>

    return (
      <Box flexDirection="column">
        <Text bold>Setup</Text>
        {isComplete(v.setup)
          ? <Text key="setup-done">Setup complete</Text>
          : v.setup.filter(row => row.status !== 'ok').map(setupLine)}
        <Button key="recheck" onPress={() => void refresh($)}>Re-check</Button>
        <Text bold>Project</Text>
        {ROWS.map(id => field(id, id))}
        {SHOWN.map(id => <Text>{`${id}: ${v.values[id] ?? ''}`}</Text>)}
        <Button key="fast-track" onPress={() => void choose($, FAST, String(!isFast))}>
          {`Fast track: ${isFast ? 'on' : 'off'}`}
        </Button>
        <Text dimColor>one review round; serious findings fixed, the rest recorded as debt</Text>
        <Box>
          <Text>{`${BRIEFS}: ${v.values[BRIEFS] ?? ''}`}</Text>
          {['warn', 'block'].map(mode => (
            <Button key={`briefs-${mode}`} onPress={() => void choose($, BRIEFS, mode)}>
              {mode}
            </Button>
          ))}
        </Box>
        <Text>{`${PROVIDER}: ${v.values[PROVIDER] ?? ''} (read-only)`}</Text>
        {GH in v.values && <Text>{`${GH}: ${v.values[GH]}`}</Text>}
        <Text bold>Telemetry (off by default)</Text>
        <Button key="telemetry" onPress={() => void toggle($)}>
          {`Send telemetry: ${v.telemetry ? 'on' : 'off'}`}
        </Button>
        {field(ENDPOINT, 'OTLP endpoint')}
        {field(ATTRS, 'Resource attributes', 'host.name=<machine>,dev_loop.repo=<repo>')}
        <Text>Set OTEL_EXPORTER_OTLP_HEADERS in your user settings env (it holds your token)</Text>
        <Text>{`Header present: ${v.hasHeader ? 'yes' : 'no'}`}</Text>
        <Text dimColor>Changes apply to the next Claude Code session.</Text>
        {v.note !== '' && <Text>{v.note}</Text>}
        <Text bold>Live</Text>
        {v.live.split('\n').map(line => <Text>{line}</Text>)}
        {debts(v.live).map(line => <Text>{line}</Text>)}
      </Box>
    )
  })
}
