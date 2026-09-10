import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { clearModelMenuSnapshot, MODEL_MENU_SNAPSHOT_KEY, readModelMenuSnapshot, writeModelMenuSnapshot, type ModelMenuSnapshot } from "./modelMenuSnapshot";

const snapshot = (): ModelMenuSnapshot => ({
  version: 1, savedAt: Date.now(),
  catalog: { default_cwd: "private/workspace", shell: "private/shell", providers: [{
    id: "example", label: "Example", family: "example", runner: "brain", models_source: "curated",
    curated_models: [{ id: "small", label: "Small", efforts: ["low"] }], default_model: "small",
    keyless: false, native_resume: false, effort_levels: ["low"], default_effort: "low",
    permission_modes: [], default_permission_mode: "ask", cli_installed: null,
  }] },
  connections: [{ jarvis: "example", key_set: true, api_key_set: true, is_active_brain: false }],
  providers: [{ id: "example", label: "Example", family: "example", runner: "brain", subscription: false,
    keyless: false, platform: null, accounts: [{ id: "work", label: "Work", connected: true, mode: "subscription",
      message: "private diagnostic", email: "private@example.invalid", tier: null, warning: null }] }],
  live: { example: [{ id: "live-small", label: "Live small" }] },
});

beforeEach(clearModelMenuSnapshot);
afterEach(() => { vi.restoreAllMocks(); clearModelMenuSnapshot(); });

test("reload retains models and selections without emails, paths or unknown fields", () => {
  const source = snapshot();
  Object.assign(source, { token: "must-not-be-saved" });
  writeModelMenuSnapshot(source);
  const raw = localStorage.getItem(MODEL_MENU_SNAPSHOT_KEY)!;
  expect(raw).not.toContain("private");
  expect(raw).not.toContain("must-not-be-saved");
  clearModelMenuSnapshot();
  localStorage.setItem(MODEL_MENU_SNAPSHOT_KEY, raw);
  const restored = readModelMenuSnapshot()!;
  expect(restored.catalog.providers[0].curated_models[0].id).toBe("small");
  expect(restored.live.example[0].id).toBe("live-small");
  expect(restored.providers[0].accounts[0].id).toBe("work");
});

test.each(["not-json", '{"version":0}', JSON.stringify({ ...snapshot(), connections: [{ jarvis: "example", key_set: "yes" }] })])("corrupt or incompatible cache falls back to discovery", (raw) => {
  localStorage.setItem(MODEL_MENU_SNAPSHOT_KEY, raw);
  expect(readModelMenuSnapshot()).toBeNull();
});

test("unavailable storage still retains an in-memory snapshot", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
  writeModelMenuSnapshot(snapshot());
  expect(readModelMenuSnapshot()?.catalog.providers[0].id).toBe("example");
});

test("shared observers do not serialize and write the same data twice", () => {
  const write = vi.spyOn(Storage.prototype, "setItem");
  const source = snapshot();
  writeModelMenuSnapshot(source);
  writeModelMenuSnapshot({ ...source, live: { ...source.live } });
  expect(write).toHaveBeenCalledTimes(1);
});

test("fresh connection metadata does not make an old live model list fresh", () => {
  const source = snapshot();
  source.liveUpdatedAt = { example: Date.now() - 11 * 60_000 };
  writeModelMenuSnapshot(source);
  const raw = localStorage.getItem(MODEL_MENU_SNAPSHOT_KEY)!;
  clearModelMenuSnapshot();
  localStorage.setItem(MODEL_MENU_SNAPSHOT_KEY, raw);
  expect(readModelMenuSnapshot()?.liveUpdatedAt?.example).toBe(source.liveUpdatedAt.example);
});
