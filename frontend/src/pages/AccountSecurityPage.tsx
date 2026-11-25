import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AlertCircle, Copy, KeyRound, LockKeyhole, RefreshCw, ShieldCheck, ShieldOff, UserRound, History, LogOut } from "lucide-react";
import { request } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useDialog } from "../components/AppDialog";
import "./AdminGaps.css";

interface AccountSummary {
  username: string;
  display_name: string;
  totp_enabled: boolean;
  recovery_codes_remaining: number;
}
interface AccountResponse {
  account_configured: boolean;
  auth_method: string;
  active_sessions: number;
  user: AccountSummary | null;
}
interface TotpSetup {
  secret: string;
  formatted_secret: string;
  otpauth_uri: string;
  period_seconds: number;
  digits: number;
}
interface RecoveryResult {
  recovery_codes: string[];
  session_token?: string;
}
interface SecurityEvent {
  id: number;
  kind: string;
  subject_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}
interface SecurityAuditFinding {
  check: string;
  level: "pass" | "warning" | "critical";
  message: string;
}
interface SecurityAuditResult {
  status: "pass" | "warning" | "critical";
  summary: { critical: number; warning: number; pass: number };
  findings: SecurityAuditFinding[];
}

const eventNames: Record<string, string> = {
  "owner.bootstrapped": "账户初始化",
  "owner.password_changed": "登录密码已更改",
  "owner.2fa_enabled": "已开启双重验证",
  "owner.2fa_disabled": "已关闭双重验证",
  "owner.2fa_recovery_regenerated": "恢复码已轮换",
  "owner.sessions_revoked_all": "所有会话已注销",
  "owner.session_revoked": "会话已注销",
};
const message = (reason: unknown) => reason instanceof Error ? reason.message : "操作失败";

