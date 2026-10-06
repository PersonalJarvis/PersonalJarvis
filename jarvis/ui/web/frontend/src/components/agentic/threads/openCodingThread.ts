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
