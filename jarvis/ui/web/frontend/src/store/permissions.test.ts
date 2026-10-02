import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { privacySectionVisible, usePermissionsStore } from "./permissions";

function episode(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "dictation",
    reason: "denied",
    phase: "blocked",
    origin: "user",
    target: "",
    can_prompt: false,
    can_open_settings: true,
    outside_app: false,
    detail: "",
    trace_id: "srv-trace",
    opened_at_ns: 1,
    ...overrides,
  };
}

function snapshot(needed: unknown[] = [], overrides: Record<string, unknown> = {}) {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: {
      app_name: "Personal Jarvis",
      bundle_id: "ai.example.app",
      bundle_path: null,
      launched_as_bundle: true,
      stable: true,
    },
    outside_installed_app: false,
    permissions: [],
    needed,
    ...overrides,
  };
}

function stubStatus(body: unknown, ok = true) {
  const fetchMock = vi.fn().mockResolvedValue({ ok, status: ok ? 200 : 500, json: async () => body });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, snapshot: null, owner: true, inline: {}, dictationNote: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("seeding", () => {
  it("fills the episodes and the snapshot from GET /status", async () => {
    const fetchMock = stubStatus(snapshot([episode()]));

    await usePermissionsStore.getState().seed();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/permissions/status");
    const state = usePermissionsStore.getState();
    expect(state.episodes.map((e) => e.key)).toEqual(["dictation:microphone"]);
    expect(state.snapshot?.app_identity.app_name).toBe("Personal Jarvis");
  });

  it("does nothing outside the owner window", async () => {
    const fetchMock = stubStatus(snapshot([episode()]));
    usePermissionsStore.setState({ owner: false });

    await usePermissionsStore.getState().seed();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(usePermissionsStore.getState().episodes).toEqual([]);
  });

  it("is single-flight: a welcome frame and a focus during one read join it", async () => {
    const fetchMock = stubStatus(snapshot());

    await Promise.all([
      usePermissionsStore.getState().seed(),
      usePermissionsStore.getState().seed(),
      usePermissionsStore.getState().seed(),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("stays quiet and keeps what it had when the backend does not answer", async () => {
    usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode(), Date.now() - 10_000);
    stubStatus({}, false);

    await usePermissionsStore.getState().seed();

    expect(usePermissionsStore.getState().episodes).toHaveLength(1);
  });

  it("keeps what it had for an older backend whose answer has no needed[]", async () => {
    usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode(), Date.now() - 10_000);
    stubStatus({ platform: "darwin", permissions: [], features: {} });

    await usePermissionsStore.getState().seed();

    expect(usePermissionsStore.getState().episodes).toHaveLength(1);
  });

  it("a rejected fetch never throws out of seed()", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    await expect(usePermissionsStore.getState().seed()).resolves.toBeUndefined();
  });
});

describe("events", () => {
  it("only update: needed then resolved", () => {
    const { ingest } = usePermissionsStore.getState();

    ingest("PermissionNeeded", "t1", episode(), 1_000);
    expect(usePermissionsStore.getState().episodes).toHaveLength(1);

    ingest("PermissionResolved", "t1", { permissions: ["microphone"], feature: "dictation", granted: true }, 2_000);
    const state = usePermissionsStore.getState();
    expect(state.episodes).toEqual([]);
    expect(state.resolved[0]).toMatchObject({ feature: "dictation", granted: true, hadCard: true });
  });

  it("ignores events it cannot use without touching the state", () => {
    const before = usePermissionsStore.getState().episodes;

    usePermissionsStore.getState().ingest("PermissionNeeded", "t", { feature: "x" }, 1);

    expect(usePermissionsStore.getState().episodes).toBe(before);
  });
});

describe("inline surfaces", () => {
  it("are ref-counted per feature", () => {
    const { registerInline } = usePermissionsStore.getState();

    const a = registerInline("dictation");
    const b = registerInline("dictation");
    expect(usePermissionsStore.getState().inline).toEqual({ dictation: 2 });

    a();
    a(); // a second release of the same registration is a no-op
    expect(usePermissionsStore.getState().inline).toEqual({ dictation: 1 });
    b();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });
});

describe("dictation notes", () => {
  it("keeps the specific refusal when the generic error follows it", () => {
    const { noteDictationRefusal } = usePermissionsStore.getState();

    noteDictationRefusal({ source: "refused", reason: "microphone_unavailable", ts: 1_000 });
    noteDictationRefusal({ source: "error", reason: "already_running", ts: 1_500 });

    expect(usePermissionsStore.getState().dictationNote?.reason).toBe("microphone_unavailable");
  });

  it("takes a later error and can be cleared", () => {
    const { noteDictationRefusal, clearDictationNote } = usePermissionsStore.getState();

    noteDictationRefusal({ source: "refused", reason: "no_stt", ts: 1_000 });
    noteDictationRefusal({ source: "error", reason: "already_running", ts: 9_000 });
    expect(usePermissionsStore.getState().dictationNote?.reason).toBe("already_running");

    clearDictationNote();
    expect(usePermissionsStore.getState().dictationNote).toBeNull();
  });
});

describe("Privacy section visibility", () => {
  it("follows the backend's platform once a snapshot was read", () => {
    expect(privacySectionVisible({ platform: "darwin" } as never)).toBe(true);
    expect(privacySectionVisible({ platform: "win32" } as never)).toBe(false);
    expect(privacySectionVisible({ platform: "linux" } as never)).toBe(false);
  });

  it("falls back to the client's OS before any snapshot (a Windows window never flashes it)", () => {
    // jsdom's user agent is not a Mac.
    expect(privacySectionVisible(null)).toBe(false);
  });
});
