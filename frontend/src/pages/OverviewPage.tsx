import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertCircle, ArrowRight, Bot, CheckCircle2, Clock3,
  FolderKanban, RefreshCw, ShieldAlert, Wifi, WifiOff
} from "lucide-react";
import { Link } from "react-router-dom";
import { getApiOrigin, request, websocketUrl } from "../api/client";
import type {
  Agent, BriefingTask, DailyBriefing, PortfolioMetrics, Project, SystemMode
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import StatCard from "../components/StatCard";

interface OverviewData {
  projects: Project[];
  metrics: PortfolioMetrics;
  briefing: DailyBriefing;
  agents: Agent[];
  system: SystemMode;
}

function StatusTag({ value }: { value: string }) {
  const map: Record<string, string> = {
    active: "进行中", paused: "已暂停", archived: "已归档",
    ready: "待执行", running: "执行中", submitted: "待验收",
    waiting_approval: "待审批", blocked: "已阻塞", completed: "已完成", cancelled: "已取消"
  };
  const tone = value === "completed" || value === "active" ? "tag-green"
    : value === "blocked" || value === "waiting_approval" ? "tag-red"
      : value === "running" || value === "submitted" ? "tag-blue" : "tag-neutral";
  return <span className={"status-tag " + tone}>{map[value] || value}</span>;
}

function PendingList({ title, items, kind }: {
  title: string; items: BriefingTask[]; kind: "approval" | "review" | "blocked";
}) {
  const Icon = kind === "approval" ? ShieldAlert : kind === "review" ? Clock3 : AlertCircle;
  if (items.length === 0) return null;
  return (
    <section className="panel pending-panel">
      <div className="panel-heading">
        <div className="panel-title"><Icon size={17} /><h3>{title}</h3></div>
        <span className="count-chip">{items.length}</span>
      </div>
      <div className="pending-list">
        {items.slice(0, 5).map((item) => (
          <div className="pending-row" key={item.task_id}>
            <div className="pending-copy">
              <strong>{item.title}</strong>
              <small>{item.project_id.slice(0, 8)} · {item.task_id.slice(0, 8)}</small>
            </div>
            <Link to={"/projects?project=" + encodeURIComponent(item.project_id)} className="icon-link" aria-label="查看项目">
              <ArrowRight size={16} />
            </Link>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function OverviewPage() {
  const { token } = useAuth();
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [live, setLive] = useState<"connecting" | "connected" | "offline">("connecting");
  const [lastEvent, setLastEvent] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [projects, metrics, briefing, agents, system] = await Promise.all([
        request<Project[]>("/projects"),
        request<PortfolioMetrics>("/portfolio/metrics"),
        request<DailyBriefing>("/briefing/daily"),
        request<Agent[]>("/agents"),
        request<SystemMode>("/system/mode")
      ]);
      setData({ projects, metrics, briefing, agents, system });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取控制台数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const activeProjectIds = useMemo(
    () => (data?.projects || []).filter((project) => project.status === "active").slice(0, 5).map((project) => project.id),
    [data?.projects]
  );
  const activeProjectKey = activeProjectIds.join(",");

  useEffect(() => {
    if (!token || activeProjectIds.length === 0) {
      setLive("offline");
      return;
    }
    setLive("connecting");
    const connectedIds = new Set<string>();
    const sockets: WebSocket[] = [];
    for (const pid of activeProjectIds) {
      try {
        const socket = new WebSocket(websocketUrl(
          "/api/v1/projects/" + encodeURIComponent(pid) + "/events/ws"
        ));
        socket.onopen = () => {
          socket.send(JSON.stringify({ type: "auth", token, after: 0 }));
        };
        socket.onmessage = (event) => {
          try {
            const message = JSON.parse(String(event.data)) as { type?: string; kind?: string };
            if (message.type === "connected") {
              connectedIds.add(pid);
              setLive("connected");
            }
            if (message.type === "event") {
              setLastEvent(message.kind || "收到新事件");
              void refresh();
            }
          } catch {
            // REST remains the authoritative source if a frame cannot be parsed.
          }
        };
        socket.onerror = () => {
          connectedIds.delete(pid);
          if (connectedIds.size === 0) setLive("offline");
        };
        socket.onclose = () => {
          connectedIds.delete(pid);
          if (connectedIds.size === 0) setLive("offline");
        };
        sockets.push(socket);
      } catch {
        // Ignore individual socket failure if others connect.
      }
    }
    return () => {
      for (const socket of sockets) socket.close();
    };
  }, [token, activeProjectKey, refresh]);

  const projectRows = useMemo(() => {
    if (!data) return [];
    const metricProjects = Array.isArray(data.metrics?.projects) ? data.metrics.projects : [];
    const briefingProjects = Array.isArray(data.briefing?.projects) ? data.briefing.projects : [];
    const metricsByProject = new Map(metricProjects.map((metric) => [metric.project_id, metric]));
    return briefingProjects.map((project) => ({
      ...project,
      metric: metricsByProject.get(project.project_id)
    }));
  }, [data]);

  if (loading && !data) return <div className="loading-panel"><div className="spinner" />正在读取真实项目数据…</div>;
  if (error && !data) return <div className="error-panel"><AlertCircle size={18} />{error}<button onClick={() => void refresh()}>重试</button></div>;

  const briefing = data?.briefing;
  const metrics = data?.metrics;

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">COMMAND CENTER</div><h1>团队总览</h1><p>项目、执行节点和待处理事项的实时概况。</p></div>
        <div className="heading-actions">
          <div className={"live-pill " + (live === "connected" ? "live-on" : "")}>
            {live === "connected" ? <Wifi size={15} /> : <WifiOff size={15} />}
            {live === "connected" ? "事件流已连接" : live === "connecting" ? "正在连接事件流" : "事件流离线"}
          </div>
          <button className="secondary-button" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw size={15} className={loading ? "spin" : ""} />刷新
          </button>
        </div>
      </div>
      {error && <div className="inline-error">{error}</div>}

      <div className="welcome-banner">
        <div className="welcome-copy">
          <span className="welcome-kicker">YOUR AI TEAM, YOUR RULES</span>
          <h2>让每一只小水獭，都在正确的任务上协作。</h2>
          <p>目标由你设定，权限由你掌控，成果由你验收。</p>
          <Link to="/projects" className="banner-link">查看项目与任务 <ArrowRight size={16} /></Link>
        </div>
        <img src={getApiOrigin() + "/assets/mascots/otter-wave.png"} alt="OtterNest 水獭" />
        <div className="banner-orb banner-orb-one" /><div className="banner-orb banner-orb-two" />
      </div>

      <div className="stat-grid">
        <StatCard label="项目空间" value={metrics?.project_count ?? 0} hint="已登记项目" icon={FolderKanban} tone="violet" />
        <StatCard label="任务总数" value={metrics?.total_tasks ?? 0} hint="跨项目任务" icon={Activity} tone="blue" />
        <StatCard label="已完成任务" value={metrics?.completed_count ?? 0} hint="已验收完成" icon={CheckCircle2} tone="green" />
        <StatCard label="阻塞任务" value={metrics?.blocked_count ?? 0} hint="需要排查处理" icon={AlertCircle} tone="amber" />
        <StatCard label="启用 Agent" value={briefing?.enabled_agents ?? 0} hint="已登记并启用" icon={Bot} tone="violet" />
      </div>

      <div className="section-heading">
        <div><h2>项目运行情况</h2><p>数据来自服务端每日简报与 Portfolio 指标。</p></div>
        <Link to="/projects" className="subtle-link">全部项目 <ArrowRight size={15} /></Link>
      </div>
      <div className="project-grid">
        {projectRows.length === 0 ? (
          <div className="empty-state">
            <FolderKanban size={28} /><strong>还没有项目</strong><p>创建第一个项目，开始组织你的 AI 团队。</p>
            <Link to="/projects" className="primary-button compact">创建项目</Link>
          </div>
        ) : projectRows.map((project) => {
          const progress = project.task_total > 0 ? Math.round(project.completed / project.task_total * 100) : 0;
          return (
            <Link to={"/projects?project=" + encodeURIComponent(project.project_id)} className="project-card" key={project.project_id}>
              <div className="project-card-top">
                <div className="project-avatar"><FolderKanban size={18} /></div>
                <StatusTag value={project.status} />
              </div>
              <h3>{project.name}</h3>
              <p className="project-meta">优先级 {project.priority} · {project.task_total} 项任务</p>
              <div className="progress-label"><span>完成进度</span><strong>{progress}%</strong></div>
              <div className="progress-track"><span style={{ width: progress + "%" }} /></div>
              <div className="project-card-bottom"><span>{project.completed} 已完成</span><span>{project.running} 执行中</span><ArrowRight size={15} /></div>
            </Link>
          );
        })}
      </div>

      <div className="section-heading">
        <div><h2>需要你处理</h2><p>这些事项来自真实的任务状态，不是模拟数据。</p></div>
        <span className="muted-inline">{briefing?.unread_notifications ?? 0} 条未读通知</span>
      </div>
      <div className="pending-grid">
        <PendingList title="高风险任务待审批" items={briefing?.pending_r3_tasks || []} kind="approval" />
        <PendingList title="任务待验收" items={briefing?.pending_review_tasks || []} kind="review" />
        <PendingList title="执行阻塞" items={briefing?.blocked_tasks || []} kind="blocked" />
        {(!briefing || (
          (briefing.pending_r3_tasks?.length ?? 0) === 0 &&
          (briefing.pending_review_tasks?.length ?? 0) === 0 &&
          (briefing.blocked_tasks?.length ?? 0) === 0
        )) && (
          <div className="all-clear"><CheckCircle2 size={22} /><strong>目前没有待处理的任务异常</strong><span>系统仍会持续记录项目事件和执行状态。</span></div>
        )}
      </div>
      <div className="data-footnote">
        <span>系统模式：{data?.system?.mode || "未知"}</span>
        <span>简报生成：{briefing?.generated_at ? new Date(briefing.generated_at).toLocaleString("zh-CN") : "—"}</span>
        <span>{lastEvent ? "最近事件：" + lastEvent : "等待项目事件"}</span>
      </div>
    </div>
  );
}
