import test from "node:test";
import assert from "node:assert/strict";
import { ALL_NAV_ITEMS, NAVIGATION_GROUPS, findNavGroupId, findNavLabel } from "./navigation";

test("navigation has exactly eight primary entries and preserves seventeen unique destinations", () => {
  assert.equal(NAVIGATION_GROUPS.length, 8);
  const paths = ALL_NAV_ITEMS.map((item) => item.to);
  const icons = ALL_NAV_ITEMS.map((item) => item.icon);
  assert.equal(new Set(paths).size, paths.length);
  assert.equal(new Set(icons).size, icons.length);
  assert.equal(ALL_NAV_ITEMS.length, 17);
  assert.deepEqual(paths.slice().sort(), [
    "/", "/account-security", "/agents", "/artifacts", "/command", "/goals-workflows",
    "/governance", "/interfaces", "/knowledge", "/portfolio", "/project-portability",
    "/projects", "/runtimes", "/skills", "/system", "/tasks", "/access"
  ].sort());
  for (const item of ALL_NAV_ITEMS) {
    assert.ok(findNavGroupId(item.to), "missing group for " + item.to);
  }
});

test("findNavLabel preserves each original destination label", () => {
  assert.equal(findNavLabel("/"), "总览");
  assert.equal(findNavLabel("/account-security"), "账号安全");
  assert.equal(findNavLabel("/projects"), "项目管理");
  assert.equal(findNavLabel("/unknown"), "控制中心");
  assert.equal(findNavGroupId("/tasks"), "projects");
  assert.equal(findNavGroupId("/runtimes"), "agents");
  assert.equal(findNavGroupId("/unknown"), null);
});
