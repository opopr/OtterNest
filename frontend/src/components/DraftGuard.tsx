import { useEffect, useRef } from "react";
import { AlertCircle } from "lucide-react";
import { useDialog } from "./AppDialog";

/** In-memory draft protection; no project data is persisted in browser storage. */
export function useDraftGuard(dirty: boolean) {
  const dialog = useDialog();
  const confirming = useRef(false);
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
  return async () => {
    if (confirming.current) return false;
    if (!dirty) return true;
    confirming.current = true;
    try {
      return await dialog.confirm({ title: "存在未保存内容", message: "切换后将清空当前未保存的内容。是否放弃修改？", confirmText: "放弃并切换", cancelText: "继续编辑", tone: "warning" });
    } finally { confirming.current = false; }
  };
}

export function UnsavedDraftNotice({dirty}: {dirty: boolean}) {
  return dirty ? <div className="unsaved-draft-notice" role="status"><AlertCircle size={15} aria-hidden="true"/>有未保存的修改</div> : null;
}
