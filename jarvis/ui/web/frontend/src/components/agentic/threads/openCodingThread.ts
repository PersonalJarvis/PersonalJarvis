import type { TimelineItem } from "@/components/agentchat/reduce";
import { useEventStore } from "@/store/events";
import { useIdeThreadsStore } from "@/store/ideThreads";

/**
 * Show one coding thread in the Agentic IDE's thread layout — the jump from
 * an agent's chat to the thread it started. The thread view finds the
 * thread's project itself, so none is passed.
 */
export function openCodingThread(sessionId: string): void {
  if (!sessionId) return;
  const threads = useIdeThreadsStore.getState();
  threads.setLayout("threads");
  threads.openThread(sessionId, "");
  useEventStore.getState().setActiveSection("agentic-ide");
}

/** The thread a coding-thread report came from (`coding-thread:<id>` sender ids). */
export function codingThreadOf(senderId: string): string {
  return senderId.startsWith("coding-thread:") ? senderId.slice("coding-thread:".length) : "";
}

/**
 * Drop a coding thread's status line that only repeats the last one shown for
 * that thread — "waits for an approval" again after a restart replayed the
 * same card. An unchanged state stays one row; a new state (another
 * approval, a question, a finished turn) is a row of its own.
 */
export function foldRepeatedThreadStatus(items: TimelineItem[]): TimelineItem[] {
  const last = new Map<string, string>();
  const out = items.filter((item) => {
    if (item.type !== "internal") return true;
    const thread = codingThreadOf(item.message.sender_id);
    if (!thread) return true;
    const text = item.message.text.trim();
    if (last.get(thread) === text) return false;
    last.set(thread, text);
    return true;
  });
  return out.length === items.length ? items : out;
}
