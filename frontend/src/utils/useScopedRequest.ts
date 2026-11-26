import { useCallback, useEffect, useRef } from "react";

/** Only the newest response for the current project may update its view. */
export function useScopedRequest(scope: string) {
  const current = useRef(scope);
  const sequence = useRef(0);
  current.current = scope;
  useEffect(() => () => { sequence.current += 1; }, [scope]);
  return useCallback((requestedScope: string) => {
    if (requestedScope !== current.current) return () => false;
    const id = ++sequence.current;
    return () => current.current === requestedScope && sequence.current === id;
  }, []);
}
