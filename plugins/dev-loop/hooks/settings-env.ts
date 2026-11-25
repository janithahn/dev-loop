import type { EngineInterface } from 'claude-code'

// The project's own, untracked settings file: the pane writes its env block only.
export const LOCAL = '.claude/settings.local.json'

// What "Send telemetry" switches on; switching it off removes exactly these.
export const TELEMETRY: Record<string, string> = {
  CLAUDE_CODE_ENABLE_TELEMETRY: '1',
  DEV_LOOP_TELEMETRY: '1',
  OTEL_METRICS_EXPORTER: 'otlp',
  OTEL_LOGS_EXPORTER: 'otlp',
  OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
}

export type EnvBlock = Record<string, string>

async function readDoc($: EngineInterface): Promise<Record<string, unknown>> {
  const text = await $.fs.read(LOCAL).catch(() => '')

  return text.trim() ? JSON.parse(text) : {}
}

export async function readEnv($: EngineInterface): Promise<EnvBlock> {
  const doc = await readDoc($).catch(() => ({}))

  return ((doc as { env?: EnvBlock }).env ?? {}) as EnvBlock
}

// A null removes the key; every other key of the file stays as it was.
export async function patchEnv(
  $: EngineInterface,
  patch: Record<string, string | null>,
): Promise<void> {
  const doc = await readDoc($)
  const env: EnvBlock = { ...((doc as { env?: EnvBlock }).env ?? {}) }
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete env[key]
    else env[key] = value
  }
  await $.fs.write(LOCAL, JSON.stringify({ ...doc, env }, null, 2) + '\n')
}

export const isOn = (env: EnvBlock): boolean =>
  Object.entries(TELEMETRY).every(([key, value]) => env[key] === value)

export const OFF: Record<string, null> = Object.fromEntries(
  Object.keys(TELEMETRY).map(key => [key, null]),
)
