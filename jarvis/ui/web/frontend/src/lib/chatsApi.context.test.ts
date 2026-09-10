import { afterEach, expect, it, vi } from "vitest";
import { resumeConversation, startNewVoiceRun } from "./chatsApi";

afterEach(() => vi.unstubAllGlobals());

it("finishes an old resume before applying the next selection or a fresh context", async () => {
  let finishFirst!: (response: Response) => void;
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    requests.push(url);
    if (requests.length === 1) return new Promise<Response>((resolve) => { finishFirst = resolve; });
    return Promise.resolve(new Response(JSON.stringify({ messages: [], cleared: true })));
  }));
  const first = resumeConversation("voice", "first");
  const second = resumeConversation("voice", "second");
  const fresh = startNewVoiceRun();
  await vi.waitFor(() => expect(requests).toEqual(["/api/chats/voice/first/resume"]));
  finishFirst(new Response(JSON.stringify({ messages: [] })));
  await Promise.all([first, second, fresh]);
  expect(requests).toEqual([
    "/api/chats/voice/first/resume", "/api/chats/voice/second/resume", "/api/chats/voice/new",
  ]);
});
