import { expect, test } from 'claude-code/testing'

import {
  isComplete, parseRows, ranDlInit, redsSignature, requiredReds, shouldAutoOpen,
} from './settings-setup'
import type { SetupRow } from './settings-state'

const row = (name: string, status: SetupRow['status']): SetupRow =>
  ({ name, status, detail: `${name} ${status}`, fix: status === 'red' ? `fix ${name}` : '' })

const RED = row('dev-loop.yaml', 'red')
const OPT = row('telemetry', 'optional')
const OK = row('Python', 'ok')

test('parseRows keeps valid rows and drops junk and bad json', () => {
  const good = JSON.stringify([
    { name: 'a', status: 'ok', detail: 'd', fix: '' },
    { name: 'b', status: 'nope', detail: 'd', fix: '' }, // unknown status: dropped
    { name: 'c', status: 'red', detail: 'd' }, // missing fix: dropped
    42, // not an object: dropped
  ])
  expect(parseRows(good).map(r => r.name)).toEqual(['a'])
  expect(parseRows('not json')).toEqual([])
  expect(parseRows('{"not":"an array"}')).toEqual([])
})

test('requiredReds is the red rows only, never the optional ones', () => {
  expect(requiredReds([RED, OPT, OK]).map(r => r.name)).toEqual(['dev-loop.yaml'])
})

test('isComplete is true only when rows were read and none is red', () => {
  expect(isComplete([])).toBe(false) // nothing read yet
  expect(isComplete([OK, OPT])).toBe(true) // optional-not-ok does not block
  expect(isComplete([OK, RED])).toBe(false)
})

test('shouldAutoOpen is true only on a required red', () => {
  expect(shouldAutoOpen([])).toBe(false)
  expect(shouldAutoOpen([OK, OPT])).toBe(false) // optional-only gap never opens
  expect(shouldAutoOpen([OK, RED])).toBe(true)
})

test('redsSignature is stable by set and changes when the reds change', () => {
  const a = row('rules imported', 'red')
  expect(redsSignature([RED, a, OK])).toBe(redsSignature([a, RED])) // order-independent
  expect(redsSignature([RED])).not.toBe(redsSignature([RED, a])) // a new red changes it
  expect(redsSignature([OK, OPT])).toBe('') // no reds: empty
})

test('ranDlInit matches a dl init command, not a lookalike', () => {
  expect(ranDlInit('dl init')).toBe(true)
  expect(ranDlInit('cd /repo && dl init discover')).toBe(true)
  expect(ranDlInit('dl  init')).toBe(true)
  expect(ranDlInit('dl initialize')).toBe(false)
  expect(ranDlInit('dl doctor')).toBe(false)
})
