import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { useBlocker } from "react-router-dom";
import { AlertCircle } from "lucide-react";
import { useDialog } from "./AppDialog";

interface DraftRegistry {
  register: (key: symbol) => () => void;
  confirmDiscard: () => Promise<boolean>;
}
const DraftContext = createContext<DraftRegistry | null>(null);

/** One shared route blocker, including browser history navigation. */
export function DraftNavigationProvider({children}: {children: ReactNode}) {
  const {confirm} = useDialog();
  const drafts = useRef(new Set<symbol>());
  const confirming = useRef(false);
  const register = useCallback((key: symbol) => { drafts.current.add(key); return () => { drafts.current.delete(key); }; }, []);
  const confirmDiscard = useCallback(async () => {
    if (confirming.current) return false;
    if (!drafts.current.size) return true;
    confirming.current = true;
    try {
      return await confirm({title:"存在未保存内容",message:"离开当前工作区将清空未保存的内容。是否放弃修改？",confirmText:"放弃并切换",cancelText:"继续编辑",tone:"warning"});
    } finally { confirming.current = false; }
  }, [confirm]);
  const blocker = useBlocker(useCallback(({currentLocation,nextLocation}: {currentLocation:{pathname:string};nextLocation:{pathname:string}}) => drafts.current.size > 0 && currentLocation.pathname !== nextLocation.pathname, []));
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;
  const handling = useRef(false);
  useEffect(() => {
    if (blocker.state !== "blocked" || handling.current) return;
    handling.current = true;
    void confirmDiscard().then(allowed => {
      const current = blockerRef.current;
      if (current.state === "blocked") {
        if (allowed) current.proceed();
        else current.reset();
      }
    }).finally(() => { handling.current = false; });
  }, [blocker,confirmDiscard]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!drafts.current.size) return;
      event.preventDefault(); event.returnValue = "";
    };
    window.addEventListener("beforeunload",beforeUnload);
    return () => window.removeEventListener("beforeunload",beforeUnload);
  }, []);
  return <DraftContext.Provider value={{register,confirmDiscard}}>{children}</DraftContext.Provider>;
}

export function useConfirmDiscard() {
  const registry = useContext(DraftContext);
  return registry?.confirmDiscard ?? (() => Promise.resolve(true));
}

/** Drafts stay in memory, never in local storage. */
export function useDraftGuard(dirty: boolean) {
  const registry = useContext(DraftContext);
  const register = registry?.register;
  const key = useRef(Symbol("draft"));
  useLayoutEffect(() => {
    if (!dirty || !register) return;
    return register(key.current);
  }, [dirty,register]);
  return useConfirmDiscard();
}

export function UnsavedDraftNotice({dirty}: {dirty: boolean}) {
  return dirty ? <div className="unsaved-draft-notice" role="status"><AlertCircle size={15} aria-hidden="true"/>有未保存的修改</div> : null;
}
