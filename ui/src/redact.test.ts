import assert from "node:assert/strict";
import { compileTermPattern, redactText, setRedactTerms } from "./redact.ts";

setRedactTerms(["Acme Grain", "North Elevator", "José"]);

assert.equal(redactText("Shipped to acme grain today."), "Shipped to ████ █████ today.");
assert.equal(redactText("NORTH ELEVATOR owes 1200."), "█████ ████████ owes XXXX.");
assert.equal(redactText("Ask José at the desk."), "Ask ████ at the desk.");
assert.equal(redactText("Acme Grainery is a different word."), "Acme Grainery is a different word.");
assert.equal(redactText("Season 2024 table t3."), "Season 2024 table t3.");

const pattern = compileTermPattern(["River Mill", "River"]);
assert.equal("River Mill and River".replace(pattern!, (m) => m.replace(/[^\s]/g, "█")), "█████ ████ and █████");

setRedactTerms([]);
assert.equal(redactText("Acme Grain owes 12."), "Acme Grain owes XX.");
