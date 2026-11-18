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
  items: NavItem[];
}

export const NAVIGATION_GROUPS: NavGroup[] = [
  {
    id: "workspace",
    label: "工作台与指挥",
    items: [
      { to: "/", label: "总览", icon: "dashboard", end: true },
      { to: "/command", label: "AI 指挥中心", icon: "command" },
      { to: "/projects", label: "项目管理", icon: "projects" },
      { to: "/tasks", label: "任务中心", icon: "tasks" }
    ]
  },
  {
    id: "governance",
    label: "治理与安全",
    items: [
      { to: "/governance", label: "审批与治理", icon: "governance" },
      { to: "/goals-workflows", label: "目标与工作流", icon: "goalsWorkflows" },
      { to: "/access", label: "任务授权", icon: "access" },
      { to: "/account-security", label: "账号安全", icon: "accountSecurity" }
    ]
  },
  {
    id: "knowledge",
    label: "知识与产出",
    items: [
      { to: "/knowledge", label: "知识库", icon: "knowledge" },
      { to: "/skills", label: "Skills 管理", icon: "skills" },
      { to: "/artifacts", label: "成果中心", icon: "artifacts" },
      { to: "/portfolio", label: "Portfolio", icon: "portfolio" }
    ]
  },
  {
    id: "operations",
    label: "运行与运维",
    items: [
      { to: "/agents", label: "Agent 管理", icon: "agents" },
      { to: "/runtimes", label: "Runtime 监控", icon: "runtimes" },
      { to: "/interfaces", label: "外部接口", icon: "interfaces" },
      { to: "/project-portability", label: "项目导入导出", icon: "portability" },
      { to: "/system", label: "系统与事件", icon: "system" }
    ]
  }
];

export const ALL_NAV_ITEMS: NavItem[] = NAVIGATION_GROUPS.flatMap((group) => group.items);

export function findNavLabel(pathname: string): string {
  return ALL_NAV_ITEMS.find((item) => item.to === pathname)?.label || "控制中心";
}
