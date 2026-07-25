import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import type { RiskFeasibilityResult } from './api/types'
import {
  feasibilityViewReducer,
  formatDecimalString,
  initialFeasibilityState,
  isResultFresh,
  type FeasibilityViewState,
} from './riskFeasibility'
import { feasibilityResult } from './test/utils'

const now = Date.parse('2026-07-22T10:00:00Z')
const resultAt = (
  sourceSignalId: string,
  freshUntil: number,
  status: RiskFeasibilityResult['status'] = 'FEASIBLE',
): RiskFeasibilityResult => ({
  ...feasibilityResult,
  source_signal_id: sourceSignalId,
  status,
  recommendation: status === 'FEASIBLE'
    ? 'PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW'
    : status === 'INFEASIBLE' ? 'DO_NOT_FORCE_MINIMUM_LOT' : 'RETRY_WITH_VALID_FRESH_DATA',
  snapshot_timestamps: {
    ...feasibilityResult.snapshot_timestamps,
    fresh_until: new Date(freshUntil).toISOString(),
  },
})

const start = (signalId = 'signal-a', generation = 1): FeasibilityViewState => feasibilityViewReducer(
  { ...initialFeasibilityState, activeSignalId: signalId },
  { type: 'REQUEST_STARTED', signalId, generation },
)

describe('risk feasibility display helpers', () => {
  it('formats plain decimal strings without binary number conversion or precision loss', () => {
    expect(formatDecimalString('12345678901234567890.01000000000000000001'))
      .toBe('12,345,678,901,234,567,890.01000000000000000001')
    expect(formatDecimalString('0.009999999999999999999')).toBe('0.009999999999999999999')
    expect(formatDecimalString(null)).toBe('Unavailable')
  })

  it('treats the exact fresh-until boundary as current and the next millisecond as stale', () => {
    const result = resultAt('signal-a', now)
    expect(isResultFresh(result, now)).toBe(true)
    let state = feasibilityViewReducer(start(), {
      type: 'REQUEST_SUCCEEDED', signalId: 'signal-a', generation: 1, result, now,
    })
    expect(state.phase).toBe('current')
    state = feasibilityViewReducer(state, { type: 'CLOCK_TICK', now: now + 1 })
    expect(state).toMatchObject({ phase: 'stale', result: null })
  })

  it('handles unavailable, error, source replacement, and superseded completions safely', () => {
    let state = feasibilityViewReducer(start(), {
      type: 'REQUEST_SUCCEEDED', signalId: 'signal-a', generation: 1,
      result: resultAt('signal-a', now + 1000, 'UNAVAILABLE'), now,
    })
    expect(state.phase).toBe('unavailable')

    state = feasibilityViewReducer(start('signal-a', 2), {
      type: 'REQUEST_FAILED', signalId: 'signal-a', generation: 2, message: 'safe error',
    })
    expect(state).toMatchObject({ phase: 'error', error: 'safe error', result: null })

    state = feasibilityViewReducer(start('signal-a', 2), {
      type: 'REQUEST_SUCCEEDED', signalId: 'signal-a', generation: 1,
      result: resultAt('signal-a', now + 1000), now,
    })
    expect(state.phase).toBe('loading')

    state = feasibilityViewReducer(state, { type: 'SIGNAL_CHANGED', signalId: 'signal-b' })
    expect(state).toMatchObject({ phase: 'idle', activeSignalId: 'signal-b', result: null })
  })

  it('discards a current-generation response whose source does not match the active signal', () => {
    const state = feasibilityViewReducer(start(), {
      type: 'REQUEST_SUCCEEDED', signalId: 'signal-a', generation: 1,
      result: resultAt('signal-b', now + 1000), now,
    })
    expect(state).toMatchObject({ phase: 'stale', result: null })
  })
})

describe('Feature: risk-feasibility-analyzer, Property 16: UI displays only the current fresh result', () => {
  it('never exposes a feasible conclusion from an old generation, mismatched source, replacement, or stale snapshot', () => {
    fc.assert(fc.property(
      fc.constantFrom('signal-a', 'signal-b'),
      fc.boolean(),
      fc.boolean(),
      fc.boolean(),
      fc.integer({ min: 0, max: 3 }),
      (responseSource, completeOldFirst, replaceSignal, fresh, clockAdvance) => {
        let state: FeasibilityViewState = { ...initialFeasibilityState, activeSignalId: 'signal-a' }
        state = feasibilityViewReducer(state, { type: 'REQUEST_STARTED', signalId: 'signal-a', generation: 1 })
        state = feasibilityViewReducer(state, { type: 'REQUEST_STARTED', signalId: 'signal-a', generation: 2 })
        const expiry = fresh ? now + 2 : now - 1
        const oldCompletion = {
          type: 'REQUEST_SUCCEEDED' as const,
          signalId: 'signal-a', generation: 1,
          result: resultAt(responseSource, expiry), now,
        }
        const latestCompletion = {
          type: 'REQUEST_SUCCEEDED' as const,
          signalId: 'signal-a', generation: 2,
          result: resultAt(responseSource, expiry), now,
        }
        state = feasibilityViewReducer(state, completeOldFirst ? oldCompletion : latestCompletion)
        state = feasibilityViewReducer(state, completeOldFirst ? latestCompletion : oldCompletion)
        if (replaceSignal) state = feasibilityViewReducer(state, { type: 'SIGNAL_CHANGED', signalId: 'signal-b' })
        state = feasibilityViewReducer(state, { type: 'CLOCK_TICK', now: now + clockAdvance })

        if (state.phase === 'current') {
          expect(state.generation).toBe(2)
          expect(state.activeSignalId).toBe('signal-a')
          expect(state.result?.source_signal_id).toBe('signal-a')
          expect(state.result && isResultFresh(state.result, now + clockAdvance)).toBe(true)
          expect(replaceSignal).toBe(false)
        } else {
          expect(state.result?.status === 'FEASIBLE').toBe(false)
        }
      },
    ), { numRuns: 100 })
  })
})
