import { useState, type FormEvent } from "react";
import { ArrowRight, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";

type LoginMode = "account" | "token";

export default function LoginPage() {
  const { authStatus, bootstrap, loginWithPassword, verifyMfa, loginWithToken } = useAuth();
  const [mode, setMode] = useState<LoginMode>("account");
  const [challenge, setChallenge] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ownerToken, setOwnerToken] = useState("");
  const [displayName, setDisplayName] = useState("管理员");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请检查 API 连接"); }
    finally { setBusy(false); }
  };

  const handleBootstrap = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(() => bootstrap({ owner_token: ownerToken, username, password, display_name: displayName }));
  };
  const handleLogin = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(async () => {
      const nextChallenge = await loginWithPassword(username, password);
      if (nextChallenge !== null) setChallenge(nextChallenge);
    });
  };
  const handleMfa = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(async () => { await verifyMfa(challenge, code); setChallenge(""); });
  };
  const handleToken = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(() => loginWithToken(ownerToken.trim()));
  };

  const configured = Boolean(authStatus?.account_configured);
  const canBootstrap = Boolean(authStatus?.token_configured);

  return (
    <div className="login-screen">
      <div className="login-decoration login-decoration-one" />
      <div className="login-decoration login-decoration-two" />
      <div className="login-card">
        <div className="login-brand">
          <div className="login-mascot">🦦</div>
          <div><div className="eyebrow">PERSONAL AI TEAM OS</div><h1>欢迎回到 OtterNest</h1><p>你的 AI 团队，始终由你掌控。</p></div>
        </div>
        {challenge ? (
          <form className="login-form" onSubmit={handleMfa}>
            <div className="login-form-heading"><ShieldCheck size={19} /><strong>双重身份验证</strong></div>
            <p className="form-hint">请输入验证器 App 的 6 位动态验证码，或 8 位一次性备用恢复码。</p>
            <label>验证码 / 恢复码<input autoFocus value={code} onChange={(event) => setCode(event.target.value)} autoComplete="one-time-code" required /></label>
            {error && <div className="form-error">{error}</div>}
            <button className="primary-button" disabled={busy}>{busy ? "正在验证…" : "验证并进入"}<ArrowRight size={16} /></button>
            <button type="button" className="text-button" onClick={() => { setChallenge(""); setCode(""); setError(""); }}>返回账号登录</button>
          </form>
        ) : !configured && canBootstrap ? (
          <form className="login-form" onSubmit={handleBootstrap}>
            <div className="login-form-heading"><LockKeyhole size={19} /><strong>初始化所有者账户</strong></div>
            <p className="form-hint">使用服务端配置的所有者令牌创建账户，令牌不会保存到浏览器。</p>
            <label>所有者初始化令牌<input type="password" value={ownerToken} onChange={(event) => setOwnerToken(event.target.value)} autoComplete="off" required /></label>
            <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
            <label>登录用户名<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required minLength={3} /></label>
            <label>登录密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" required minLength={8} /></label>
            {error && <div className="form-error">{error}</div>}
            <button className="primary-button" disabled={busy}>{busy ? "正在初始化…" : "创建所有者账户"}<ArrowRight size={16} /></button>
          </form>
        ) : (
          <>
            <div className="login-tabs">
              <button className={mode === "account" ? "selected" : ""} onClick={() => { setMode("account"); setError(""); }}>账号登录</button>
              <button className={mode === "token" ? "selected" : ""} onClick={() => { setMode("token"); setError(""); }}>管理员令牌</button>
            </div>
            {mode === "account" ? (
              <form className="login-form" onSubmit={handleLogin}>
                <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></label>
                <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
                {error && <div className="form-error">{error}</div>}
                <button className="primary-button" disabled={busy}>{busy ? "正在登录…" : "登录 OtterNest"}<ArrowRight size={16} /></button>
              </form>
            ) : (
              <form className="login-form" onSubmit={handleToken}>
                <label>管理员令牌<input type="password" value={ownerToken} onChange={(event) => setOwnerToken(event.target.value)} autoComplete="off" required /></label>
                {error && <div className="form-error">{error}</div>}
                <button className="primary-button" disabled={busy}>{busy ? "正在验证…" : "验证令牌"}<KeyRound size={16} /></button>
              </form>
            )}
          </>
        )}
        {!configured && !canBootstrap && <div className="form-error setup-warning">后端尚未配置 OTTERNEST_OWNER_TOKEN。请先在 FastAPI 服务端完成安全配置，再初始化所有者账户。</div>}
        <div className="login-footnote"><ShieldCheck size={15} /><span>身份验证由 FastAPI 完成 · 会话令牌仅保存在当前页面内存中</span></div>
      </div>
    </div>
  );
}
