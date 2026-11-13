/**
 * Entry hand-off resolution for a private JD target.
 *
 * Round 17 defect: the one-click hand-off read `jobTargets`, which a *parallel*
 * `listTargets()` request fills. When that request resolved last, the handler
 * took the "target does not exist" branch, showed an alert and silently dropped
 * the preselection. The fix awaits the list and falls back to an owner-scoped
 * detail read, so the outcome no longer depends on request ordering.
 *
 * It lives here, free of Vue, so the ordering can be tested deterministically
 * instead of being rediscovered by hand in a browser.
 */

export type EntryTarget = { id: number; parse_status: string }

export type EntryResolution<T extends EntryTarget = EntryTarget> =
  | { status: 'SELECTED'; target: T; source: 'LIST' | 'DETAIL' }
  | { status: 'MISSING'; reason: 'NO_HANDOFF' | 'NOT_FOUND' | 'NOT_CONFIRMED' }

export type EntryDeps<T extends EntryTarget> = {
  /** The list request; may resolve late, empty, or fail, exactly like the page. */
  list: () => Promise<T[]>
  /** Owner-scoped detail read. Throws for another user's or a deleted target. */
  detail: (id: number) => Promise<T | null>
}

export async function resolvePrivateEntryTarget<T extends EntryTarget>(
  targetId: number,
  deps: EntryDeps<T>,
): Promise<EntryResolution<T>> {
  if (!targetId || targetId <= 0) return { status: 'MISSING', reason: 'NO_HANDOFF' }

  // A failed list must not be fatal: the detail read is the authoritative one.
  const listed = await deps.list().catch(() => [] as T[])
  const fromList = listed.find(item => item.id === targetId)
  if (fromList && fromList.parse_status === 'CONFIRMED') {
    return { status: 'SELECTED', target: fromList, source: 'LIST' }
  }

  const fetched = await deps.detail(targetId).catch(() => null)
  if (fetched && fetched.parse_status === 'CONFIRMED') {
    return { status: 'SELECTED', target: fetched, source: 'DETAIL' }
  }
  return {
    status: 'MISSING',
    reason: fromList || fetched ? 'NOT_CONFIRMED' : 'NOT_FOUND',
  }
}
