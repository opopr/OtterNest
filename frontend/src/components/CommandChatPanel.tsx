import { useEffect, useState, type FormEvent } from "react";
import { Bot, CheckCircle2, MessageSquare, RefreshCw, Send, Sparkles, UserRound } from "lucide-react";
import { request } from "../api/client";
import type { Task } from "../api/types";
import A2UIRenderer, { type A2UIResult } from "./A2UIRenderer";

interface ChatTurn {
  role: "user" | "assistant";
  text: string;
  task_id?: string;
}
interface ChatMessage extends ChatTurn {
  id: string;
  status?: string;
  a2ui?: A2UIResult;
  candidates?: Array<{ id: string; title: string; status: string; risk: string }>;
}
interface CommandPlan {
  id: string;
  task_id: string;
  project_id: string;
  action: string;
  parameters: Record<string, unknown>;
  expected_version: number;
  status: string;
  expires_at: string;
  confirmation_required: boolean;
  task_title?: string;
  task_status?: string;
  task_risk?: string;
}
interface ChatResponse {
  status: "answered" | "needs_clarification" | "plan_ready";
  reply: string;
  focus_task_id?: string;
  candidates?: ChatMessage["candidates"];
  plan?: CommandPlan;
  a2ui?: A2UIResult;
}
interface Props {
  projectId: string;
  tasks: Task[];
  onPlan: (plan: CommandPlan) => void;
}

const statusLabel: Record<string, string> = { waiting_approval: "等待授权", submitted: "等待验收", blocked: "阻塞", ready: "待执行" };

export default function CommandChatPanel({ projectId, tasks, onPlan }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [text, setText] = useState("");
  const [focusTaskId, setFocusTaskId] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setMessages([]);
    setHistory([]);
    setFocusTaskId("");
    setText("");
    setError("");
  }, [projectId]);

  const send = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const utterance = text.trim();
    if (!utterance || !projectId || sending) return;
    const userTurn: ChatTurn = { role: "user", text: utterance, ...(focusTaskId ? { task_id: focusTaskId } : {}) };
    const nextHistory = [...history, userTurn].slice(-20);
    const userMessage: ChatMessage = { ...userTurn, id: "user-" + Date.now() };
    setMessages((current) => [...current, userMessage]);
    setHistory(nextHistory);
    setText("");
    setSending(true);
    setError("");
    try {
      const response = await request<ChatResponse>("/command/chat", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          text: utterance,
          focus_task_id: focusTaskId || null,
          history,
          mode: "template"
        })
      });
      const assistantTurn: ChatTurn = {
        role: "assistant",
        text: response.reply,
        ...(response.focus_task_id || focusTaskId ? { task_id: response.focus_task_id || focusTaskId } : {})
      };
      const assistantMessage: ChatMessage = {
        ...assistantTurn,
        id: "assistant-" + Date.now(),
        status: response.status,
        a2ui: response.a2ui,
        candidates: response.candidates
      };
      setMessages((current) => [...current, assistantMessage]);
      setHistory([...nextHistory, assistantTurn].slice(-20));
      if (response.focus_task_id) setFocusTaskId(response.focus_task_id);
      if (response.plan) onPlan(response.plan);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Command Center 请求失败";
      setError(message);
      const assistantTurn: ChatTurn = { role: "assistant", text: "请求未完成：" + message };
      setMessages((current) => [...current, { ...assistantTurn, id: "error-" + Date.now(), status: "error" }]);
      setHistory([...nextHistory, assistantTurn].slice(-20));
    } finally { setSending(false); }
  };

  const clear = () => {
    setMessages([]);
    setHistory([]);
    setFocusTaskId("");
    setText("");
    setError("");
  };

  const chooseCandidate = (candidate: NonNullable<ChatMessage["candidates"]>[number]) => {
    setFocusTaskId(candidate.id);
    setText("继续");
  };

  return (
    <section className="panel command-chat-panel">
      <div className="panel-heading"><div className="panel-title"><MessageSquare size={17} /><h3>多轮 Command Center</h3><span className="status-tag tag-blue">服务端上下文</span></div><button className="text-button" onClick={clear}><RefreshCw size={13} />清空会话</button></div>
      <div className="command-chat-toolbar">
        <label>当前任务上下文<select value={focusTaskId} onChange={(event) => setFocusTaskId(event.target.value)}><option value="">由系统根据会话判断</option>{tasks.map((task) => <option key={task.id} value={task.id}>{task.title} · {statusLabel[task.status] || task.status}</option>)}</select></label>
        <span className="muted-inline">保留最近 20 轮对话上下文</span>
      </div>
      <div className="command-chat-transcript">
        {!messages.length && <div className="chat-welcome"><div className="chat-welcome-icon"><Sparkles size={21} /></div><strong>你可以直接询问项目，也可以要求执行任务操作。</strong><p>系统会先查询权威状态；遇到对象不明确时会向你澄清，不会猜测任务。</p><div className="chat-suggestions"><button onClick={() => setText("查看项目状态和指标")}>查看项目状态和指标</button><button onClick={() => setText("为什么任务被阻塞？")}>解释任务阻塞原因</button><button onClick={() => setText("请批准它")}>批准当前任务</button></div></div>}
        {messages.map((message) => <article className={"chat-message " + (message.role === "user" ? "from-user" : "from-assistant")} key={message.id}>
          <div className="chat-message-avatar">{message.role === "user" ? <UserRound size={15} /> : <Bot size={15} />}</div>
          <div className="chat-message-body"><div className="chat-message-meta"><strong>{message.role === "user" ? "你" : "OtterNest"}</strong>{message.status === "plan_ready" && <span className="status-tag tag-green">已生成预览</span>}{message.status === "needs_clarification" && <span className="status-tag tag-amber">需要澄清</span>}</div>
            <p>{message.text}</p>
            {message.candidates && message.candidates.length > 0 && <div className="chat-candidates"><small>请选择具体任务，随后可以继续输入“继续”或其他指令：</small>{message.candidates.map((candidate) => <button key={candidate.id} onClick={() => chooseCandidate(candidate)}><span><strong>{candidate.title}</strong><small>{statusLabel[candidate.status] || candidate.status} · {candidate.risk}</small></span><CheckCircle2 size={15} /></button>)}</div>}
            <A2UIRenderer value={message.a2ui} />
          </div>
        </article>)}
        {sending && <div className="chat-typing"><span className="spinner" />正在读取项目上下文…</div>}
      </div>
      {error && <div className="inline-error"><span>{error}</span></div>}
      <form className="command-chat-composer" onSubmit={(event) => void send(event)}>
        <textarea rows={2} value={text} onChange={(event) => setText(event.target.value)} maxLength={3500} placeholder="询问项目状态、解释任务原因，或提出任务操作指令…" />
        <div className="command-chat-composer-foot"><span>模式：受限模板 + 项目上下文 · 不调用外部 LLM</span><button className="primary-button compact" disabled={sending || !text.trim()}><Send size={14} />发送</button></div>
      </form>
    </section>
  );
}
