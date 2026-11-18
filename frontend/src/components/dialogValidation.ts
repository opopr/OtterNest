export interface PromptValidationOptions {
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  allowedValues?: string[];
}

export function validatePromptValue(
  rawValue: string,
  options: PromptValidationOptions = {}
): { valid: boolean; value: string; error: string } {
  const value = rawValue.trim();
  const required = options.required ?? false;
  const minLength = options.minLength ?? 0;
  const maxLength = options.maxLength ?? 10000;

  if (required && value.length === 0) {
    return { valid: false, value, error: "请输入有效内容，不能为空。" };
  }
  if (minLength > 0 && value.length < minLength) {
    return {
      valid: false,
      value,
      error: `至少需要 ${minLength} 个有效字符（当前 ${value.length} 个）。`
    };
  }
  if (value.length > maxLength) {
    return {
      valid: false,
      value,
      error: `内容长度不能超过 ${maxLength} 个字符。`
    };
  }
  if (options.allowedValues && options.allowedValues.length > 0 && !options.allowedValues.includes(value)) {
    return {
      valid: false,
      value,
      error: `仅允许以下值之一：${options.allowedValues.join("、")}`
    };
  }
  return { valid: true, value, error: "" };
}
