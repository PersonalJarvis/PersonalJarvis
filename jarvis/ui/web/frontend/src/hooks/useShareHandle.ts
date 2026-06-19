import { useCallback, useState } from "react";

/**
 * The user's X handle for the share card, persisted in ``localStorage``.
 *
 * Deliberately client-only: keeping the handle out of the repo/server matters
 * for the depersonalized public release. Stored without a leading ``@``; the
 * card renders it with one. Empty by default → the card shows brand only.
 */
const KEY = "board.share.handle";

function read(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function useShareHandle(): [string, (value: string) => void] {
  const [handle, setHandleState] = useState<string>(read);

  const setHandle = useCallback((value: string) => {
    const cleaned = value.trim().replace(/^@+/, "");
    setHandleState(cleaned);
    try {
      if (cleaned) localStorage.setItem(KEY, cleaned);
      else localStorage.removeItem(KEY);
    } catch {
      /* private mode / SSR — keep the in-memory value */
    }
  }, []);

  return [handle, setHandle];
}
