import test from "node:test";
import assert from "node:assert/strict";
import { computeReconnectDelayMs } from "./reconnect";

test("computeReconnectDelayMs scales exponentially from baseMs and caps at maxMs", () => {
  assert.equal(computeReconnectDelayMs(0, 2000, 15000), 2000);
  assert.equal(computeReconnectDelayMs(1, 2000, 15000), 4000);
  assert.equal(computeReconnectDelayMs(2, 2000, 15000), 8000);
  assert.equal(computeReconnectDelayMs(3, 2000, 15000), 15000);
  assert.equal(computeReconnectDelayMs(8, 2000, 15000), 15000);
});

test("computeReconnectDelayMs clamps negative attempts safely", () => {
  assert.equal(computeReconnectDelayMs(-3, 1500, 10000), 1500);
});
