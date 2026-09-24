import assert from "node:assert/strict";
import { codeModeParentId, groupNestedByParent } from "./nestedActivity.ts";

assert.equal(codeModeParentId("call_abc__1"), "call_abc");
assert.equal(codeModeParentId("call_abc__12"), "call_abc");
assert.equal(codeModeParentId("call_abc"), null);
assert.equal(codeModeParentId("__1"), null);
assert.equal(codeModeParentId("pyd_ai_code_mode__3"), "pyd_ai_code_mode");

function span(callId: string, startedAt: number, seq = startedAt) {
  return { callId, startedAt, seq };
}

const parent = span("call_parent", 10);
const nested = span("call_parent__1", 11);
const laterNested = span("call_parent__2", 12);
const prev = span("call_old", 1);
const prevNested = span("call_old__1", 2);

// Explicit parent not in the protocol feed yet: do not timestamp-attach to
// the previous turn.
let grouped = groupNestedByParent([prev, prevNested, nested], new Set(["call_old"]));
assert.deepEqual(
  [...(grouped.get("call_old") ?? [])].map((a) => a.callId),
  ["call_old__1"],
);
assert.equal(grouped.has("call_parent"), false);

// Once the parent call is on the protocol, nested rows attach to it — even
// if the parent has no bridge activity of its own.
grouped = groupNestedByParent([nested, laterNested], new Set(["call_parent"]));
assert.deepEqual(
  [...(grouped.get("call_parent") ?? [])].map((a) => a.callId),
  ["call_parent__1", "call_parent__2"],
);

// Ids without the CodeMode suffix still use the execution-window heuristic.
const loose = span("mystery", 15);
grouped = groupNestedByParent([parent, loose], new Set(["call_parent"]));
assert.deepEqual(
  [...(grouped.get("call_parent") ?? [])].map((a) => a.callId),
  ["mystery"],
);

console.log("nestedActivity ok");
