// === F-FRIENDS [F2] · feature/friends-section · the maintainer-2026-04-30 ===
/**
 * Friends-API-Hooks (Phase F2).
 *
 * Spiegelt die Endpoints aus jarvis/ui/web/friends_routes.py.
 * Pattern wie useFederation.ts: React-Query, eigene fetch-Wrapper.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type FriendChannelKind = "telegram" | "jarvis_pubkey";
export type StatusProfile = "minimal" | "standard" | "detailed";

export interface ChannelLink {
  channel: FriendChannelKind;
  handle: string;
  is_primary: boolean;
  linked_at_ns: number;
}

export interface FriendItem {
  id: string;
  display_name: string;
  avatar_url: string | null;
  note: string | null;
  created_at_ns: number;
  channels: ChannelLink[];
}

export interface FriendDetail extends FriendItem {
  permission_profile: StatusProfile;
}

export interface FriendMessage {
  direction: "inbound" | "outbound";
  text: string;
  timestamp_ns: number;
  channel: FriendChannelKind | null;
}

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

export function useFriends() {
  return useQuery<FriendItem[]>({
    queryKey: ["friends"],
    queryFn: () => jsonFetch<FriendItem[]>("/api/friends"),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export function useFriend(friendId: string | null) {
  return useQuery<FriendDetail>({
    queryKey: ["friends", friendId],
    queryFn: () => jsonFetch<FriendDetail>(`/api/friends/${friendId}`),
    enabled: friendId !== null && friendId.length > 0,
    staleTime: 5_000,
  });
}

export interface CreateFriendInput {
  display_name: string;
  avatar_url?: string | null;
  note?: string | null;
}

export function useCreateFriend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateFriendInput) =>
      jsonFetch<FriendDetail>("/api/friends", {
        method: "POST",
        body: input,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["friends"] }),
  });
}

export interface UpdateFriendInput {
  friend_id: string;
  display_name?: string;
  note?: string | null;
}

export function useUpdateFriend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ friend_id, ...rest }: UpdateFriendInput) =>
      jsonFetch<FriendDetail>(`/api/friends/${friend_id}`, {
        method: "PATCH",
        body: rest,
      }),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["friends"] });
      qc.invalidateQueries({ queryKey: ["friends", vars.friend_id] });
    },
  });
}

export function useDeleteFriend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (friend_id: string) =>
      jsonFetch<void>(`/api/friends/${friend_id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["friends"] }),
  });
}

export interface LinkChannelInput {
  friend_id: string;
  channel: FriendChannelKind;
  handle: string;
  is_primary?: boolean;
}

export function useLinkChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ friend_id, ...body }: LinkChannelInput) =>
      jsonFetch<FriendDetail>(`/api/friends/${friend_id}/channels`, {
        method: "POST",
        body,
      }),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["friends"] });
      qc.invalidateQueries({ queryKey: ["friends", vars.friend_id] });
    },
  });
}

export interface UnlinkChannelInput {
  friend_id: string;
  channel: FriendChannelKind;
  handle: string;
}

export function useUnlinkChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ friend_id, channel, handle }: UnlinkChannelInput) =>
      jsonFetch<void>(
        `/api/friends/${friend_id}/channels/${channel}/${encodeURIComponent(handle)}`,
        { method: "DELETE" }
      ),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["friends"] });
      qc.invalidateQueries({ queryKey: ["friends", vars.friend_id] });
    },
  });
}

export interface FriendPermission {
  friend_id: string;
  profile: StatusProfile;
  custom_whitelist: string[] | null;
  updated_at_ns: number;
}

export function useFriendPermission(friendId: string | null) {
  return useQuery<FriendPermission>({
    queryKey: ["friends", friendId, "permission"],
    queryFn: () =>
      jsonFetch<FriendPermission>(`/api/friends/${friendId}/permission`),
    enabled: friendId !== null && friendId.length > 0,
  });
}

export interface UpdatePermissionInput {
  friend_id: string;
  profile: StatusProfile;
  custom_whitelist?: string[] | null;
}

export function useUpdatePermission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ friend_id, profile, custom_whitelist }: UpdatePermissionInput) =>
      jsonFetch<FriendPermission>(`/api/friends/${friend_id}/permission`, {
        method: "PATCH",
        body: { profile, custom_whitelist },
      }),
    onSuccess: (_, vars) =>
      qc.invalidateQueries({
        queryKey: ["friends", vars.friend_id, "permission"],
      }),
  });
}
