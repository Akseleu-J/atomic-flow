import { availableBudget } from "../src/budget";
declare function require(name: string): any;
const { test } = require("node:test");
const assert = require("node:assert/strict");

test("known reservations reduce the available budget", () => {
  assert.equal(availableBudget(6, 2, 10), 2);
});
test("unknown reservations remain unknown", () => {
  assert.equal(availableBudget(6, null, 10), null);
});
test("an overspent budget has no available allowance", () => {
  assert.equal(availableBudget(10, 2, 10), 0);
});
test("invalid numeric accounting is refused", () => {
  assert.throws(() => availableBudget(Number.NaN, 0, 10), RangeError);
});
