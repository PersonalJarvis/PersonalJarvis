import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import * as api from "@/lib/appshotApi";
import { useEventStore } from "@/store/events";
import { useAppshotClaim } from "./useAppshotClaim";

const language = vi.hoisted(() => ({ locale: "en", name: "Assistant" }));
vi.mock("@/i18n", () => ({ useT: () => {
  const { locale, name } = language;
  return (key: string) => `${locale}:${name}:${key}`;
} }));

const shot: api.AppshotMeta = { id: "shot", width: 10, height: 10, label: "Screen", app_name: "Editor",
  trigger: "shortcut", taken_at: 1, delivered_to: "message" };
const file = new File(["image"], "shot.png", { type: "image/png" });
const toast = vi.fn();
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
beforeEach(() => {
  language.locale = "en"; language.name = "Assistant";
  toast.mockClear();
  useEventStore.setState({ events: [], pushToast: toast });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("callback, locale and name rerenders do not re-fetch but delivery uses their latest values", async () => {
  const pending = deferred<{ appshot: api.AppshotMeta | null }>();
  const read = vi.spyOn(api, "fetchPendingAppshot").mockReturnValue(pending.promise);
  const claim = vi.spyOn(api, "claimPendingAppshot").mockResolvedValue(file);
  const first = vi.fn(); const latest = vi.fn();
  const view = renderHook(({ attach }) => useAppshotClaim(attach, true, "chat-a"), { initialProps: { attach: first } });
  language.locale = "de"; language.name = "Updated";
  view.rerender({ attach: latest });
  view.rerender({ attach: latest });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve({ appshot: shot }); });
  expect(claim).toHaveBeenCalledTimes(1);
  expect(first).not.toHaveBeenCalled();
  expect(latest).toHaveBeenCalledWith([file]);
  expect(toast).toHaveBeenCalledWith("info", "de:Updated:appshots.chip_label");
  view.rerender({ attach: vi.fn() });
  expect(read).toHaveBeenCalledTimes(1);
});

it("a chat switch during a pending GET re-reads serially and claims once for the active recipient", async () => {
  const pending = deferred<{ appshot: api.AppshotMeta | null }>();
  const read = vi.spyOn(api, "fetchPendingAppshot").mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ appshot: shot });
  const claim = vi.spyOn(api, "claimPendingAppshot").mockResolvedValue(file);
  const first = vi.fn(); const next = vi.fn();
  const view = renderHook(({ attach, recipient }) => useAppshotClaim(attach, true, recipient), {
    initialProps: { attach: first, recipient: "chat-a" },
  });
  view.rerender({ attach: next, recipient: "chat-b" });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve({ appshot: shot }); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(claim).toHaveBeenCalledTimes(1);
  expect(first).not.toHaveBeenCalled();
  expect(next).toHaveBeenCalledWith([file]);
});

it("does not consume a pending picture after this recipient is disabled", async () => {
  const pending = deferred<{ appshot: api.AppshotMeta | null }>();
  vi.spyOn(api, "fetchPendingAppshot").mockReturnValue(pending.promise);
  const claim = vi.spyOn(api, "claimPendingAppshot").mockResolvedValue(file);
  const attach = vi.fn();
  const view = renderHook(({ enabled }) => useAppshotClaim(attach, enabled, "chat-a"), { initialProps: { enabled: true } });
  view.rerender({ enabled: false });
  await act(async () => { pending.resolve({ appshot: shot }); });
  expect(claim).not.toHaveBeenCalled();
  expect(attach).not.toHaveBeenCalled();
});

it("leaves an unclaimed image on the backend if the composer unmounts during discovery", async () => {
  const pending = deferred<{ appshot: api.AppshotMeta | null }>();
  vi.spyOn(api, "fetchPendingAppshot").mockReturnValue(pending.promise);
  const claim = vi.spyOn(api, "claimPendingAppshot").mockResolvedValue(file);
  const view = renderHook(() => useAppshotClaim(vi.fn(), true, "chat-a"));
  view.unmount();
  await act(async () => { pending.resolve({ appshot: shot }); });
  expect(claim).not.toHaveBeenCalled();
});

it("a new shortcut event retries a failed discovery without rerender-driven polling", async () => {
  const read = vi.spyOn(api, "fetchPendingAppshot").mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ appshot: shot });
  const claim = vi.spyOn(api, "claimPendingAppshot").mockResolvedValue(file);
  const attach = vi.fn();
  renderHook(() => useAppshotClaim(attach, true, "chat-a"));
  await act(async () => {});
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => {
    useEventStore.setState({ events: [{ id: "new-shot", name: "AppshotTaken", ts: 1, payload: { delivered_to: "message" } }] });
  });
  expect(read).toHaveBeenCalledTimes(2);
  expect(claim).toHaveBeenCalledTimes(1);
  expect(attach).toHaveBeenCalledWith([file]);
});
