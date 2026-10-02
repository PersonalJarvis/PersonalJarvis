import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LiveSubscriptionAccount } from "./LiveSubscriptionAccount";
import { cancelLoginFlow, getLoginFlow, startLoginFlow, type AccountPlatformGroup, type LoginFlowState } from "@/lib/agentAccountsApi";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/lib/agentAccountsApi", () => ({
  cancelLoginFlow: vi.fn(), getLoginFlow: vi.fn(), startLoginFlow: vi.fn(), submitLoginFlowCode: vi.fn(),
}));
vi.mock("@/lib/connectBudget", () => ({
  spreadDelay: () => 5,
  requestConnect: (callback: () => void, delay: number) => {
    const timer = setTimeout(callback, delay);
    return () => clearTimeout(timer);
  },
}));

const group: AccountPlatformGroup = {
  platform: "codex", active_account: "codex:default", accounts: [{
    id: "codex:default", platform: "codex", label: "ChatGPT", config_dir: "", builtin: true,
    connected: false, mode: "unknown", message: "", email: null, tier: null,
  }],
};
function flow(status = "awaiting_input"): LoginFlowState {
  return { flow_id: "flow-1", account_id: "codex:default", platform: "codex", label: "ChatGPT",
    status, url: "https://auth.openai.com/example", code_expected: false, message: "", tail: "",
    finished: status === "success" };
}

afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("uses the selected account's in-app login and refreshes only after success", async () => {
  vi.mocked(startLoginFlow).mockResolvedValue(flow());
  vi.mocked(getLoginFlow).mockResolvedValue(flow("success"));
  const onConnected = vi.fn();
  render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={onConnected} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  await waitFor(() => expect(startLoginFlow).toHaveBeenCalledWith("codex:default"));
  await waitFor(() => expect(onConnected).toHaveBeenCalledOnce());
  expect(getLoginFlow).toHaveBeenCalledWith("flow-1");
  expect(screen.queryByText("live.subscription_voice_ready")).toBeNull();
});

it("cancels an unfinished login when subscription setup is closed", async () => {
  vi.mocked(startLoginFlow).mockResolvedValue(flow());
  vi.mocked(getLoginFlow).mockResolvedValue(flow());
  vi.mocked(cancelLoginFlow).mockResolvedValue({ ...flow(), status: "cancelled", finished: true });
  const { unmount } = render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  await screen.findByRole("button", { name: "agent_accounts.flow.cancel" });
  unmount();
  expect(cancelLoginFlow).toHaveBeenCalledWith("flow-1");
});

it("ends polling and offers explicit retry when sign-in status cannot be read", async () => {
  vi.mocked(startLoginFlow).mockResolvedValue(flow());
  vi.mocked(getLoginFlow).mockRejectedValue(new Error("private provider response"));
  vi.mocked(cancelLoginFlow).mockResolvedValue({ ...flow(), status: "cancelled", finished: true });
  render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "live.subscription_login_failed");
  expect(screen.getByRole("button", { name: "live.subscription_sign_in" })).toBeTruthy();
  expect(screen.queryByText("private provider response")).toBeNull();
});
