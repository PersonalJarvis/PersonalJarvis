import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { consumeAccountReset, fetchAccountResets, ResetRequestError, type AccountResets } from "@/lib/agentResetsApi";
import { openExternalUrl } from "@/lib/openExternal";
import { BankedResets } from "./BankedResets";

vi.mock("@/lib/agentResetsApi", async () => ({
  ...await vi.importActual<typeof import("@/lib/agentResetsApi")>("@/lib/agentResetsApi"),
  consumeAccountReset: vi.fn(), fetchAccountResets: vi.fn(),
}));
vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn(async () => true) }));

const inventory: AccountResets = {
  account_id: "codex:test", status: "ok", available_count: 3,
  credits: [{ id: "credit-1", title: "Full reset", description: "A saved reset", expires_at: "2099-11-01T12:00:00Z", redeemable: true }],
  can_redeem: true, account_key: "a".repeat(64), manage_url: "https://chatgpt.com/codex/settings/usage", as_of: 1, usage: null,
};
const onUsage = vi.fn();
const mount = () => render(<BankedResets accountId="codex:test" accountName="seat@example.test" revision={0} onUsage={onUsage} />);
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.mocked(fetchAccountResets).mockResolvedValue(inventory);
  vi.mocked(consumeAccountReset).mockResolvedValue({ outcome: "reset", resets: { ...inventory, available_count: 0, credits: [], can_redeem: false } });
});
afterEach(cleanup);

it("shows the authoritative count, expiry and selected account before sending", async () => {
  mount();
  await screen.findByText("3 available");
  expect(screen.getByText(/Expires/).textContent).toContain("2099");
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  expect(screen.getByRole("group").textContent).toContain("seat@example.test");
  expect(consumeAccountReset).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(consumeAccountReset).not.toHaveBeenCalled();
});

it("consumes once after confirmation and updates inventory", async () => {
  mount();
  await screen.findByText("3 available");
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  const confirm = screen.getByRole("button", { name: "Confirm and use reset" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await screen.findByText("0 available");
  expect(consumeAccountReset).toHaveBeenCalledTimes(1);
  expect(consumeAccountReset).toHaveBeenCalledWith("codex:test", expect.objectContaining({ credit_id: "credit-1", account_key: inventory.account_key }));
  expect(sessionStorage.getItem("subscription-reset:codex:test")).toBeNull();
});

it("retains the same attempt across lost replies and panel remounts without auto-retry", async () => {
  vi.mocked(consumeAccountReset).mockRejectedValueOnce(new Error("offline"));
  const view = mount();
  await screen.findByText("3 available");
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm and use reset" }));
  await screen.findByRole("alert");
  const attempt = vi.mocked(consumeAccountReset).mock.calls[0][1];
  view.unmount();
  mount();
  await screen.findByText("3 available");
  expect(consumeAccountReset).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Check / retry same request" }));
  await screen.findByText("0 available");
  expect(vi.mocked(consumeAccountReset).mock.calls[1][1]).toEqual(attempt);
});

it("clears a rejected account identity without silently using the replacement account", async () => {
  vi.mocked(consumeAccountReset).mockRejectedValueOnce(new ResetRequestError("account_changed"));
  mount();
  await screen.findByText("3 available");
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm and use reset" }));
  await screen.findByRole("alert");
  expect(sessionStorage.getItem("subscription-reset:codex:test")).toBeNull();
  expect(screen.queryByRole("button", { name: "Use reset" })).toBeNull();
});

it("explains count-only inventory and lets the service select the next reset", async () => {
  vi.mocked(fetchAccountResets).mockResolvedValue({ ...inventory, credits: null });
  mount();
  await screen.findByText(/reports a count only/);
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm and use reset" }));
  await waitFor(() => expect(consumeAccountReset).toHaveBeenCalledWith("codex:test", expect.objectContaining({ credit_id: null })));
});

it("does not offer expired reset details", async () => {
  vi.mocked(fetchAccountResets).mockResolvedValue({ ...inventory, credits: [{ ...inventory.credits![0], expires_at: "2000-01-01T00:00:00Z" }] });
  mount();
  await screen.findByText("3 available");
  expect((screen.getByRole("button", { name: "Use reset" }) as HTMLButtonElement).disabled).toBe(true);
});

it("offers a browser handoff without pretending to know the provider's reset count", async () => {
  vi.mocked(fetchAccountResets).mockResolvedValue({ ...inventory, status: "browser", available_count: null, credits: null, can_redeem: false, manage_url: "https://claude.ai/settings/usage" });
  mount();
  await screen.findByText(/manages resets on its usage page/);
  expect(screen.queryByText("0 available")).toBeNull();
  expect(screen.queryByRole("button", { name: "Use reset" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open provider usage" }));
  expect(openExternalUrl).toHaveBeenCalledWith("https://claude.ai/settings/usage");
  expect(screen.getByText(/In the browser/).textContent).toContain("seat@example.test");
});

it("keeps the account identity that was presented for confirmation across refreshes", async () => {
  mount();
  await screen.findByText("3 available");
  fireEvent.click(screen.getByRole("button", { name: "Use reset" }));
  vi.mocked(fetchAccountResets).mockResolvedValue({ ...inventory, account_key: "b".repeat(64), available_count: 4 });
  fireEvent.click(screen.getByRole("button", { name: "Refresh resets" }));
  await screen.findByText("4 available");
  fireEvent.click(screen.getByRole("button", { name: "Confirm and use reset" }));
  await waitFor(() => expect(consumeAccountReset).toHaveBeenCalledWith("codex:test", expect.objectContaining({ account_key: inventory.account_key })));
});
