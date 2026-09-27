import { useQuery } from "@tanstack/react-query";

export interface SocietyChatGroup {
  group_id: string;
  name: string;
  members: string[];
  created_ms: number;
  updated_ms: number;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

const body = (data: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

export function useSocietyChatGroups(enabled = true) {
  return useQuery({
    queryKey: ["society", "chat-groups"],
    queryFn: async () => (await json<{ groups: SocietyChatGroup[] }>("/api/society/chat-groups")).groups,
    enabled,
    staleTime: 15_000,
    refetchInterval: 10_000,
  });
}

export async function createSocietyChatGroup(name: string, members: string[]) {
  return (await json<{ group: SocietyChatGroup }>("/api/society/chat-groups", body({ name, members }))).group;
}

export async function updateSocietyChatGroup(groupId: string, name: string, members: string[]) {
  return (await json<{ group: SocietyChatGroup }>(`/api/society/chat-groups/${encodeURIComponent(groupId)}`, {
    ...body({ name, members }), method: "PATCH",
  })).group;
}

export async function deleteSocietyChatGroup(groupId: string) {
  await json(`/api/society/chat-groups/${encodeURIComponent(groupId)}`, { method: "DELETE" });
}
