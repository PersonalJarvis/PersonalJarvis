import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AgentChatStoreProvider,
  useAgentChat,
  useAgentChatApi,
} from "@/components/agentchat/AgentChatStoreContext";
import { useAgentChatStore, useAgentSessionStore } from "@/store/agentChat";

function Probe() {
  const surface = useAgentChat((s) => s.surface);
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const api = useAgentChatApi();
  return (
    <div data-testid="probe" data-surface={surface} data-active={activeSessionId ?? ""}>
      {api.getState().surface}
    </div>
  );
}

describe("the agent-chat store context", () => {
  it("reads the front page's store without a provider", () => {
    useAgentChatStore.setState({ activeSessionId: "front-1" });
    useAgentSessionStore.setState({ activeSessionId: "ide-1" });
    render(<Probe />);
    const probe = screen.getByTestId("probe");
    expect(probe.dataset.surface).toBe("jarvis");
    expect(probe.dataset.active).toBe("front-1");
    expect(probe.textContent).toBe("jarvis");
  });

  it("reads the IDE's store inside its provider — the two chats never mix", () => {
    useAgentChatStore.setState({ activeSessionId: "front-1" });
    useAgentSessionStore.setState({ activeSessionId: "ide-1" });
    render(
      <AgentChatStoreProvider store={useAgentSessionStore}>
        <Probe />
      </AgentChatStoreProvider>,
    );
    const probe = screen.getByTestId("probe");
    expect(probe.dataset.surface).toBe("agent");
    expect(probe.dataset.active).toBe("ide-1");
    expect(probe.textContent).toBe("agent");
  });

  it("keeps the two stores' sessions apart", () => {
    useAgentChatStore.setState({ activeSessionId: "front-2" });
    expect(useAgentSessionStore.getState().activeSessionId).not.toBe("front-2");
    expect(useAgentChatStore.getState().surface).toBe("jarvis");
    expect(useAgentSessionStore.getState().surface).toBe("agent");
  });
});
