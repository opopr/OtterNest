import test from "node:test";
import assert from "node:assert/strict";
import { validatePromptValue } from "./dialogValidation";

test("validatePromptValue enforces required and minLength after trimming", () => {
  const empty = validatePromptValue("   ", { required: true });
  assert.equal(empty.valid, false);
  assert.match(empty.error, /不能为空/);

  const tooShort = validatePromptValue("  ab  ", { minLength: 5 });
  assert.equal(tooShort.valid, false);
  assert.match(tooShort.error, /至少需要 5/);

  const ok = validatePromptValue("  已完成核查记录  ", { minLength: 5 });
  assert.equal(ok.valid, true);
  assert.equal(ok.value, "已完成核查记录");
  assert.equal(ok.error, "");
});

test("validatePromptValue validates allowedValues and optional empty inputs", () => {
  const optionalEmpty = validatePromptValue("   ", { required: false });
  assert.equal(optionalEmpty.valid, true);
  assert.equal(optionalEmpty.value, "");

  const badVerdict = validatePromptValue("unknown", {
    required: true,
    allowedValues: ["pass", "fail", "concern"]
  });
  assert.equal(badVerdict.valid, false);

  const okVerdict = validatePromptValue(" concern ", {
    required: true,
    allowedValues: ["pass", "fail", "concern"]
  });
  assert.equal(okVerdict.valid, true);
  assert.equal(okVerdict.value, "concern");
});
