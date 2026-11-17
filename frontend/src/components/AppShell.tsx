import { useEffect, useState } from "react";
import {
  Activity, ArrowUpRight, Bot, BookMarked, BookOpen, FileArchive, Gauge, ChevronDown, CircleHelp, KeyRound, ClipboardCheck, MessageSquare,
  FolderKanban, GitBranch, LayoutDashboard, LogOut, Monitor, PlugZap, Settings2, ShieldCheck, ShieldUser, ArchiveRestore, Wifi
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { getApiOrigin, request } from "../api/client";
import type { SystemMode } from "../api/types";
import { useAuth } from "../auth/AuthProvider";

const navigation = [
  { to: "/", label: "总览", icon: LayoutDashboard, end: true },
  { to: "/projects", label: "项目管理", icon: FolderKanban },
  { to: "/tasks", label: "任务中心", icon: ClipboardCheck },
  { to: "/command", label: "AI 指挥中心", icon: MessageSquare },
  { to: "/agents", label: "Agent 管理", icon: Bot },
  { to: "/runtimes", label: "Runtime 监控", icon: Monitor },
  { to: "/interfaces", label: "外部接口", icon: PlugZap },
  { to: "/project-portability", label: "项目导入导出", icon: ArchiveRestore },
  { to: "/account-security", label: "账号安全", icon: ShieldUser },
  { to: "/governance", label: "审批与治理", icon: ShieldCheck },
  { to: "/goals-workflows", label: "目标与工作流", icon: GitBranch },
  { to: "/knowledge", label: "知识库", icon: BookOpen },
  { to: "/skills", label: "Skills 管理", icon: BookMarked },
  { to: "/portfolio", label: "Portfolio", icon: Gauge },
  { to: "/access", label: "任务授权", icon: KeyRound },
  { to: "/artifacts", label: "成果中心", icon: FileArchive },
  { to: "/system", label: "系统与事件", icon: Settings2 }
];

export default function AppShell() {
  const { user, logout } = useAuth();
  const [systemMode, setSystemMode] = useState<SystemMode | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    let active = true;
    request<SystemMode>("/system/mode").then((mode) => {
      if (active) setSystemMode(mode);
    }).catch(() => {
      if (active) setSystemMode(null);
    });
    return () => { active = false; };
  }, [location.pathname]);

  const modeLabel = !systemMode ? "状态读取中"
    : systemMode.mode === "readonly_protect" ? "只读保护"
      : systemMode.mode === "degraded" ? "降级模式" : "正常运行";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="OtterNest 首页">
          <span className="brand-mascot">🦦</span>
          <span><strong>OtterNest</strong><small>水獭窝 · AI Team OS</small></span>
        </Link>
        <div className="nav-section-label">工作空间</div>
        <nav className="side-nav">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} end={item.end}
                className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
                <Icon size={18} strokeWidth={1.8} /><span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-note">
          <div className="sidebar-note-icon"><ShieldCheck size={17} /></div>
          <div><strong>所有者控制</strong><p>重要操作始终经过授权与审查。</p></div>
        </div>
        <a className="side-link" href={getApiOrigin() + "/legacy"} target="_blank" rel="noreferrer"><ArrowUpRight size={16} />进入旧版完整管理台</a>
        <a className="side-link" href={getApiOrigin() + "/api/docs"} target="_blank" rel="noreferrer"><CircleHelp size={16} />API 文档</a>
        <div className="sidebar-footer">Personal AI Team OS · v0.1.0</div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <span>OtterNest</span><span className="breadcrumb-sep">/</span>
            <strong>{navigation.find((item) => item.to === location.pathname)?.label || "控制中心"}</strong>
          </div>
          <div className="topbar-actions">
            <div className={"system-pill " + (systemMode?.mode === "normal" ? "is-ok" : "is-warn")}>
              <span className="system-dot" />{modeLabel}
            </div>
            <div className={"connection-pill" + (systemMode ? "" : " is-offline")}><Wifi size={15} />{systemMode ? "API 已连接" : "API 状态未知"}</div>
            <div className="user-menu-wrap">
              <button className="user-button" onClick={() => setMenuOpen((open) => !open)}>
                <span className="user-avatar">O</span>
                <span className="user-name">{user?.id === "owner" ? "所有者" : user?.id || "账户"}</span>
                <ChevronDown size={15} />
              </button>
              {menuOpen && <div className="user-menu"><button onClick={() => { setMenuOpen(false); void logout(); }}><LogOut size={15} />退出登录</button></div>}
            </div>
          </div>
        </header>
        <main className="page-content"><Outlet /></main>
        <footer className="workspace-footer">
          <span>OtterNest · 本地优先，个人掌控</span>
          <span><Activity size={13} />控制平面状态以服务端记录为准</span>
        </footer>
      </div>
    </div>
  );
}
