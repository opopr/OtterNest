import test from "node:test";
import assert from "node:assert/strict";
import { ALL_NAV_ITEMS, NAVIGATION_GROUPS, findNavLabel } from "./navigation";

test("navigation routes and icons are unique across all groups", () => {
  assert.equal(NAVIGATION_GROUPS.length, 4);
  const paths = ALL_NAV_ITEMS.map((item) => item.to);
  const icons = ALL_NAV_ITEMS.map((item) => item.icon);
  assert.equal(new Set(paths).size, paths.length);
  assert.equal(new Set(icons).size, icons.length);
  assert.equal(ALL_NAV_ITEMS.length, 17);
});

test("findNavLabel resolves known paths and falls back cleanly", () => {
  assert.equal(findNavLabel("/"), "总览");
  assert.equal(findNavLabel("/account-security"), "账号安全");
  assert.equal(findNavLabel("/unknown"), "控制中心");
});
