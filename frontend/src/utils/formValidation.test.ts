import test from "node:test";
import assert from "node:assert/strict";
import { constraintMessage } from "./formValidation";
test("localizes missing required fields and selections", () => {
  assert.equal(constraintMessage({valueMissing:true},{label:"任务标题"}),"请填写「任务标题」。");
  assert.equal(constraintMessage({valueMissing:true},{label:"项目",type:"select"}),"请选择「项目」。");
});
test("localizes length, numeric and format constraints", () => {
  assert.match(constraintMessage({tooShort:true},{minLength:8}), /至少需要 8/);
  assert.match(constraintMessage({tooLong:true},{maxLength:160}), /不能超过 160/);
  assert.match(constraintMessage({rangeOverflow:true},{max:"100"}), /不能大于 100/);
  assert.match(constraintMessage({rangeUnderflow:true},{min:"0"}), /不能小于 0/);
  assert.match(constraintMessage({typeMismatch:true},{type:"url"}), /有效的网址/);
  assert.match(constraintMessage({patternMismatch:true}), /格式不正确/);
  assert.match(constraintMessage({badInput:true}), /有效数字/);
});
