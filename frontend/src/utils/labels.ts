export const TASK_STATUS_LABELS: Record<string, string> = {
  ready: "待执行",
  waiting_dependencies: "等待依赖",
  waiting_approval: "等待授权",
  running: "执行中",
  submitted: "等待验收",
  blocked: "已阻塞",
  completed: "已完成",
  cancelled: "已取消",
  failed: "执行失败"
};

export const TASK_STATUS_TONES: Record<string, string> = {
  ready: "tag-blue",
  waiting_dependencies: "tag-neutral",
  waiting_approval: "tag-red",
  running: "tag-blue",
  submitted: "tag-amber",
  blocked: "tag-red",
  completed: "tag-green",
  cancelled: "tag-neutral",
  failed: "tag-red"
};

export const RISK_LABELS: Record<string, string> = {
  R0: "低风险",
  R1: "一般风险",
  R2: "较高风险",
  R3: "高风险"
};

export const SENSITIVITY_LABELS: Record<string, string> = {
  public: "公开",
  internal: "内部",
  confidential: "机密",
  restricted: "受限"
};

export const SYSTEM_MODE_LABELS: Record<string, string> = {
  normal: "正常运行",
  degraded: "降级运行",
  readonly_protect: "只读保护"
};

export function getTaskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] || status;
}

export function getTaskStatusTone(status: string): string {
  return TASK_STATUS_TONES[status] || "tag-neutral";
}

export function getSystemModeLabel(mode?: string | null): string {
  if (!mode) return "状态读取中";
  return SYSTEM_MODE_LABELS[mode] || mode;
}
