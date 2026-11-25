// The contract for the settings pane's stored view. It must stay self-contained: the plugin
// manifest names it as `types`, and that file may not import. So the Setup row shape lives here
// and settings-setup.ts imports it back.
export type SetupStatus = 'ok' | 'red' | 'optional'

export type SetupRow = {
  name: string
  status: SetupStatus
  detail: string
  fix: string
}

export type View = {
  values: Record<string, string>
  drafts: Record<string, string>
  live: string
  note: string
  telemetry: boolean
  hasHeader: boolean
  setup: SetupRow[]
  openedSig: string
}

declare module 'claude-code' {
  interface PluginState {
    'dev-loop': { settings: Shaped<View> }
  }
}
