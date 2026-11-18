import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, HelpCircle, ShieldAlert, X } from "lucide-react";
import { validatePromptValue, type PromptValidationOptions } from "./dialogValidation";

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  tone?: "default" | "danger" | "warning";
}

export interface PromptChoice {
  value: string;
  label: string;
}

export interface PromptOptions extends PromptValidationOptions {
  title: string;
  message?: string;
  label?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmText?: string;
  cancelText?: string;
  multiline?: boolean;
  choices?: PromptChoice[];
}

interface DialogContextValue {
  confirm: (options: string | ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
}

type PendingDialog =
  | {
      kind: "confirm";
      options: ConfirmOptions;
      resolve: (value: boolean) => void;
    }
  | {
      kind: "prompt";
      options: PromptOptions;
      resolve: (value: string | null) => void;
    };

const DialogContext = createContext<DialogContextValue | null>(null);

export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingDialog | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [inputError, setInputError] = useState("");
  const pendingRef = useRef<PendingDialog | null>(null);
  pendingRef.current = pending;

  const confirm = useCallback((options: string | ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      if (pendingRef.current) {
        if (pendingRef.current.kind === "confirm") pendingRef.current.resolve(false);
        else pendingRef.current.resolve(null);
      }
      const normalized: ConfirmOptions = typeof options === "string" ? { message: options } : options;
      setInputError("");
      setPending({ kind: "confirm", options: normalized, resolve });
    });
  }, []);

  const prompt = useCallback((options: PromptOptions) => {
    return new Promise<string | null>((resolve) => {
      if (pendingRef.current) {
        if (pendingRef.current.kind === "confirm") pendingRef.current.resolve(false);
        else pendingRef.current.resolve(null);
      }
      setInputValue(options.defaultValue ?? (options.choices?.[0]?.value ?? ""));
      setInputError("");
      setPending({ kind: "prompt", options, resolve });
    });
  }, []);

  const cancel = useCallback(() => {
    const active = pendingRef.current;
    if (!active) return;
    setPending(null);
    setInputError("");
    if (active.kind === "confirm") active.resolve(false);
    else active.resolve(null);
  }, []);

  useEffect(() => {
    if (!pending) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pending, cancel]);

  const handleConfirm = () => {
    if (!pending || pending.kind !== "confirm") return;
    setPending(null);
    pending.resolve(true);
  };

  const handlePromptSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!pending || pending.kind !== "prompt") return;
    const check = validatePromptValue(inputValue, pending.options);
    if (!check.valid) {
      setInputError(check.error);
      return;
    }
    setPending(null);
    setInputError("");
    pending.resolve(check.value);
  };

  return (
    <DialogContext.Provider value={{ confirm, prompt }}>
      {children}
      {pending && (
        <div
          className="modal-backdrop app-dialog-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) cancel();
          }}
        >
          {pending.kind === "confirm" ? (
            <section
              className="confirm-dialog app-dialog-card"
              role="dialog"
              aria-modal="true"
              aria-labelledby="app-dialog-title"
            >
              <div className="panel-heading">
                <div className="panel-title">
                  {pending.options.tone === "danger" ? (
                    <ShieldAlert size={18} className="app-dialog-icon-danger" />
                  ) : pending.options.tone === "warning" ? (
                    <AlertTriangle size={18} className="app-dialog-icon-warn" />
                  ) : (
                    <HelpCircle size={18} />
                  )}
                  <h3 id="app-dialog-title">{pending.options.title || "请确认操作"}</h3>
                </div>
                <button type="button" className="icon-button" onClick={cancel} aria-label="关闭确认弹窗">
                  <X size={16} />
                </button>
              </div>
              <p className="app-dialog-message">{pending.options.message}</p>
              <div className="form-actions">
                <button type="button" className="secondary-button compact" onClick={cancel}>
                  {pending.options.cancelText || "取消"}
                </button>
                <button
                  type="button"
                  autoFocus
                  className={
                    pending.options.tone === "danger"
                      ? "primary-button compact app-dialog-danger-btn"
                      : "primary-button compact"
                  }
                  onClick={handleConfirm}
                >
                  {pending.options.confirmText || "确认继续"}
                </button>
              </div>
            </section>
          ) : (
            <form
              className="confirm-dialog app-dialog-card"
              role="dialog"
              aria-modal="true"
              aria-labelledby="app-dialog-title"
              onSubmit={handlePromptSubmit}
            >
              <div className="panel-heading">
                <div className="panel-title">
                  <HelpCircle size={18} />
                  <h3 id="app-dialog-title">{pending.options.title}</h3>
                </div>
                <button type="button" className="icon-button" onClick={cancel} aria-label="关闭输入弹窗">
                  <X size={16} />
                </button>
              </div>
              {pending.options.message && <p className="app-dialog-message">{pending.options.message}</p>}
              <label className="app-dialog-field">
                <span>{pending.options.label || "说明内容"}</span>
                {pending.options.choices && pending.options.choices.length > 0 ? (
                  <select
                    autoFocus
                    value={inputValue}
                    onChange={(event) => {
                      setInputValue(event.target.value);
                      if (inputError) setInputError("");
                    }}
                  >
                    {pending.options.choices.map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                  </select>
                ) : pending.options.multiline ? (
                  <textarea
                    autoFocus
                    rows={3}
                    value={inputValue}
                    placeholder={pending.options.placeholder}
                    maxLength={pending.options.maxLength ?? 5000}
                    onChange={(event) => {
                      setInputValue(event.target.value);
                      if (inputError) setInputError("");
                    }}
                  />
                ) : (
                  <input
                    autoFocus
                    value={inputValue}
                    placeholder={pending.options.placeholder}
                    maxLength={pending.options.maxLength ?? 2000}
                    onChange={(event) => {
                      setInputValue(event.target.value);
                      if (inputError) setInputError("");
                    }}
                  />
                )}
              </label>
              {inputError && <div className="inline-error">{inputError}</div>}
              <div className="form-actions">
                <button type="button" className="secondary-button compact" onClick={cancel}>
                  {pending.options.cancelText || "取消"}
                </button>
                <button type="submit" className="primary-button compact">
                  {pending.options.confirmText || "确认提交"}
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </DialogContext.Provider>
  );
}

export function useDialog(): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error("useDialog must be used inside DialogProvider");
  }
  return ctx;
}
