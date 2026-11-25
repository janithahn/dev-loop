import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'dev-loop',
  component: 'Pane',
  requestId: 'dev-loop-settings',
  surface: 'terminal',
  props: {
    title: 'dev-loop',
    isFocused: false,
    bodyColumns: 100,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

const SECRET = 'Authorization=Bearer-secret-value'
const TELEMETRY_KEYS = [
  'CLAUDE_CODE_ENABLE_TELEMETRY',
  'DEV_LOOP_TELEMETRY',
  'OTEL_METRICS_EXPORTER',
  'OTEL_LOGS_EXPORTER',
  'OTEL_EXPORTER_OTLP_PROTOCOL',
]

type Ran = { exitCode: number, stdout: string, stderr: string }
// What a test adds: every argv run is kept, an answer may replace the default one, and opens are
// counted so a test can prove the pane did (or did not) auto-open.
interface Extra {
  calls?: string[][]
  answer?: (argv: string[]) => Ran | undefined
  opens?: unknown[]
}

// The engine beneath: dl answers a value per key, the settings file is one string.
function seed(on: any, file: { text: string }, writes: string[], extra: Extra = {}) {
  on('process.run', (_: unknown, e: { argv: string[] }) => ({
    value: (extra.calls?.push(e.argv), extra.answer?.(e.argv)) ?? (e.argv[1] === 'status'
      ? { exitCode: 0, stdout: 'chg: tasks 1/2; verify: not run', stderr: '' }
      : e.argv[2] === 'set'
        ? { exitCode: 1, stderr: 'dev-loop config: size.cap: a whole number in 50..1000', stdout: '' }
        : { exitCode: 0, stdout: `value-of-${e.argv[3]}`, stderr: '' }),
  }))
  on('fs.read', () => ({ value: file.text }))
  on('fs.write', (_: unknown, e: { text: string }) => {
    file.text = e.text
    writes.push(e.text)

    return { value: undefined }
  })
  on('env.get', () => ({ value: SECRET }))
  on('clock.every', () => ({ value: { cancel() {} } }))
  on('command.register', () => ({ value: { command: 'dev-loop-settings' } }))
  on('ui.open', (_: unknown, e: unknown) => (extra.opens?.push(e), { value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('tool.call', (_: unknown, e: { command?: string }) => ({ result: { stdout: e.command ?? '' } }))
}

// Rows a controlled `dl doctor --json` returns; a mutable holder lets Re-check change them.
const ROW = (name: string, status: string, fix = ''): object =>
  ({ name, status, detail: `${name}-detail`, fix })
const doctorAnswer = (held: { rows: object[] }) => (argv: string[]): Ran | undefined =>
  argv[1] === 'doctor' && argv[2] === '--json'
    ? { exitCode: 0, stdout: JSON.stringify(held.rows), stderr: '' }
    : undefined
const START = { cwd: '/', surface: 'terminal', isInteractive: true } as const

async function open($: any) {
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'dev-loop-settings' })

  return $.ui.mount(PANE)
}

test('the pane shows project values and the live status, not run included', async ($, on) => {
  seed(on, { text: '' }, [])
  const ui = await open($)

  expect((await ui.find({ key: 'f-size.cap' }))?.props.value).toBe('value-of-size.cap')
  expect(JSON.stringify(await ui.drawn())).toContain('verify: not run')
  await ui.unmount()
})

test('the toggle writes exactly the five keys and off removes them', async ($, on) => {
  const file = { text: JSON.stringify({ permissions: { allow: [] }, env: { KEEP: 'x' } }) }
  const writes: string[] = []
  seed(on, file, writes)
  const ui = await open($)

  await ui.press({ key: 'telemetry' })
  const onEnv = JSON.parse(writes[0] ?? '').env
  expect(Object.keys(onEnv).sort()).toEqual([...TELEMETRY_KEYS, 'KEEP'].sort())
  expect(onEnv.OTEL_EXPORTER_OTLP_PROTOCOL).toBe('http/protobuf')
  expect(JSON.parse(writes[0] ?? '').permissions).toEqual({ allow: [] })

  await ui.press({ key: 'telemetry' })
  expect(JSON.parse(file.text).env).toEqual({ KEEP: 'x' })
  await ui.unmount()
})

test('the endpoint is written, and the header value is never drawn', async ($, on) => {
  const file = { text: '' }
  seed(on, file, [])
  const ui = await open($)

  await ui.input({ key: 'f-OTEL_EXPORTER_OTLP_ENDPOINT', text: 'https://otlp.example/v1', kind: 'change' })
  await ui.press({ key: 's-OTEL_EXPORTER_OTLP_ENDPOINT' })
  expect(JSON.parse(file.text).env.OTEL_EXPORTER_OTLP_ENDPOINT).toBe('https://otlp.example/v1')
  expect(file.text).not.toContain(SECRET)
  const tree = JSON.stringify(await ui.drawn())
  expect(tree).toContain('Header present: yes')
  expect(tree).not.toContain('secret-value')
  await ui.unmount()
})

test('a refused value shows the refusal line and keeps the draft', async ($, on) => {
  seed(on, { text: '' }, [])
  const ui = await open($)

  await ui.input({ key: 'f-size.cap', text: '5', kind: 'change' })
  await ui.press({ key: 's-size.cap' })
  expect(JSON.stringify(await ui.drawn())).toContain('size.cap: a whole number in 50..1000')
  expect((await ui.find({ key: 'f-size.cap' }))?.props.value).toBe('5')
  await ui.unmount()
})

const ok = (stdout: string): Ran => ({ exitCode: 0, stdout, stderr: '' })
// The v0.2 keys as dl answers them; `set` succeeds so the saved value is the one read back.
function v02(argv: string[], config: Record<string, string>): Ran | undefined {
  if (argv[0] === 'gh') return { exitCode: 0, stdout: `Token: ${SECRET}`, stderr: SECRET }
  if (argv[1] === 'status') {
    return ok('alpha: tasks 1/2; verify: not run; debt open: 3\nbeta: tasks 2/2; debt open: 0')
  }
  if (argv[2] === 'set') {
    config[argv[3] ?? ''] = argv[4] ?? ''

    return ok('')
  }
  if (argv[2] === 'get' && (argv[3] ?? '') in config) return ok(config[argv[3] ?? ''] ?? '')

  return undefined
}

test('the fast-track toggle writes review.fast_track through dl config set', async ($, on) => {
  const calls: string[][] = []
  const config = { 'review.fast_track': 'false' }
  seed(on, { text: '' }, [], { calls, answer: argv => v02(argv, config) })
  const ui = await open($)

  expect(JSON.stringify(await ui.drawn()))
    .toContain('one review round; serious findings fixed, the rest recorded as debt')
  await ui.press({ key: 'fast-track' })
  expect(calls).toContainEqual(['dl', 'config', 'set', 'review.fast_track', 'true'])
  expect(JSON.stringify(await ui.drawn())).toContain('Fast track: on')
  await ui.press({ key: 'fast-track' })
  expect(calls).toContainEqual(['dl', 'config', 'set', 'review.fast_track', 'false'])
  await ui.unmount()
})

test('the briefs choice writes briefs.enforce', async ($, on) => {
  const calls: string[][] = []
  const config = { 'briefs.enforce': 'warn' }
  seed(on, { text: '' }, [], { calls, answer: argv => v02(argv, config) })
  const ui = await open($)

  await ui.press({ key: 'briefs-block' })
  expect(calls).toContainEqual(['dl', 'config', 'set', 'briefs.enforce', 'block'])
  expect(config['briefs.enforce']).toBe('block')
  await ui.press({ key: 'briefs-warn' })
  expect(calls).toContainEqual(['dl', 'config', 'set', 'briefs.enforce', 'warn'])
  await ui.unmount()
})

test('the github host row shows gh login presence and no token text', async ($, on) => {
  const config = { 'host.provider': 'github' }
  seed(on, { text: '' }, [], { answer: argv => v02(argv, config) })
  const ui = await open($)

  const tree = JSON.stringify(await ui.drawn())
  expect(tree).toContain('host.provider: github')
  expect(tree).toContain('gh login: present')
  expect(tree).not.toContain('secret-value')
  expect(tree).not.toContain('Token')
  await ui.unmount()
})

test('the live section shows the open debt of each change', async ($, on) => {
  seed(on, { text: '' }, [], { answer: argv => v02(argv, {}) })
  const ui = await open($)

  const tree = JSON.stringify(await ui.drawn())
  expect(tree).toContain('alpha debt open: 3')
  expect(tree).toContain('beta debt open: 0')
  await ui.unmount()
})

test('the Setup section shows red fixes, greys optional, reports complete', async ($, on) => {
  const held = { rows: [ROW('dev-loop.yaml', 'red', 'run dl init'), ROW('telemetry', 'optional')] }
  seed(on, { text: '' }, [], { answer: doctorAnswer(held) })
  const ui = await open($)

  const tree = JSON.stringify(await ui.drawn())
  // The red row is a plain line carrying its fix; the optional row is a greyed (dimColor) line.
  expect(tree).toContain('"children":["dev-loop.yaml: dev-loop.yaml-detail — fix: run dl init"]')
  expect(tree).toContain('"props":{"dimColor":true},"children":["telemetry: telemetry-detail"]')
  expect(tree).not.toContain('Setup complete')

  held.rows = [ROW('telemetry', 'optional')] // the gap is fixed; no required row is red
  await ui.press({ key: 'recheck' })
  expect(JSON.stringify(await ui.drawn())).toContain('Setup complete')
  await ui.unmount()
})

test('the pane auto-opens at session.start when a required row is red', async ($, on) => {
  const opens: unknown[] = []
  const held = { rows: [ROW('dev-loop.yaml', 'red')] }
  seed(on, { text: '' }, [], { answer: doctorAnswer(held), opens })

  await $.session.start(START)
  expect(opens.length).toBeGreaterThan(0)
})

test('the pane does not auto-open when only optional rows are not ok', async ($, on) => {
  const opens: unknown[] = []
  const rows = [ROW('Python', 'ok'), ROW('telemetry', 'optional')]
  seed(on, { text: '' }, [], { answer: doctorAnswer({ rows }), opens })

  await $.session.start(START)
  expect(opens.length).toBe(0)
})

test('a dl init Bash call reopens the pane only when its set of reds changes', async ($, on) => {
  const opens: unknown[] = []
  const held = { rows: [ROW('dev-loop.yaml', 'red'), ROW('rules imported', 'red')] }
  seed(on, { text: '' }, [], { answer: doctorAnswer(held), opens })

  await $.session.start(START) // opens once on the two reds
  const afterStart = opens.length
  expect(afterStart).toBeGreaterThan(0)

  await $.tool.call({ tool: 'Bash', command: 'dl init', tool_use_id: 't1' }) // same reds
  expect(opens.length).toBe(afterStart) // unchanged set: no reopen

  held.rows = [ROW('dev-loop.yaml', 'red')] // one red fixed; the set changed
  await $.tool.call({ tool: 'Bash', command: 'dl init', tool_use_id: 't2' })
  expect(opens.length).toBeGreaterThan(afterStart) // reopened on the changed set
})
