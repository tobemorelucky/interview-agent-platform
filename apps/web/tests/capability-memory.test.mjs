import { test } from 'node:test'
import assert from 'node:assert/strict'
import { capabilityLabels, capabilityReasons } from '../src/types/capability-memory.ts'

test('capability states are understandable, not fake calibrated probabilities', () => {
  for (const state of ['UNKNOWN', 'GAP_SUSPECTED', 'PRACTICING', 'VERIFIED', 'RECHECK_DUE', 'CONFLICTED']) {
    assert.ok(capabilityLabels[state])
    assert.equal(capabilityLabels[state].includes('%'), false)
  }
  assert.match(capabilityLabels.VERIFIED, /独立验证/)
})
test('hint dependence and expired verification are separately explained', () => {
  assert.match(capabilityReasons.ASSISTED_SUCCESS_NOT_MASTERY, /暂不认定/)
  assert.match(capabilityReasons.VERIFICATION_EXPIRED, /复测/)
  assert.match(capabilityReasons.INDEPENDENT_RECHECK_REQUIRED, /不同表现/)
})
