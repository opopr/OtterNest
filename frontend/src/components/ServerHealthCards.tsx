import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, Server, WifiOff } from "lucide-react";
import { Link } from "react-router-dom";
import { request } from "../api/client";

interface RuntimeHealth {
  id: string;
  label: string;
  environment: string;
  enabled: boolean;
  online: boolean;
  last_seen: string | null;
  last_health: Record<string, unknown> | null;
  agent_name: string;
  status: string;
}

const percent = (health: Record<string, unknown> | null, key: string): string => {
  const value = health?.[key];
  if (value === undefined || value === null || value === "") return "未上报";
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 && numeric <= 100 ? numeric.toFixed(1) + "%" : "数据无效";
};
const lastSeen = (value: string | null): string => {
  if (!value) return "从未上报";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
};

/** Read-only cards from existing authenticated Runtime API, not host-level telemetry. */
export default function ServerHealthCards() {
  const [rows, setRows] = useState<RuntimeHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await request<RuntimeHealth[]>("/runtimes");
      if (!Array.isArray(response)) throw new Error("运行实例数据格式无效");
      setRows(response);
      setRefreshedAt(new Date().toISOString());
    } catch (reason) {
      setRows([]);
      setError(reason instanceof Error ? reason.message : "无法读取运行实例");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <section className="panel" aria-label="远程执行节点状态">
      <div className="panel-heading">
        <div className="panel-title"><Server size={17}/><h3>远程执行节点</h3></div>
        <button className="secondary-button compact" type="button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} className={loading ? "spin" : ""}/>刷新节点
        </button>
      </div>
      <p className="muted-inline">当前展示已登记 智能体运行实例 的最后一次上报，尚不等于独立服务器的实时硬件监控。在线状态由后端心跳判断。</p>
      {refreshedAt && <p className="muted-inline">页面读取时间：{lastSeen(refreshedAt)}</p>}
      {error ? <div className="inline-error" role="alert"><WifiOff size={16}/>{error}</div> : null}
      {loading ? <div className="panel-loading"><span className="spinner"/>读取节点状态…</div> :
        rows.length === 0 ? <div className="empty-state"><Server size={25}/><strong>暂无可展示的执行节点</strong><p>请先注册 智能体运行实例 并配置心跳，未接入前不显示虚构服务器数据。</p></div> :
        <div className="stat-grid">
          {rows.map((row) => (
            <article className="stat-card" key={row.id}>
              <div className={"stat-icon " + (row.enabled && row.online ? "tone-green" : "tone-amber")}><Activity size={18}/></div>
              <div className="stat-copy">
                <span>{row.agent_name} · {row.environment || "环境未标注"}</span>
                <strong>{row.label}</strong>
                <small>{!row.enabled ? "已禁用" : row.online ? "运行实例在线" : "运行实例离线 / 未确认"}</small>
                <small>最后心跳：{lastSeen(row.last_seen)}</small>
                <small>CPU：{percent(row.last_health, "cpu_percent")} · 内存：{percent(row.last_health, "memory_percent")}</small>
                <small>磁盘：{percent(row.last_health, "disk_percent")} · GPU：{percent(row.last_health, "gpu_percent")}</small>
              </div>
            </article>
          ))}
        </div>
      }
      <div className="form-actions"><Link className="secondary-button compact" to="/runtimes">查看运行实例详情</Link></div>
    </section>
  );
}
