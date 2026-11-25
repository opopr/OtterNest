import test from "node:test";
import assert from "node:assert/strict";
import { getSystemModeLabel, getTaskStatusLabel, getTaskStatusTone, getUiLabel, formatApiError } from "./labels";

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

test("UI labels translate known codes without changing unknown values", () => {
  assert.equal(getUiLabel("worker"), "执行者");
  assert.equal(getUiLabel("busy"), "忙碌");
  assert.equal(getUiLabel("authorize_task"), "批准任务");
  assert.equal(getUiLabel("RUN_STARTED"), "开始运行");
  assert.equal(getUiLabel("custom_status"), "custom_status");
  assert.equal(getUiLabel("constructor"), "constructor");
});
test("API errors include Chinese context and retain the original diagnostic", () => {
  assert.equal(formatApiError("Not Found", 404), "资源不存在（原始信息：Not Found）");
  assert.equal(formatApiError("任务不存在", 404), "任务不存在");
  assert.equal(formatApiError(null, 503), "服务暂不可用");
  assert.match(formatApiError([{msg:"Field required"}],422), /输入校验失败.*Field required/);
});
