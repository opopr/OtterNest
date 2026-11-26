import { AlertCircle, CheckCircle2, RefreshCw, X } from "lucide-react";

interface FeedbackProps {
  message: string;
  onDismiss?: () => void;
  /** Only provide a retry for an idempotent read, never a mutation. */
  onRetry?: () => void;
  busy?: boolean;
}
export function ErrorNotice({message,onDismiss,onRetry,busy}: FeedbackProps) {
  if (!message) return null;
  return <div className="inline-error page-feedback" role="alert"><AlertCircle size={16} aria-hidden="true"/><span className="feedback-message">{message}</span>{onRetry && <button type="button" className="feedback-retry" disabled={busy} onClick={onRetry}><RefreshCw size={14}/>重新读取</button>}{onDismiss && <button type="button" className="feedback-dismiss" aria-label="关闭错误提示" onClick={onDismiss}><X size={15}/></button>}</div>;
}
export function SuccessNotice({message,onDismiss}: FeedbackProps) {
  return message ? <div className="success-notice page-feedback" role="status"><CheckCircle2 size={16} aria-hidden="true"/><span className="feedback-message">{message}</span>{onDismiss && <button type="button" className="feedback-dismiss" aria-label="关闭成功提示" onClick={onDismiss}><X size={15}/></button>}</div> : null;
}
export function LoadingState({label="正在读取数据…"}: {label?:string}) {
  return <div className="panel-loading" role="status" aria-live="polite"><div className="spinner" aria-hidden="true"/><span>{label}</span></div>;
}
