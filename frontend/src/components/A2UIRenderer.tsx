import type { ReactNode } from "react";

export interface A2UINode {
  type: "card" | "text" | "badge" | "metric" | "list" | "table" | "action_preview";
  title?: string;
  subtitle?: string;
  label?: string;
  text?: string;
  value?: string;
  tone?: string;
  action?: string;
  plan_id?: string;
  items?: string[];
  rows?: string[][];
  children?: A2UINode[];
}
export interface A2UIResult {
  valid: boolean;
  node_count: number;
  stripped: string[];
  components: A2UINode[];
}

function A2UINodeView({ node }: { node: A2UINode }) {
  switch (node.type) {
    case "card":
      return <article className="a2ui-card">{node.title && <strong>{node.title}</strong>}{node.subtitle && <p>{node.subtitle}</p>}{node.children?.map((child, index) => <A2UINodeView key={index} node={child} />)}</article>;
    case "text":
      return <p className="a2ui-text">{node.text || node.value || ""}</p>;
    case "badge":
      return <span className={"a2ui-badge tone-" + (node.tone || "neutral")}>{node.label || node.text || node.value || ""}</span>;
    case "metric":
      return <div className="a2ui-metric"><small>{node.label || "指标"}</small><strong>{node.value || "—"}</strong></div>;
    case "list":
      return <div className="a2ui-list">{node.title && <strong>{node.title}</strong>}<ul>{(node.items || []).map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
    case "table":
      return <div className="a2ui-table-wrap"><table className="a2ui-table"><tbody>{(node.rows || []).map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>;
    case "action_preview":
      return <div className="a2ui-action-preview"><small>待确认操作预览</small><strong>{node.title || "任务操作"}</strong><span>{node.action || "—"} · {node.value || "版本待校验"}</span></div>;
    default: {
      const fallback: ReactNode = node.text || node.title || "";
      return <>{fallback}</>;
    }
  }
}

export default function A2UIRenderer({ value }: { value?: A2UIResult | null }) {
  if (!value?.components?.length) return null;
  return <div className="a2ui-renderer">
    {value.components.map((node, index) => <A2UINodeView key={index} node={node} />)}
    {!value.valid && <small className="a2ui-sanitized-note">部分不受支持的组件已由服务端过滤。</small>}
  </div>;
}