export default function AccountSecurityPage() {
  const { user, logout, loginWithToken, token } = useAuth();
  const isOwner = user?.kind === "owner";
  const [account, setAccount] = useState<AccountResponse | null>(null);
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [setupCode, setSetupCode] = useState("");
  const [oneTimeCode, setOneTimeCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [audit, setAudit] = useState<SecurityAuditResult | null>(null);
  const dialog = useDialog();

  const load = useCallback(async () => {
    if (!isOwner) { setLoading(false); return; }
    setLoading(true); setError("");
    try {
      const [result, recent, auditResult] = await Promise.all([
        request<AccountResponse>("/auth/account"),
        request<SecurityEvent[]>("/auth/events?limit=30"),
        request<SecurityAuditResult>("/system/security-audit").catch(() => null),
      ]);
      setAccount(result); setEvents(recent); setAudit(auditResult);
    } catch (reason) { setError(message(reason)); }
    finally { setLoading(false); }
  }, [isOwner]);

  useEffect(() => { void load(); }, [load]);

  const copy = async (value: string) => {
    try { await navigator.clipboard.writeText(value); setNotice("已复制。请保存在可信的私有位置。"); }
    catch { setError("剪贴板不可用，请手动复制。"); }
  };

  const updatePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (newPassword !== confirmPassword) { setError("两次输入的新密码不一致。"); return; }
    if (!account?.account_configured && !newUsername.trim()) { setError("首次配置需要用户名。"); return; }
    const confirmed = await dialog.confirm({
      title: "更改登录密码",
      message: "修改密码会使所有已有登录会话失效，并需要重新登录。确定继续吗？",
      confirmText: "确认更改密码",
      tone: "warning",
    });
    if (!confirmed) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await request("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername.trim() || null,
          display_name: displayName.trim() || null,
          current_password: currentPassword || null,
          new_password: newPassword
        })
      });
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      setNewUsername(""); setDisplayName("");
      await logout();
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const startTotp = async () => {
    setSaving(true); setError(""); setNotice(""); setRecoveryCodes([]);
    try {
      const result = await request<TotpSetup>("/auth/2fa/setup", { method: "POST" });
      setSetup(result); setSetupCode("");
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const enableTotp = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await request<RecoveryResult>("/auth/2fa/enable", {
        method: "POST", body: JSON.stringify({ code: setupCode.trim() })
      });
      setSetup(null); setSetupCode("");
      setRecoveryCodes(result.recovery_codes);
      if (result.session_token) await loginWithToken(result.session_token);
      await load();
      setNotice("已开启双重验证。恢复码仅此次显示，保存后再离开本页。");
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const rotateRecovery = async () => {
    if (!oneTimeCode.trim()) { setError("请输入当前验证器的动态验证码。"); return; }
    const confirmed = await dialog.confirm({
      title: "轮换恢复码",
      message: "重新生成恢复码会立即使旧恢复码全部失效，是否继续？",
      confirmText: "确认轮换",
      tone: "warning",
    });
    if (!confirmed) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await request<RecoveryResult>("/auth/2fa/recovery-codes", {
        method: "POST", body: JSON.stringify({ code: oneTimeCode.trim() })
      });
      setOneTimeCode(""); setRecoveryCodes(result.recovery_codes);
      await load();
      setNotice("新的恢复码已生成。旧恢复码立即失效，请安全保存新码。");
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const disableTotp = async () => {
    if (!disablePassword.trim() || !oneTimeCode.trim()) {
      setError("关闭双重验证需要密码及验证码或恢复码。"); return;
    }
    const confirmed = await dialog.confirm({
      title: "关闭双重验证（2FA）",
      message: "关闭双重验证会降低账号安全等级。确定继续关闭吗？",
      confirmText: "确认关闭 2FA",
      tone: "danger",
    });
    if (!confirmed) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await request("/auth/2fa/disable", {
        method: "POST",
        body: JSON.stringify({ current_password: disablePassword, code: oneTimeCode.trim() })
      });
      setDisablePassword(""); setOneTimeCode(""); setRecoveryCodes([]); setSetup(null);
      await load(); setNotice("双重验证已关闭。");
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const revokeSessions = async () => {
    const confirmed = await dialog.confirm({
      title: "注销全部会话",
      message: "确定注销全部密码登录会话？其他浏览器的登录也将失效，需要重新登录。",
      confirmText: "确认注销全部",
      tone: "danger",
    });
    if (!confirmed) return;
    setSaving(true); setError("");
    try {
      await request("/auth/logout-all", { method: "POST" });
      await logout();
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  if (!isOwner) return <div className="page-stack"><div className="page-heading"><h1>账号安全</h1></div><div className="panel empty-state"><ShieldCheck size={26} /><strong>只有所有者可访问账号安全设置。</strong></div></div>;

  return <div className="page-stack admin-gap-page">
    <div className="page-heading"><div><h1>账号安全</h1>
      <p>管理所有者登录密码、TOTP 双重验证、会话和安全审计记录。</p></div>
      <button className="secondary-button compact" onClick={() => void load()} disabled={loading || saving}><RefreshCw size={14} />刷新</button>
    </div>
    {error && <div className="inline-error"><AlertCircle size={16} />{error}</div>}
    {notice && <div className="success-notice"><ShieldCheck size={16} />{notice}<button onClick={() => setNotice("")}>关闭</button></div>}

    <section className="panel admin-security-summary">
      <div className="panel-heading"><div className="panel-title"><UserRound size={17} /><h3>当前账户</h3></div><span className="status-tag tag-green">所有者</span></div>
      {loading && !account ? <div className="panel-loading">正在读取账户信息…</div> :
      <div className="admin-metric-grid">
        <div><span>用户名</span><strong>{account?.user?.username || "尚未创建"}</strong></div>
        <div><span>显示名称</span><strong>{account?.user?.display_name || "—"}</strong></div>
        <div><span>双重验证</span><strong>{account?.user?.totp_enabled ? "已启用" : "未启用"}</strong></div>
        <div><span>活跃会话</span><strong>{account?.active_sessions ?? "—"}</strong></div>
        <div><span>剩余恢复码</span><strong>{account?.user?.recovery_codes_remaining ?? "—"}</strong></div>
        <div><span>当前身份方式</span><strong>{account?.auth_method === "owner_token" ? "服务器 Owner Token" : account?.auth_method || "—"}</strong></div>
      </div>}
    </section>

    <div className="admin-two-column">
      <section className="panel">
        <div className="panel-heading"><div className="panel-title"><LockKeyhole size={17} /><h3>更改登录密码</h3></div></div>
        <form className="form-panel admin-stack-form" onSubmit={(event) => void updatePassword(event)}>
          {!account?.account_configured && <label>首次创建用户名<input value={newUsername} onChange={(event) => setNewUsername(event.target.value)} minLength={3} maxLength={64} required autoComplete="username" /></label>}
          <label>显示名称（选填）<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={120} /></label>
          <label>当前密码{account?.auth_method === "owner_token" ? "（使用服务器 Token 时可不填）" : ""}
            <input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)}
              required={Boolean(account?.account_configured && account?.auth_method !== "owner_token")} autoComplete="current-password" /></label>
          <label>新密码<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={8} maxLength={128} required autoComplete="new-password" /></label>
          <label>确认新密码<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={8} maxLength={128} required autoComplete="new-password" /></label>
          <p className="form-hint">修改成功会注销所有已有会话，并返回登录页面。</p>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "处理中…" : "保存新密码"}</button></div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-heading"><div className="panel-title"><KeyRound size={17} /><h3>TOTP 双重验证</h3></div>
          <span className={"status-tag " + (account?.user?.totp_enabled ? "tag-green" : "tag-amber")}>{account?.user?.totp_enabled ? "已启用" : "尚未启用"}</span></div>
        {account?.user?.totp_enabled ? <div className="admin-stack-form">
          <p className="form-hint">恢复码仅在生成时显示。轮换恢复码请使用验证器的有效动态码；关闭 2FA 需当前密码和动态码或恢复码。</p>
          <label>当前验证码 / 恢复码<input value={oneTimeCode} onChange={(event) => setOneTimeCode(event.target.value)} autoComplete="one-time-code" /></label>
          <button className="secondary-button compact" disabled={saving || !oneTimeCode.trim()} onClick={() => void rotateRecovery()}><RefreshCw size={14} />轮换恢复码</button>
          <label>关闭 2FA 时的当前密码<input type="password" value={disablePassword} onChange={(event) => setDisablePassword(event.target.value)} autoComplete="current-password" /></label>
          <button className="secondary-button compact" disabled={saving || !disablePassword.trim() || !oneTimeCode.trim()} onClick={() => void disableTotp()}><ShieldOff size={14} />关闭双重验证</button>
        </div> : <div className="admin-stack-form">
          <p className="form-hint">使用身份验证器 App 扫描 otpauth URI 或输入密钥，然后填写 6 位动态码激活。</p>
          {!setup && <button className="primary-button compact" disabled={saving || !account?.account_configured} onClick={() => void startTotp()}><ShieldCheck size={14} />开始配置 TOTP</button>}
          {setup && <form className="admin-stack-form" onSubmit={(event) => void enableTotp(event)}>
            <label>身份验证器密钥（请勿分享）<div className="admin-secret-row"><code>{setup.formatted_secret}</code>
              <button type="button" className="secondary-button compact" onClick={() => void copy(setup.secret)}><Copy size={13} />复制</button></div></label>
            <label>验证器 URI（可手动复制）<div className="admin-secret-row"><code>{setup.otpauth_uri}</code>
              <button type="button" className="secondary-button compact" onClick={() => void copy(setup.otpauth_uri)}><Copy size={13} />复制</button></div></label>
            <label>6 位动态验证码<input value={setupCode} onChange={(event) => setSetupCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" required minLength={6} maxLength={6} /></label>
            <div className="form-actions"><button className="primary-button compact" disabled={saving}>启用双重验证</button>
              <button type="button" className="secondary-button compact" onClick={() => setSetup(null)}>暂不启用</button></div>
          </form>}
        </div>}
      </section>
    </div>

    {recoveryCodes.length > 0 && <section className="panel token-panel">
      <div className="token-panel-title"><ShieldCheck size={17} /><strong>新恢复码（仅此次显示）</strong></div>
      <p>每个恢复码只能使用一次。请线下安全保存；不要将它们上传到 GitHub、Google AI Studio 或共享聊天中。</p>
      <div className="admin-code-grid">{recoveryCodes.map((code) => <code key={code}>{code}</code>)}</div>
      <button className="secondary-button compact" onClick={() => void copy(recoveryCodes.join("\n"))}><Copy size={14} />复制全部恢复码</button>
      <button className="text-button" onClick={() => setRecoveryCodes([])}>我已安全保存，隐藏恢复码</button>
    </section>}

    <section className="panel">
      <div className="panel-heading"><div className="panel-title"><LogOut size={17} /><h3>会话控制</h3></div></div>
      <p className="form-hint">可撤销所有密码登录会话。服务器 Owner Token 由服务器环境配置单独管理，不会因此轮换。</p>
      <button className="secondary-button compact" onClick={() => void revokeSessions()} disabled={saving || !token}><LogOut size={14} />注销所有会话</button>
    </section>

    <section className="panel">
      <div className="panel-heading">
        <div className="panel-title"><ShieldCheck size={17} /><h3>部署前安全自检</h3></div>
        {audit && <span className={"status-tag " + (audit.status === "pass" ? "tag-green" : audit.status === "warning" ? "tag-amber" : "tag-neutral")}>
          {audit.status === "pass" ? "全部通过" : audit.status === "warning" ? `${audit.summary.warning} 项警告` : `${audit.summary.critical} 项严重`}
        </span>}
      </div>
      <div className="admin-event-list">
        {audit?.findings.map((finding, idx) => (
          <div className="admin-event-row admin-event-row-gap" key={idx}>
            <div className="admin-event-title">
              <span className={"status-tag " + (finding.level === "pass" ? "tag-green" : finding.level === "warning" ? "tag-amber" : "tag-neutral")}>{finding.level}</span>
              <strong>{finding.check}</strong>
            </div>
            <small>{finding.message}</small>
          </div>
        ))}
        {!audit && !loading && <div className="empty-mini">暂未获取到安全自检结果。</div>}
      </div>
    </section>

    <section className="panel">
      <div className="panel-heading"><div className="panel-title"><History size={17} /><h3>最近安全事件</h3></div><span className="count-chip">{events.length}</span></div>
      <div className="admin-event-list">{events.map((event) => <div className="admin-event-row" key={event.id}>
        <span>{eventNames[event.kind] || event.kind}</span>
        <small>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</small>
      </div>)}
      {!loading && events.length === 0 && <div className="empty-mini">暂无安全事件记录。</div>}</div>
    </section>
  </div>;
}
