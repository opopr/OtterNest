import { LoadingState } from "./PageFeedback";
import { useConfirmDiscard } from "./DraftGuard";
import { Component, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  ArchiveRestore, ArrowUpRight, Bell, BookMarked, BookOpen, Bot, CheckCheck, ChevronDown,
  CircleHelp, ClipboardCheck, FileArchive, FolderKanban, Gauge, GitBranch, KeyRound,
  LayoutDashboard, LockKeyhole, LogOut, Menu, MessageSquare, Monitor, PlugZap, Search, Settings2, X, Moon, Sun, PanelLeftClose, PanelLeftOpen,
  ShieldCheck, Wifi, type LucideIcon
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { getApiOrigin, request } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useSystemMode } from "../auth/SystemModeProvider";
import { getSystemModeLabel } from "../utils/labels";
import BrandMark from "./BrandMark";
import { NAVIGATION_GROUPS, findNavLabel, findNavGroupId, searchNavigation, type NavIconKey } from "../utils/navigation";

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
  const confirmDiscard = useConfirmDiscard();
  const { systemMode } = useSystemMode();
  const [menuOpen, setMenuOpen] = useState(false);
  const [darkTheme, setDarkTheme] = useState(() => { try { return localStorage.getItem("otternest-ui-theme") === "dark"; } catch { return false; } });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => { try { return localStorage.getItem("otternest-sidebar-collapsed") === "true"; } catch { return false; } });
  const [wideLayout, setWideLayout] = useState(() => { try { return localStorage.getItem("otternest-wide-layout") === "true"; } catch { return false; } });
  const [compactDensity, setCompactDensity] = useState(() => { try { return localStorage.getItem("otternest-compact-density") === "true"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("otternest-wide-layout", String(wideLayout)); localStorage.setItem("otternest-compact-density", String(compactDensity)); } catch {} }, [wideLayout,compactDensity]);
  useEffect(() => { document.documentElement.dataset.uiTheme = darkTheme ? "dark" : "light"; try { localStorage.setItem("otternest-ui-theme", darkTheme ? "dark" : "light"); } catch {} }, [darkTheme]);
  useEffect(() => { try { localStorage.setItem("otternest-sidebar-collapsed", String(sidebarCollapsed)); } catch {} }, [sidebarCollapsed]);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifications, setNotifications] = useState<TopbarNotification[]>([]);
  const location = useLocation();
  const navigate = useNavigate();
  const [searchOpen, setSearchOpen] = useState(false);
  const [pageQuery, setPageQuery] = useState("");
  const searchTrigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchDialog = useRef<HTMLElement>(null);
  const searchResults = searchNavigation(pageQuery);
  const closeSearch = useCallback(() => { setSearchOpen(false); searchTrigger.current?.focus(); }, []);
  const openSearch = useCallback(() => { setPageQuery(""); setMenuOpen(false); setNotifOpen(false); setSearchOpen(true); }, []);
  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !document.querySelector(".app-dialog-card, .task-detail-dialog, .confirm-dialog")) {
        event.preventDefault(); openSearch();
      }
    };
    document.addEventListener("keydown", onShortcut);
    return () => document.removeEventListener("keydown", onShortcut);
  }, [openSearch]);
  useEffect(() => {
    if (!searchOpen) return;
    searchInput.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeSearch(); }
      if (event.key === "Tab") {
        const items = Array.from(searchDialog.current?.querySelectorAll<HTMLElement>("button, input, a[href]") || []);
        const first = items[0], last = items[items.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", onKeyDown); };
  }, [searchOpen, closeSearch]);
  const [expandedNavId, setExpandedNavId] = useState<string | null>(
    () => findNavGroupId(location.pathname)
  );

  useLayoutEffect(() => {
    setExpandedNavId(findNavGroupId(location.pathname));
    setMobileNavOpen(false);
    setMenuOpen(false);
    setNotifOpen(false);
    setSearchOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen && !notifOpen) return;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest(".user-menu-wrap")) setMenuOpen(false);
      if (!target.closest(".notif-menu-wrap")) setNotifOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenuOpen(false); setNotifOpen(false); }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen, notifOpen]);

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
    <div className={"app-shell" + (sidebarCollapsed ? " sidebar-collapsed" : "") + (wideLayout ? " wide-layout" : "") + (compactDensity ? " compact-density" : "")}>
      <aside className={"sidebar" + (mobileNavOpen ? " mobile-open" : "")}>
        <div className="brand-row-mobile">
        <Link className="brand" to="/" aria-label="OtterNest 首页" onClick={() => setMobileNavOpen(false)}>
          <span className="brand-mascot"><BrandMark /></span>
          <span><strong>OtterNest</strong><small>水獭窝</small></span>
        </Link>
        <button type="button" className="mobile-nav-toggle" aria-expanded={mobileNavOpen} aria-controls="main-navigation" onClick={() => setMobileNavOpen(open => !open)}>{mobileNavOpen ? <X size={18} /> : <Menu size={18} />}<span>{mobileNavOpen ? "收起菜单" : "菜单"}</span></button>
        </div>

        <nav id="main-navigation" className="side-nav-grouped" aria-label="主导航">
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
                    title={group.label}
                    aria-label={group.label}
                    onClick={() => setMobileNavOpen(false)}
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
                      title={group.label}
                      aria-label={group.label}
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
                            title={item.label}
                            aria-label={item.label}
                            onClick={() => setMobileNavOpen(false)}
                            className={({ isActive }) => "nav-sublink" + (isActive ? " active" : "")}
                          >
                            {(() => { const ItemIcon = NAV_ICONS[item.icon]; return <ItemIcon size={15} aria-hidden="true"/>; })()}
                            <span>{item.label}</span>
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
        <div className="sidebar-tools">
        <a className="side-link" title="旧版管理台" aria-label="旧版管理台" href={getApiOrigin() + "/legacy"} target="_blank" rel="noreferrer">
          <ArrowUpRight size={16} /><span>旧版管理台</span>
        </a>
        <a className="side-link" title="接口文档" aria-label="接口文档" href={getApiOrigin() + "/api/docs"} target="_blank" rel="noreferrer">
          <CircleHelp size={16} /><span>接口文档</span>
        </a>
        </div>
        
      </aside>

      {searchOpen && <div className="modal-backdrop page-search-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeSearch(); }}>
        <section ref={searchDialog} className="panel page-search-dialog" role="dialog" aria-modal="true" aria-labelledby="page-search-title">
          <div className="panel-heading"><h3 id="page-search-title">搜索页面</h3><button className="icon-button" type="button" aria-label="关闭页面搜索" onClick={closeSearch}><X size={17}/></button></div>
          <form className="page-search-field" onSubmit={event => { event.preventDefault(); if (searchResults.length) { closeSearch(); navigate(searchResults[0].to); } }}><Search size={18}/><input ref={searchInput} aria-label="搜索菜单" placeholder="输入页面名称或关键词" value={pageQuery} onChange={event => setPageQuery(event.target.value)}/></form>
          <div className="page-search-results" aria-label="页面搜索结果">{searchResults.map(item => { const Icon = NAV_ICONS[item.icon]; return <Link key={item.to} to={item.to} onClick={closeSearch}><Icon size={17}/><span>{item.label}</span><ArrowUpRight size={14}/></Link>; })}{!searchResults.length && <div className="empty-mini">没有匹配的页面</div>}</div>
        </section>
      </div>}
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button type="button" className="icon-button desktop-sidebar-toggle" aria-label={sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"} title={sidebarCollapsed ? "展开侧边栏" : "折叠侧边栏"} aria-expanded={!sidebarCollapsed} aria-controls="main-navigation" onClick={()=>setSidebarCollapsed(v=>!v)}>{sidebarCollapsed ? <PanelLeftOpen size={18}/> : <PanelLeftClose size={18}/>}</button>
            <span>OtterNest</span><span className="breadcrumb-sep">/</span>
            <strong>{findNavLabel(location.pathname)}</strong>
          </div>
          <div className="topbar-actions">
            <button type="button" className="icon-button theme-toggle" aria-label={darkTheme ? "切换浅色主题" : "切换深色主题"} title={darkTheme ? "切换浅色主题" : "切换深色主题"} aria-pressed={darkTheme} onClick={()=>setDarkTheme(v=>!v)}>{darkTheme ? <Sun size={17}/> : <Moon size={17}/>}</button>
            <button ref={searchTrigger} type="button" className="page-search-trigger" aria-label="搜索页面" aria-haspopup="dialog" aria-expanded={searchOpen} onClick={openSearch}><Search size={16}/><span>搜索页面</span><kbd>Ctrl K</kbd></button>
            <div className={"system-pill " + (systemMode?.mode === "normal" ? "is-ok" : "is-warn")}>
              <span className="system-dot" />{modeLabel}
            </div>
            <div className={"connection-pill" + (systemMode ? "" : " is-offline")}>
              <Wifi size={15} />{systemMode ? "服务已连接" : "连接状态未知"}
            </div>
            <div className="notif-menu-wrap">
              <button
                type="button"
                className="notif-button"
                aria-label="通知收件箱"
                aria-expanded={notifOpen}
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
                    <Link to="/portfolio" onClick={() => setNotifOpen(false)}>查看全部通知</Link>
                  </div>
                </div>
              )}
            </div>
            <div className="user-menu-wrap">
              <button className="user-button" aria-label="账户菜单" aria-expanded={menuOpen} onClick={() => { setNotifOpen(false); setMenuOpen((open) => !open); }}>
                <span className="user-avatar" aria-hidden="true">主</span>
                <span className="user-name">{user?.id === "owner" ? "所有者" : user?.id || "账户"}</span>
                <ChevronDown size={15} />
              </button>
              {menuOpen && (
                <div className="user-menu">
                  <div className="display-preferences" aria-label="显示设置" role="group"><small>显示设置</small><button type="button" aria-pressed={wideLayout} onClick={()=>setWideLayout(v=>!v)}><Monitor size={15}/><span>宽屏布局</span><span className="preference-state">{wideLayout ? "已开启" : "已关闭"}</span></button><button type="button" aria-pressed={compactDensity} onClick={()=>setCompactDensity(v=>!v)}><ClipboardCheck size={15}/><span>紧凑列表</span><span className="preference-state">{compactDensity ? "已开启" : "已关闭"}</span></button></div>
                  {user?.kind === "owner" && <Link to="/account-security" onClick={() => setMenuOpen(false)}><LockKeyhole size={15}/>账号安全</Link>}
                  <button onClick={() => { setMenuOpen(false); void confirmDiscard().then(allowed => { if (allowed) void logout(); }); }}>
                    <LogOut size={15} />退出登录
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="page-content">
          <RouteErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<LoadingState label="正在加载页面…"/>}>
              <Outlet />
            </Suspense>
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
  );
}
