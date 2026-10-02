import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LiveSubscriptionAccount } from "./LiveSubscriptionAccount";
import { cancelLoginFlow, getLoginFlow, startLoginFlow, type AccountPlatformGroup, type LoginFlowState } from "@/lib/agentAccountsApi";
import { recheckAgent } from "@/lib/agenticIdeApi";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/lib/agentAccountsApi", () => ({
  cancelLoginFlow: vi.fn(), getLoginFlow: vi.fn(), startLoginFlow: vi.fn(), submitLoginFlowCode: vi.fn(),
}));
vi.mock("@/lib/agenticIdeApi", () => ({ recheckAgent: vi.fn() }));
const helperFetch = vi.hoisted(() => vi.fn());
const restartApp = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useRestartApp", () => ({ useRestartApp: () => ({
  restart: restartApp, restarting: false, buttonLabel: "Restart application",
}) }));
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

const helperReady = () => ({ ok: true, status: 200, json: async () => ({
  status: "ready", installed: true, source: "private", version: "0.147.0",
}) });
const helperMissing = () => ({ ok: true, status: 200, json: async () => ({
  status: "missing", installed: false, source: "missing", version: "",
}) });

beforeEach(() => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: true, version: null });
  helperFetch.mockResolvedValue(helperReady());
  vi.stubGlobal("fetch", helperFetch);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

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

it("explicitly provisions a private sign-in helper while the coding CLI stays absent", async () => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  helperFetch.mockResolvedValueOnce(helperMissing());
  vi.mocked(startLoginFlow).mockResolvedValue(flow());
  vi.mocked(getLoginFlow).mockResolvedValue(flow("success"));
  const onConnected = vi.fn();
  render(<LiveSubscriptionAccount group={group} accountId="codex:default" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={onConnected} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  const install = await screen.findByRole("button", { name: "live.subscription_install" });
  expect(helperFetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  expect(startLoginFlow).not.toHaveBeenCalled();
  fireEvent.click(install);
  await waitFor(() => expect(startLoginFlow).toHaveBeenCalledWith("codex:default"));
  await waitFor(() => expect(onConnected).toHaveBeenCalledOnce());
  expect(helperFetch).toHaveBeenCalledTimes(3);
  expect(helperFetch).toHaveBeenCalledWith("/api/live/login-helper", expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }));
  expect(recheckAgent).not.toHaveBeenCalled();
});

it("keeps installation failure actionable without displaying provider details or starting login", async () => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  helperFetch.mockResolvedValueOnce(helperMissing()).mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({ detail: "private provider credential" }) });
  render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  fireEvent.click(await screen.findByRole("button", { name: "live.subscription_install" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "live.subscription_install_failed");
  expect(screen.getByRole("button", { name: "live.subscription_install" })).toBeTruthy();
  expect(screen.queryByText("private provider credential")).toBeNull();
  expect(startLoginFlow).not.toHaveBeenCalled();
});

it("does not require an installed CLI for an already connected subscription", () => {
  const connected = { ...group, accounts: [{ ...group.accounts[0], connected: true, mode: "subscription" }] };
  render(<LiveSubscriptionAccount group={connected} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  expect(screen.getByText("live.subscription_connected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "live.subscription_sign_in" })).toBeNull();
  expect(recheckAgent).not.toHaveBeenCalled();
  expect(helperFetch).not.toHaveBeenCalled();
});

it("does not start OAuth when the private helper cannot be verified after setup", async () => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  helperFetch.mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(
    init?.method === "POST" ? helperReady() : helperMissing(),
  ));
  render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  fireEvent.click(await screen.findByRole("button", { name: "live.subscription_install" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "live.subscription_install_failed");
  expect(startLoginFlow).not.toHaveBeenCalled();
});

it("discards a late installation check when the selected account changes", async () => {
  let resolveCheck!: (value: unknown) => void;
  helperFetch.mockReturnValue(new Promise((resolve) => { resolveCheck = resolve; }));
  const props = { group, loading: false, disabled: false, voiceStatus: "unverified",
    onAccountChange: vi.fn(), onConnected: vi.fn() };
  const view = render(<LiveSubscriptionAccount {...props} accountId="codex:default" />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  view.rerender(<LiveSubscriptionAccount {...props} accountId="codex:second" />);
  resolveCheck(helperReady());
  await waitFor(() => expect((screen.getByRole("button", { name: "live.subscription_sign_in" }) as HTMLButtonElement).disabled).toBe(false));
  expect(startLoginFlow).not.toHaveBeenCalled();
});

it("shows a restart remedy on an older backend without falling back to npm", async () => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  helperFetch.mockResolvedValue({ ok: false, status: 404 });
  render(<LiveSubscriptionAccount group={group} accountId="" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  fireEvent.click(await screen.findByRole("button", { name: "Restart application" }));
  expect(restartApp).toHaveBeenCalledOnce();
  expect(startLoginFlow).not.toHaveBeenCalled();
  expect(helperFetch).toHaveBeenCalledTimes(1);
});

it("uses a verified private helper even when no coding CLI is installed", async () => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  vi.mocked(startLoginFlow).mockResolvedValue(flow("success"));
  render(<LiveSubscriptionAccount group={group} accountId="codex:default" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  await waitFor(() => expect(startLoginFlow).toHaveBeenCalledWith("codex:default"));
  expect(recheckAgent).not.toHaveBeenCalled();
  expect(helperFetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("preserves existing full Codex login on a backend without private helper routes", async () => {
  helperFetch.mockResolvedValue({ ok: false, status: 404 });
  vi.mocked(startLoginFlow).mockResolvedValue(flow("success"));
  render(<LiveSubscriptionAccount group={group} accountId="codex:default" loading={false} disabled={false}
    voiceStatus="unverified" onAccountChange={vi.fn()} onConnected={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  await waitFor(() => expect(startLoginFlow).toHaveBeenCalledWith("codex:default"));
  expect(recheckAgent).toHaveBeenCalledWith("codex");
  expect(helperFetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it.each(["cancel", "account-change", "unmount"])("cancels native provisioning on %s and never starts a stale account login", async (action) => {
  vi.mocked(recheckAgent).mockResolvedValue({ name: "codex", display_name: "Codex", installed: false, version: null });
  let resolveInstall!: (value: unknown) => void;
  helperFetch.mockImplementation((_url: string, init?: RequestInit) => init?.method === "POST"
    ? new Promise((resolve) => { resolveInstall = resolve; }) : Promise.resolve(helperMissing()));
  const props = { group, loading: false, disabled: false, voiceStatus: "unverified",
    onAccountChange: vi.fn(), onConnected: vi.fn() };
  const view = render(<LiveSubscriptionAccount {...props} accountId="codex:default" />);
  fireEvent.click(screen.getByRole("button", { name: "live.subscription_sign_in" }));
  fireEvent.click(await screen.findByRole("button", { name: "live.subscription_install" }));
  expect(await screen.findByText("live.subscription_installing")).toBeTruthy();
  const signal = helperFetch.mock.calls.find(([, init]) => init?.method === "POST")![1].signal as AbortSignal;
  if (action === "cancel") fireEvent.click(screen.getByRole("button", { name: "common.cancel" }));
  else if (action === "account-change") view.rerender(<LiveSubscriptionAccount {...props} accountId="codex:second" />);
  else view.unmount();
  expect(signal.aborted).toBe(true);
  resolveInstall({ ok: true, status: 200, json: async () => ({ status: "ready", installed: true }) });
  await Promise.resolve();
  expect(startLoginFlow).not.toHaveBeenCalled();
});
