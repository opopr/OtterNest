import test from "node:test";
import assert from "node:assert/strict";
import { getSystemModeLabel, getTaskStatusLabel, getTaskStatusTone } from "./labels";

test("getTaskStatusLabel maps known and unknown statuses", () => {
  assert.equal(getTaskStatusLabel("waiting_approval"), "等待授权");
  assert.equal(getTaskStatusLabel("completed"), "已完成");
  assert.equal(getTaskStatusLabel("custom_state"), "custom_state");
});

test("getTaskStatusTone maps badge tones safely", () => {
  assert.equal(getTaskStatusTone("completed"), "tag-green");
  assert.equal(getTaskStatusTone("blocked"), "tag-red");
  assert.equal(getTaskStatusTone("submitted"), "tag-amber");
  assert.equal(getTaskStatusTone("unknown"), "tag-neutral");
});

test("getSystemModeLabel handles empty and known modes", () => {
  assert.equal(getSystemModeLabel(null), "状态读取中");
  assert.equal(getSystemModeLabel("normal"), "正常运行");
  assert.equal(getSystemModeLabel("readonly_protect"), "只读保护");
});
