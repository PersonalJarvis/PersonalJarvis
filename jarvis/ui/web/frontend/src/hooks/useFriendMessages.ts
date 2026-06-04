// === F-FRIENDS [F2] · feature/friends-section · alex-2026-04-30 ===
/**
 * Chat-Thread-Hooks fuer einen Friend (Phase F2).
 *
 * In F2: Polling 5s. Backend liefert leere Liste — UI zeigt empty-state,
 * Send-Pfad funktioniert bereits gegen Telegram.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { FriendMessage } from "./useFriends";

interface JsonFetchInit {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
}

async function jsonFetch<T>(path: string, init?: JsonFetchInit): Promise<T> {
  const opts: RequestInit = {
    method: init?.method,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  };
  if (init?.body !== undefined) {
    opts.body =
      typeof init.body === "string" ? init.body : JSON.stringify(init.body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(
      (detail as { detail?: string })?.detail ?? `HTTP ${res.status}`
    );
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export function useFriendMessages(friendId: string | null) {
  return useQuery<FriendMessage[]>({
    queryKey: ["friends", friendId, "messages"],
    queryFn: () =>
      jsonFetch<FriendMessage[]>(`/api/friends/${friendId}/messages`),
    enabled: friendId !== null && friendId.length > 0,
    refetchInterval: 5_000,
    staleTime: 1_000,
  });
}

export interface SendFriendMessageInput {
  friend_id: string;
  text: string;
}

export function useSendFriendMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ friend_id, text }: SendFriendMessageInput) =>
      jsonFetch<FriendMessage>(`/api/friends/${friend_id}/messages`, {
        method: "POST",
        body: { text },
      }),
    onSuccess: (newMsg, vars) => {
      qc.setQueryData<FriendMessage[]>(
        ["friends", vars.friend_id, "messages"],
        (prev) => (prev ? [...prev, newMsg] : [newMsg])
      );
    },
  });
}
