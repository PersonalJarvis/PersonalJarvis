import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ----------------------------------------------------------------------
// Types — spiegelt board-backend/board_backend/schemas.py
// ----------------------------------------------------------------------

export interface FederationStatus {
  enabled: boolean;
  backend_url: string;
  pubkey: string | null;
}

export interface FriendItem {
  pubkey: string;
  url: string;
  display_name: string;
  paired_at: string;
  last_pull_at: string | null;
  pull_interval_s: number;
}

export interface PairInitiateResp {
  token: string;
  url: string;
  expires_at: string;
}

export type Visibility = "private" | "friends" | "public";
export type Reaction = "rocket" | "brain" | "fire";

export interface ActivityItemDTO {
  id: string;
  author_pubkey: string;
  author_display_name: string;
  kind: "achievement_unlocked" | "story" | "milestone";
  payload: Record<string, unknown>;
  created_at: string;
  visibility: Visibility;
  expires_at: string | null;
  reaction_counts: Record<string, number> | null;
  has_reactions: boolean;
}

export interface FeedResponse {
  items: ActivityItemDTO[];
  sort: "interesting" | "latest";
  server_now: string;
}

// ----------------------------------------------------------------------
// Fetchers (gegen lokalen Proxy, NICHT direkt das Federation-Backend)
// ----------------------------------------------------------------------

async function fetchStatus(): Promise<FederationStatus> {
  const res = await fetch("/api/board/federation/status");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function proxyGet<T>(path: string, query?: Record<string, string>): Promise<T> {
  const params = new URLSearchParams({ path, ...(query ?? {}) });
  const res = await fetch(`/api/board/federation/get?${params}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function proxyPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/board/federation/post", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, body }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function proxyPatch<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/board/federation/patch", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, body }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

// ----------------------------------------------------------------------
// Status / Disconnect
// ----------------------------------------------------------------------

export function useFederationStatus() {
  return useQuery({
    queryKey: ["federation", "status"],
    queryFn: fetchStatus,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/board/federation/disconnect", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation"] }),
  });
}

// ----------------------------------------------------------------------
// Pairing
// ----------------------------------------------------------------------

export function usePairInitiate() {
  return useMutation({
    mutationFn: async (): Promise<PairInitiateResp> => {
      const res = await fetch("/api/board/federation/pair/initiate", { method: "POST" });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
  });
}

export function usePairAcceptFromFriend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (pair_url: string) => {
      const res = await fetch("/api/board/federation/pair/accept-from-friend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair_url }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation"] }),
  });
}

// ----------------------------------------------------------------------
// Friends
// ----------------------------------------------------------------------

export function useFriendsList() {
  return useQuery({
    queryKey: ["federation", "friends"],
    queryFn: () => proxyGet<{ friends: FriendItem[] }>("/api/v1/friends"),
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });
}

// ----------------------------------------------------------------------
// Activity Feed
// ----------------------------------------------------------------------

export function useFeed(sort: "interesting" | "latest" = "interesting") {
  return useQuery({
    queryKey: ["federation", "feed", sort],
    queryFn: () => proxyGet<FeedResponse>("/api/v1/federation/feed", { sort }),
    refetchInterval: 2 * 60_000,
    staleTime: 60_000,
  });
}

// ----------------------------------------------------------------------
// Activity-Create + Reaction
// ----------------------------------------------------------------------

export function useCreateActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      kind: "achievement_unlocked" | "story" | "milestone";
      payload: Record<string, unknown>;
      visibility: Visibility;
      expires_in_hours?: number;
    }) => proxyPost<ActivityItemDTO>("/api/v1/activities", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation", "feed"] }),
  });
}

export function useSendReaction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { item_id: string; reaction: Reaction; author_pubkey: string }) =>
      proxyPost<{ accepted: boolean }>("/api/v1/reactions", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation", "feed"] }),
  });
}

export function useCreateStory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { text: string; visibility: Visibility }) =>
      proxyPost<ActivityItemDTO>("/api/v1/stories", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation", "feed"] }),
  });
}

export function useUpdateFriend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pubkey, pull_interval_s }: { pubkey: string; pull_interval_s: number }) =>
      proxyPatch<FriendItem>(`/api/v1/friends/${pubkey}`, { pull_interval_s }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["federation", "friends"] }),
  });
}
