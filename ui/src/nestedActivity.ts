// Nested data fetches live only on the bridge feed. CodeMode tags them
// `{parentToolCallId}__{n}`; until that parent exists as a protocol tool
// call they stay off the feed rather than dangling under the user message.

/** CodeMode nested ids are `{parentToolCallId}__{n}`. */
const CODE_MODE_NESTED_ID = /^(.*)__(\d+)$/;

export function codeModeParentId(callId: string): string | null {
  const m = CODE_MODE_NESTED_ID.exec(callId);
  return m && m[1] !== "" ? m[1] : null;
}

export interface NestedSpan {
  callId: string;
  startedAt: number;
  seq: number;
}

/** Group nested (non-top-level) activities under their parent tool-call id.
 *
 *  Prefer the CodeMode `{parent}__{n}` suffix. If that parent is not yet a
 *  top-level protocol call, leave the nested span unassigned — do not attach
 *  it to an earlier turn's card via timestamps. Timestamps are only a
 *  fallback for ids that are not CodeMode-nested.
 */
export function groupNestedByParent<T extends NestedSpan>(
  activities: Iterable<T>,
  topLevelIds: ReadonlySet<string>,
): Map<string, T[]> {
  const list = [...activities];
  const timestampParents = list
    .filter((a) => topLevelIds.has(a.callId))
    .sort((a, b) => a.startedAt - b.startedAt || a.seq - b.seq);

  const nestedByParent = new Map<string, T[]>();
  const add = (parentId: string, activity: T) => {
    const group = nestedByParent.get(parentId);
    if (group) group.push(activity);
    else nestedByParent.set(parentId, [activity]);
  };

  for (const a of list) {
    if (topLevelIds.has(a.callId)) continue;
    const explicit = codeModeParentId(a.callId);
    if (explicit !== null) {
      if (topLevelIds.has(explicit)) add(explicit, a);
      continue;
    }
    let parent: T | undefined;
    for (const p of timestampParents) {
      if (p.startedAt <= a.startedAt) parent = p;
      else break;
    }
    if (parent) add(parent.callId, a);
  }

  for (const group of nestedByParent.values()) {
    group.sort((a, b) => a.startedAt - b.startedAt || a.seq - b.seq);
  }
  return nestedByParent;
}
