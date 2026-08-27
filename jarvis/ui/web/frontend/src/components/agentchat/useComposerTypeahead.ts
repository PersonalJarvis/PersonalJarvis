import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";

import { fetchTypeahead, type AgentChatSurface } from "@/lib/agentChatApi";
import {
  activeToken,
  applyPick,
  filterItems,
  isStaticTrigger,
  type ActiveToken,
  type TypeaheadItem,
} from "@/components/agentchat/typeahead";

/** How long the box waits after a keystroke before asking for `@` matches. */
const MENTION_DEBOUNCE_MS = 120;
/** One fetched `/` or `$` list stands this long before it is read again. */
const STATIC_LIST_TTL_MS = 15_000;

export interface ComposerTypeahead {
  open: boolean;
  token: ActiveToken | null;
  items: TypeaheadItem[];
  loading: boolean;
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  /** Re-read the caret; call after any change, click or arrow key in the box. */
  refresh: () => void;
  /** Returns true when the key was the list's — the caller must then stop. */
  onKeyDown: (ev: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  pick: (item: TypeaheadItem) => void;
  /** Escape: hide the list for this token until the token changes. */
  close: () => void;
  /** Focus left the box: hide the list; it comes back with the next caret move. */
  blur: () => void;
}

interface Seat {
  surface: AgentChatSurface;
  provider: string;
  cwd: string;
  triggers: readonly string[];
}

/**
 * The typeahead's state machine for one text box.
 *
 * The caret decides everything: whenever it sits in a token a trigger opens
 * (`typeahead.ts`), the list for that trigger is shown, filtered by what was
 * typed after it. `/` and `$` lists are read once per seat and folder and
 * filtered here; `@` asks the backend per keystroke, because the folder's
 * file list is too large to ship. Escape dismisses the list for THIS token
 * only — a `/` typed on purpose as text must not keep re-opening, and the
 * next token opens as usual.
 */
export function useComposerTypeahead(
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  value: string,
  setValue: (next: string) => void,
  seat: Seat,
): ComposerTypeahead {
  const [token, setToken] = useState<ActiveToken | null>(null);
  const [rawItems, setRawItems] = useState<TypeaheadItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const dismissedStart = useRef<number | null>(null);
  const staticCache = useRef(new Map<string, { at: number; items: TypeaheadItem[] }>());
  const abortRef = useRef<AbortController | null>(null);
  const seatRef = useRef(seat);
  seatRef.current = seat;

  const refresh = useCallback(() => {
    const box = textareaRef.current;
    if (!box) {
      setToken(null);
      return;
    }
    const next = activeToken(box.value, box.selectionStart ?? box.value.length, seatRef.current.triggers);
    if (next && dismissedStart.current === next.start) {
      setToken(null);
      return;
    }
    if (next === null) dismissedStart.current = null;
    setToken((prev) =>
      prev && next && prev.trigger === next.trigger && prev.start === next.start && prev.query === next.query
        ? prev
        : next,
    );
  }, [textareaRef]);

  // The value can change under the box without a key (dictation, a paste
  // rescue, a pick); the caret is read afterwards so the token follows.
  useEffect(() => {
    refresh();
  }, [value, refresh]);

  const seatKey = `${seat.surface}|${seat.provider}|${seat.cwd}`;
  const trigger = token?.trigger ?? null;
  const query = token?.query ?? "";

  useEffect(() => {
    if (!trigger) {
      abortRef.current?.abort();
      setLoading(false);
      return;
    }
    const wanted = seatRef.current;
    const key = `${trigger}|${seatKey}`;
    if (isStaticTrigger(trigger)) {
      const cached = staticCache.current.get(key);
      if (cached && Date.now() - cached.at < STATIC_LIST_TTL_MS) {
        setRawItems(cached.items);
        setLoading(false);
        return;
      }
    }
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    setLoading(true);
    const timer = window.setTimeout(
      () => {
        fetchTypeahead(
          {
            surface: wanted.surface,
            provider: wanted.provider,
            cwd: wanted.cwd,
            trigger,
            q: isStaticTrigger(trigger) ? "" : query,
          },
          controller.signal,
        )
          .then((res) => {
            if (controller.signal.aborted) return;
            if (isStaticTrigger(trigger)) staticCache.current.set(key, { at: Date.now(), items: res.items });
            setRawItems(res.items);
            setLoading(false);
          })
          .catch(() => {
            // An aborted request is the caller's doing; anything else leaves
            // the list empty — the box still works, only the list is gone.
            if (controller.signal.aborted) return;
            setRawItems([]);
            setLoading(false);
          });
      },
      isStaticTrigger(trigger) ? 0 : MENTION_DEBOUNCE_MS,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [trigger, query, seatKey]);

  const items = useMemo(
    () => (trigger && isStaticTrigger(trigger) ? filterItems(rawItems, query) : rawItems),
    [trigger, rawItems, query],
  );

  // A new query starts at the top; a longer list never leaves the highlight past its end.
  useEffect(() => {
    setActiveIndex(0);
  }, [trigger, query]);
  useEffect(() => {
    if (activeIndex >= items.length) setActiveIndex(Math.max(0, items.length - 1));
  }, [items.length, activeIndex]);

  const close = useCallback(() => {
    if (token) dismissedStart.current = token.start;
    setToken(null);
  }, [token]);

  const blur = useCallback(() => setToken(null), []);

  const pick = useCallback(
    (item: TypeaheadItem) => {
      const box = textareaRef.current;
      if (!token) return;
      const applied = applyPick(box ? box.value : value, token, item);
      setValue(applied.text);
      dismissedStart.current = null;
      setToken(null);
      // The caret lands after what was inserted, once React has painted the new value.
      window.requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(applied.caret, applied.caret);
        // A folder keeps the list open on its contents; refresh reads the caret.
        if (item.kind === "folder") refresh();
      });
    },
    [token, textareaRef, value, setValue, refresh],
  );

  const open = Boolean(token) && (loading || items.length > 0 || query.length > 0);

  const onKeyDown = useCallback(
    (ev: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!token || !open) return false;
      switch (ev.key) {
        case "ArrowDown":
          ev.preventDefault();
          if (items.length) setActiveIndex((i) => (i + 1) % items.length);
          return true;
        case "ArrowUp":
          ev.preventDefault();
          if (items.length) setActiveIndex((i) => (i - 1 + items.length) % items.length);
          return true;
        case "Enter":
        case "Tab": {
          if (ev.shiftKey && ev.key === "Enter") return false;
          const item = items[activeIndex];
          if (!item) {
            // Nothing to pick: Tab does nothing, Enter falls through to send.
            return ev.key === "Tab";
          }
          ev.preventDefault();
          pick(item);
          return true;
        }
        case "Escape":
          ev.preventDefault();
          close();
          return true;
        default:
          return false;
      }
    },
    [token, open, items, activeIndex, pick, close],
  );

  return {
    open,
    token,
    items,
    loading,
    activeIndex,
    setActiveIndex,
    refresh,
    onKeyDown,
    pick,
    close,
    blur,
  };
}
