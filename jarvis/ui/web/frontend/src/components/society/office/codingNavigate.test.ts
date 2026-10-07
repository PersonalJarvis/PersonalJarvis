import { afterEach, describe, expect, it } from "vitest";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { openPaneSession } from "./codingNavigate";
import { useOfficeStore } from "./officeStore";

describe("open a coding session from the office", () => {
  const initialSection = useEventStore.getState().activeSection;
  const initialSelection = useOfficeStore.getState().selection;
  const initialLayout = useIdeThreadsStore.getState().layout;
  afterEach(() => {
    useIdeThreadsStore.setState({ layout: initialLayout });
    useEventStore.setState({ activeSection: initialSection });
    useIdeChatStore.setState({ paneRequest: null });
    useOfficeStore.setState({ selection: initialSelection });
  });

  it("switches to the Agentic IDE and asks it to focus the pane", () => {
    useEventStore.setState({ activeSection: "visualization" });
    openPaneSession({ workspace_id: "ws-1", name: "T2" });
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "ws-1", pane: "T2", maximize: true });
  });

  it("stays in the IDE and issues a fresh request each time", () => {
    useEventStore.setState({ activeSection: "agentic-ide" });
    openPaneSession({ workspace_id: "ws-1", name: "T2" });
    const first = useIdeChatStore.getState().paneRequest!.nonce;
    openPaneSession({ workspace_id: "ws-1", name: "T2" });
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
    expect(useIdeChatStore.getState().paneRequest!.nonce).toBe(first + 1);
  });

  it("closes the small viewer every time a session is opened at full size", () => {
    const pane = { workspace_id: "ws-1", name: "T2" };
    useOfficeStore.getState().select({ kind: "agent", id: "coding-agent" });
    openPaneSession(pane);
    expect(useOfficeStore.getState().selection).toBeNull();

    // Opening the same pane again must release the preview even though the
    // grid already has this pane maximized.
    useOfficeStore.getState().select({ kind: "agent", id: "coding-agent" });
    openPaneSession(pane);
    expect(useOfficeStore.getState().selection).toBeNull();
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ maximize: true });
  });

  it("leaves the thread layout so the pane itself shows, not the last open thread", () => {
    useIdeThreadsStore.setState({ layout: "threads" });
    openPaneSession({ workspace_id: "ws-1", name: "T2" });
    expect(useIdeThreadsStore.getState().layout).toBe("grid");
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "ws-1", pane: "T2", maximize: true });
  });
});
