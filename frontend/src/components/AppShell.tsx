import { Component, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Activity, ArchiveRestore, ArrowUpRight, Bell, BookMarked, BookOpen, Bot, CheckCheck, ChevronDown,
  CircleHelp, ClipboardCheck, FileArchive, FolderKanban, Gauge, GitBranch, KeyRound,
  LayoutDashboard, LockKeyhole, LogOut, MessageSquare, Monitor, PlugZap, Settings2,
  ShieldCheck, Wifi, type LucideIcon
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { getApiOrigin, request } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useSystemMode } from "../auth/SystemModeProvider";
import { getSystemModeLabel } from "../utils/labels";
import { NAVIGATION_GROUPS, findNavLabel, findNavGroupId, type NavIconKey } from "../utils/navigation";

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

interface TopbarNotification {
  id: string;
  project_id: string | null;
  level: "urgent" | "important" | "digest" | string;
  title: string;
  body: string;
  read: boolean;
  created_at: string;
}

export default function AppShell() {
  const { user, logout } = useAuth();
  const { systemMode } = useSystemMode();
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifications, setNotifications] = useState<TopbarNotification[]>([]);
  const location = useLocation();
  const [expandedNavId, setExpandedNavId] = useState<string | null>(
    () => findNavGroupId(location.pathname)
  );

  useEffect(() => {
    setExpandedNavId(findNavGroupId(location.pathname));
  }, [location.pathname]);

  const loadNotifications = useCallback(async () => {
    try {
      const rows = await request<TopbarNotification[]>("/notifications?limit=20");
      setNotifications(Array.isArray(rows) ? rows : []);
    } catch {
      // Keep topbar resilient if notifications endpoint is temporarily unreachable.
    }
  }, []);

  useEffect(() => {
    void loadNotifications();
  }, [loadNotifications, location.pathname]);

  const markRead = async (id: string) => {
    try {
      await request("/notifications/" + encodeURIComponent(id) + "/read", { method: "POST" });
      setNotifications((items) => items.map((item) => item.id === id ? { ...item, read: true } : item));
    } catch {
      // Ignore transient error in quick popover.
    }
  };

  const markAllRead = async () => {
    try {
      await request("/notifications/read-all", { method: "POST" });
      setNotifications((items) => items.map((item) => ({ ...item, read: true })));
    } catch {
      // Ignore transient error in quick popover.
    }
  };

  const unreadCount = notifications.filter((item) => !item.read).length;
  const modeLabel = getSystemModeLabel(systemMode?.mode);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="OtterNest 首页">
          <span className="brand-mascot">🦦</span>
          <span><strong>OtterNest</strong><small>水獭窝 · AI Team OS</small></span>
        </Link>

        <nav className="side-nav-grouped" aria-label="主导航">
          {NAVIGATION_GROUPS.map((group) => {
            const Icon = NAV_ICONS[group.icon];
            const onlyItem = group.items.length === 1 ? group.items[0] : null;
            const isActiveGroup = group.items.some((item) => item.to === location.pathname);
            const isExpanded = expandedNavId === group.id;
            return (
              <div className="nav-group" key={group.id}>
                {onlyItem ? (
                  <NavLink
                    to={onlyItem.to}
                    end={onlyItem.end}
                    className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
                  >
                    <Icon size={17} strokeWidth={1.8} />
                    <span>{group.label}</span>
                  </NavLink>
                ) : (
                  <>
                    <button
                      type="button"
                      className={"nav-link nav-parent" + (isActiveGroup ? " active" : "")}
                      aria-expanded={isExpanded}
                      aria-controls={"nav-submenu-" + group.id}
                      onClick={() => setExpandedNavId(isExpanded ? null : group.id)}
                    >
                      <Icon size={17} strokeWidth={1.8} />
                      <span>{group.label}</span>
                      <ChevronDown
                        size={15}
                        className={"nav-chevron" + (isExpanded ? " expanded" : "")}
                      />
                    </button>
                    {isExpanded && (
                      <div className="nav-submenu" id={"nav-submenu-" + group.id}>
                        {group.items.map((item) => (
                          <NavLink
                            key={item.to}
                            to={item.to}
                            end={item.end}
                            className={({ isActive }) => "nav-sublink" + (isActive ? " active" : "")}
                          >
                            {item.label}
                          </NavLink>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
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
            <div className="notif-menu-wrap">
              <button
                type="button"
                className="notif-button"
                aria-label="通知收件箱"
                onClick={() => {
                  setMenuOpen(false);
                  setNotifOpen((open) => {
                    if (!open) void loadNotifications();
                    return !open;
                  });
                }}
              >
                <Bell size={16} />
                {unreadCount > 0 && <span className="notif-badge">{unreadCount > 99 ? "99+" : unreadCount}</span>}
              </button>
              {notifOpen && (
                <div className="notif-popover" role="dialog" aria-label="系统通知">
                  <div className="notif-popover-head">
                    <strong>通知收件箱 ({unreadCount} 未读)</strong>
                    {unreadCount > 0 && (
                      <button type="button" className="text-button" onClick={() => void markAllRead()}>
                        <CheckCheck size={13} />全部已读
                      </button>
                    )}
                  </div>
                  <div className="notif-popover-list">
                    {notifications.length === 0 ? (
                      <div className="notif-empty">暂无系统通知</div>
                    ) : (
                      notifications.slice(0, 8).map((item) => (
                        <div className={"notif-item " + (item.read ? "is-read" : "is-unread")} key={item.id}>
                          <div className="notif-item-main">
                            <div className="notif-item-title">
                              <span className={"status-tag " + (item.level === "urgent" ? "tag-red" : item.level === "important" ? "tag-amber" : "tag-neutral")}>
                                {item.level === "urgent" ? "紧急" : item.level === "important" ? "重要" : "摘要"}
                              </span>
                              <strong>{item.title}</strong>
                            </div>
                            <p>{item.body}</p>
                            <small>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</small>
                          </div>
                          {!item.read && (
                            <button type="button" className="text-button" onClick={() => void markRead(item.id)}>
                              已读
                            </button>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                  <div className="notif-popover-foot">
                    <Link to="/portfolio" onClick={() => setNotifOpen(false)}>前往 Portfolio 查看完整收件箱 →</Link>
                  </div>
                </div>
              )}
            </div>
            <div className="user-menu-wrap">
              <button className="user-button" onClick={() => { setNotifOpen(false); setMenuOpen((open) => !open); }}>
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
