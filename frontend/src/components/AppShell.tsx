import { Component, Suspense, useState, type ReactNode } from "react";
import {
  Activity, ArchiveRestore, ArrowUpRight, BookMarked, BookOpen, Bot, ChevronDown,
  CircleHelp, ClipboardCheck, FileArchive, FolderKanban, Gauge, GitBranch, KeyRound,
  LayoutDashboard, LockKeyhole, LogOut, MessageSquare, Monitor, PlugZap, Settings2,
  ShieldCheck, Wifi, type LucideIcon
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { getApiOrigin } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useSystemMode } from "../auth/SystemModeProvider";
import { getSystemModeLabel } from "../utils/labels";
import { NAVIGATION_GROUPS, findNavLabel, type NavIconKey } from "../utils/navigation";

const NAV_ICONS: Record<NavIconKey, LucideIcon> = {
  dashboard: LayoutDashboard,
  command: MessageSquare,
  projects: FolderKanban,
  tasks: ClipboardCheck,
  governance: ShieldCheck,
  goalsWorkflows: GitBranch,
  access: KeyRound,
  accountSecurity: LockKeyhole,
  knowledge: BookOpen,
  skills: BookMarked,
  artifacts: FileArchive,
  portfolio: Gauge,
  agents: Bot,
  runtimes: Monitor,
  interfaces: PlugZap,
  portability: ArchiveRestore,
  system: Settings2
};

interface BoundaryState {
  error: Error | null;
}

class RouteErrorBoundary extends Component<{ resetKey: string; children: ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidUpdate(prevProps: { resetKey: string }) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="panel error-panel" role="alert">
          <strong>页面渲染遇到异常</strong>
          <p>{this.state.error.message || "无法渲染当前视图"}</p>
          <button
            type="button"
            className="secondary-button compact"
            onClick={() => this.setState({ error: null })}
          >
            重试渲染
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function AppShell() {
  const { user, logout } = useAuth();
  const { systemMode } = useSystemMode();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  const modeLabel = getSystemModeLabel(systemMode?.mode);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="OtterNest 首页">
          <span className="brand-mascot">🦦</span>
          <span><strong>OtterNest</strong><small>水獭窝 · AI Team OS</small></span>
        </Link>

        <nav className="side-nav-grouped" aria-label="主导航">
          {NAVIGATION_GROUPS.map((group) => (
            <div className="nav-group" key={group.id}>
              <div className="nav-section-label">{group.label}</div>
              <div className="side-nav">
                {group.items.map((item) => {
                  const Icon = NAV_ICONS[item.icon];
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
                    >
                      <Icon size={17} strokeWidth={1.8} />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="sidebar-spacer" />
        <div className="sidebar-note">
          <div className="sidebar-note-icon"><ShieldCheck size={17} /></div>
          <div><strong>所有者控制</strong><p>重要操作始终经过授权与审查。</p></div>
        </div>
        <a className="side-link" href={getApiOrigin() + "/legacy"} target="_blank" rel="noreferrer">
          <ArrowUpRight size={16} />进入旧版完整管理台
        </a>
        <a className="side-link" href={getApiOrigin() + "/api/docs"} target="_blank" rel="noreferrer">
          <CircleHelp size={16} />API 文档
        </a>
        <div className="sidebar-footer">Personal AI Team OS · v0.1.0</div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <span>OtterNest</span><span className="breadcrumb-sep">/</span>
            <strong>{findNavLabel(location.pathname)}</strong>
          </div>
          <div className="topbar-actions">
            <div className={"system-pill " + (systemMode?.mode === "normal" ? "is-ok" : "is-warn")}>
              <span className="system-dot" />{modeLabel}
            </div>
            <div className={"connection-pill" + (systemMode ? "" : " is-offline")}>
              <Wifi size={15} />{systemMode ? "API 已连接" : "API 状态未知"}
            </div>
            <div className="user-menu-wrap">
              <button className="user-button" onClick={() => setMenuOpen((open) => !open)}>
                <span className="user-avatar">O</span>
                <span className="user-name">{user?.id === "owner" ? "所有者" : user?.id || "账户"}</span>
                <ChevronDown size={15} />
              </button>
              {menuOpen && (
                <div className="user-menu">
                  <button onClick={() => { setMenuOpen(false); void logout(); }}>
                    <LogOut size={15} />退出登录
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="page-content">
          <RouteErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<div className="panel-loading"><div className="spinner" />正在加载页面…</div>}>
              <Outlet />
            </Suspense>
          </RouteErrorBoundary>
        </main>
        <footer className="workspace-footer">
          <span>OtterNest · 本地优先，个人掌控</span>
          <span><Activity size={13} />控制平面状态以服务端记录为准</span>
        </footer>
      </div>
    </div>
  );
}
