import { useCallback, useRef, useState } from "react";

/** The host owns intent across Canvas recreation; the renderer acknowledges it. */
export function useCompanionFocusRequests() {
  const sequence = useRef(0);
  const [pending, setPending] = useState(0);
  const request = useCallback(() => setPending(++sequence.current), []);
  const acknowledge = useCallback((id: number) => setPending((current) => current === id ? 0 : current), []);
  const cancel = useCallback(() => setPending(0), []);
  return { pending, request, acknowledge, cancel };
}
