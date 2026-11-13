/**
 * Deterministic regression for the round-17 entry-preselection race.
 *
 * Run with: npm run test:unit  (node --experimental-strip-types --test)
 * No new dependency, no browser, no network: the ordering that used to depend on
 * luck is reproduced with explicit deferred promises.
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { resolvePrivateEntryTarget, type EntryTarget } from '../src/pages/interviewEntry.ts'

const confirmed = (id: number): EntryTarget => ({ id, parse_status: 'CONFIRMED' })
const draft = (id: number): EntryTarget => ({ id, parse_status: 'DRAFT' })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

test('late empty list still selects the target through the detail read', async () => {
  // The list request has not come back yet when the handler runs.
  const pending = deferred<EntryTarget[]>()
  const detailCalls: number[] = []
  const running = resolvePrivateEntryTarget(7, {
    list: () => pending.promise,
    detail: async id => { detailCalls.push(id); return confirmed(id) },
  })
  // Resolve the list *after* the handler started, with nothing in it.
  pending.resolve([])
  const result = await running
  assert.equal(result.status, 'SELECTED')
  assert.equal(result.status === 'SELECTED' && result.source, 'DETAIL')
  assert.deepEqual(detailCalls, [7])
})

test('a failing list is not fatal', async () => {
  const result = await resolvePrivateEntryTarget(7, {
    list: async () => { throw new Error('network down') },
    detail: async id => confirmed(id),
  })
  assert.equal(result.status, 'SELECTED')
  assert.equal(result.status === 'SELECTED' && result.source, 'DETAIL')
})

test('a listed confirmed target needs no detail request', async () => {
  let detailCalls = 0
  const result = await resolvePrivateEntryTarget(7, {
    list: async () => [confirmed(7)],
    detail: async id => { detailCalls += 1; return confirmed(id) },
  })
  assert.equal(result.status, 'SELECTED')
  assert.equal(result.status === 'SELECTED' && result.source, 'LIST')
  assert.equal(detailCalls, 0)
})

test('list arriving after detail resolution cannot undo a selection', async () => {
  // Both requests are in flight; the detail one completes first.
  const list = deferred<EntryTarget[]>()
  const detailGate = deferred<EntryTarget | null>()
  const select = async () => {
    const result = await resolvePrivateEntryTarget(9, {
      list: () => list.promise,
      detail: () => detailGate.promise,
    })
    return result
  }
  const running = select()
  // resolvePrivateEntryTarget awaits the list first: give it a draft row so it
  // falls through to the detail read, which then completes.
  list.resolve([draft(9)])
  detailGate.resolve(confirmed(9))
  const result = await running
  assert.equal(result.status, 'SELECTED')
  assert.equal(result.status === 'SELECTED' && result.source, 'DETAIL')
})

test('an unconfirmed target is reported as such, not as a generic 404', async () => {
  const result = await resolvePrivateEntryTarget(3, {
    list: async () => [draft(3)],
    detail: async () => draft(3),
  })
  assert.equal(result.status, 'MISSING')
  assert.equal(result.status === 'MISSING' && result.reason, 'NOT_CONFIRMED')
})

test("another user's target resolves to MISSING without leaking content", async () => {
  const result = await resolvePrivateEntryTarget(99, {
    list: async () => [],
    detail: async () => { const e = new Error('JOB_TARGET_NOT_FOUND'); throw e },
  })
  assert.equal(result.status, 'MISSING')
  assert.equal(result.status === 'MISSING' && result.reason, 'NOT_FOUND')
})

test('no hand-off means no request at all', async () => {
  let calls = 0
  const result = await resolvePrivateEntryTarget(0, {
    list: async () => { calls += 1; return [] },
    detail: async () => { calls += 1; return null },
  })
  assert.deepEqual(result, { status: 'MISSING', reason: 'NO_HANDOFF' })
  assert.equal(calls, 0)
})
