export type NavIconKey =
  | "dashboard"
  | "command"
  | "projects"
  | "tasks"
  | "governance"
  | "access"
  | "accountSecurity"
  | "goalsWorkflows"
  | "knowledge"
  | "skills"
  | "artifacts"
  | "portfolio"
  | "agents"
  | "runtimes"
  | "interfaces"
  | "portability"
  | "system";

export interface NavItem {
  to: string;
  label: string;
  icon: NavIconKey;
  end?: boolean;
}

export interface NavGroup {
  id: string;
  label: string;
  icon: NavIconKey;
  items: NavItem[];
}

/** Eight primary entries. Multi-page entries expand to show the original routes. */
export const NAVIGATION_GROUPS: NavGroup[] = [
  {
    id: "overview",
    label: "总览",
    icon: "dashboard",
    items: [{ to: "/", label: "总览", icon: "dashboard", end: true }]
  },
  {
    id: "command",
    label: "智能指挥中心",
    icon: "command",
    items: [{ to: "/command", label: "智能指挥中心", icon: "command" }]
  },
  {
    id: "projects",
    label: "项目与任务",
    icon: "projects",
    items: [
      { to: "/projects", label: "项目管理", icon: "projects" },
      { to: "/tasks", label: "任务中心", icon: "tasks" },
      { to: "/goals-workflows", label: "目标与工作流", icon: "goalsWorkflows" },
      { to: "/project-portability", label: "项目导入导出", icon: "portability" }
    ]
  },
  {
    id: "agents",
    label: "智能体与接入",
    icon: "agents",
    items: [
      { to: "/agents", label: "智能体管理", icon: "agents" },
      { to: "/runtimes", label: "运行监控", icon: "runtimes" },
      { to: "/interfaces", label: "外部接口", icon: "interfaces" }
    ]
  },
  {
    id: "knowledge",
    label: "知识与成果",
    icon: "knowledge",
    items: [
      { to: "/knowledge", label: "知识库", icon: "knowledge" },
      { to: "/skills", label: "技能管理", icon: "skills" },
      { to: "/artifacts", label: "成果中心", icon: "artifacts" }
    ]
  },
  {
    id: "governance",
    label: "审批与安全",
    icon: "governance",
    items: [
      { to: "/governance", label: "审批与治理", icon: "governance" },
      { to: "/access", label: "任务授权", icon: "access" },
      { to: "/account-security", label: "账号安全", icon: "accountSecurity" }
    ]
  },
  {
    id: "portfolio",
    label: "运营分析",
    icon: "portfolio",
    items: [{ to: "/portfolio", label: "运营分析", icon: "portfolio" }]
  },
  {
    id: "system",
    label: "系统设置",
    icon: "system",
    items: [{ to: "/system", label: "系统与事件", icon: "system" }]
  }
];

export const ALL_NAV_ITEMS: NavItem[] = NAVIGATION_GROUPS.flatMap((group) => group.items);

export function findNavGroupId(pathname: string): string | null {
  return NAVIGATION_GROUPS.find((group) => group.items.some((item) => item.to === pathname))?.id ?? null;
}

export function findNavLabel(pathname: string): string {
  return ALL_NAV_ITEMS.find((item) => item.to === pathname)?.label || "控制中心";
}
