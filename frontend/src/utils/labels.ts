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

/** Display-only labels; never send these translations as API values. */
const UI_LABELS: Record<string, string> = {
  ...TASK_STATUS_LABELS, ...SYSTEM_MODE_LABELS, ...SENSITIVITY_LABELS,
  active: "进行中", paused: "已暂停", archived: "已归档",
  pending: "待处理", proposed: "待确认", approved: "已批准", rejected: "已驳回",
  confirmed: "已确认", executed: "已执行", expired: "已过期", invalidated: "已失效",
  succeeded: "已成功", success: "成功", error: "失败", draft: "草稿",
  idle: "空闲", busy: "忙碌", stopped: "已停止", online: "在线", offline: "离线",
  unknown: "未知", enabled: "已启用", disabled: "已禁用", connected: "已连接",
  connecting: "连接中", disconnected: "已断开", queued: "排队中",
  open: "进行中", closed: "已关闭", published: "已发布", resolved: "已解决",
  pass: "通过", warning: "警告", concern: "需关注", fail: "未通过",
  urgent: "紧急", important: "重要", digest: "摘要",
  worker: "执行者", manager: "管理者", reviewer: "审查者", advisor: "顾问",
  owner: "所有者", member: "成员", manual: "人工接入", polling: "主动轮询",
  remote: "远程接入", disabled_mode: "未启用", pgvector: "向量检索",
  lexical: "文本检索", hybrid: "混合检索",
  authorize_task: "批准任务", review_task: "验收任务", route_task: "指派任务",
  cancel_task: "取消任务", RUN_STARTED: "开始运行", RUN_FINISHED: "运行结束",
  RUN_ERROR: "运行出错", STATE_SNAPSHOT: "状态快照", STATE_DELTA: "状态变更",
  TEXT_MESSAGE_START: "开始消息", TEXT_MESSAGE_CONTENT: "消息内容",
  TEXT_MESSAGE_END: "消息结束", TOOL_CALL_START: "开始工具调用",
  TOOL_CALL_ARGS: "工具参数", TOOL_CALL_END: "结束工具调用",
  CUSTOM: "自定义事件", RAW: "原始事件"
};
export function getUiLabel(value?: string | null): string {
  if (!value) return "—";
  return Object.prototype.hasOwnProperty.call(UI_LABELS, value) ? UI_LABELS[value] : value;
}
const HTTP_ERROR_LABELS: Record<number, string> = {
  400: "请求参数有误", 401: "身份验证失败，请重新登录", 403: "没有操作权限",
  404: "资源不存在", 405: "不支持此操作", 409: "状态冲突，请刷新后重试",
  413: "提交内容过大", 415: "不支持的内容格式", 422: "输入校验失败",
  429: "请求过于频繁", 500: "服务内部错误", 502: "上游服务不可用",
  503: "服务暂不可用", 504: "请求超时"
};
/** Keep original diagnostic text so operators can identify the actual failure. */
export function formatApiError(detail: unknown, status: number): string {
  const raw = typeof detail === "string" ? detail : detail == null ? "" : JSON.stringify(detail) || "";
  if (/[\u3400-\u9fff]/.test(raw) && typeof detail === "string") return raw;
  const message = HTTP_ERROR_LABELS[status] || "请求失败";
  return raw ? message + "（原始信息：" + raw + "）" : message;
}
