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

/** A group's shared meeting: its transcript, and whether a round is answering. */
export interface SocietyMeeting {
  messages: { id: string; speaker: string; text: string }[];
  running: boolean;
  room: { state: string; settle_reason: string; next_speaker: string | null } | null;
}

const meetingUrl = (groupId: string) => `/api/society/chat-groups/${encodeURIComponent(groupId)}/meeting`;

export function useSocietyMeeting(groupId: string) {
  return useQuery({
    queryKey: ["society", "meeting", groupId],
    queryFn: () => json<SocietyMeeting>(meetingUrl(groupId)),
    // Poll only while a round runs; an idle transcript changes only when someone sends.
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : false),
  });
}

export async function sendSocietyMeeting(groupId: string, text: string) {
  return json<SocietyMeeting>(meetingUrl(groupId), body({ text }));
}

export async function stopSocietyMeeting(groupId: string) {
  return json<SocietyMeeting>(`${meetingUrl(groupId)}/stop`, { method: "POST" });
}
