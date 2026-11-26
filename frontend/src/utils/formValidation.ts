type ConstraintState = Pick<ValidityState, "valueMissing" | "typeMismatch" | "patternMismatch" | "tooShort" | "tooLong" | "rangeUnderflow" | "rangeOverflow" | "stepMismatch" | "badInput">;

export function constraintMessage(state: Partial<ConstraintState>, options: {label?: string; type?: string; minLength?: number; maxLength?: number; min?: string; max?: string} = {}): string {
  const name = options.label ? "「" + options.label + "」" : "此项";
  if (state.badInput) return "请为" + name + "输入有效数字。";
  if (state.valueMissing) return options.type === "select" || options.type === "checkbox" || options.type === "radio" ? "请选择" + name + "。" : "请填写" + name + "。";
  if (state.typeMismatch) return "请输入有效的" + (options.type === "url" ? "网址" : options.type === "email" ? "电子邮箱地址" : "内容") + "。";
  if (state.tooShort) return name + "至少需要 " + options.minLength + " 个字符。";
  if (state.tooLong) return name + "不能超过 " + options.maxLength + " 个字符。";
  if (state.rangeUnderflow) return name + "不能小于 " + options.min + "。";
  if (state.rangeOverflow) return name + "不能大于 " + options.max + "。";
  if (state.stepMismatch) return "请为" + name + "输入符合步长要求的数值。";
  if (state.patternMismatch) return name + "的格式不正确，请检查输入要求。";
  return "请检查" + name + "的输入内容。";
}
