import { test } from 'node:test'
import assert from 'node:assert/strict'
import { knowledgeStateLabel } from '../src/types/adaptive-interview.ts'

test('knowledge observations describe this interview rather than permanent skill mastery', () => {
  assert.equal(knowledgeStateLabel('DEMONSTRATED_IN_SESSION'), '本场已有充分回答依据')
  assert.equal(knowledgeStateLabel('INSUFFICIENT_EVIDENCE'), '本轮回答依据不足')
  assert.equal(knowledgeStateLabel('UNKNOWN'), '尚无明确观察')
})
