import { describe, expect, it } from "vitest";
import {
  EMPTY_PROMPTS,
  MAX_EPISODES,
  RESOLVED_HOLD_MS,
  cardEpisodes,
  dismissEpisode,
  episodeForFeature,
  episodeKey,
  isCardEligible,
  isMacClient,
  isPermissionOwnerWindow,
  parseEpisode,
  recentResolved,
  reducePermissionEvent,
  seedPermissionEpisodes,
  type PermissionPromptsState,
} from "./permissionPrompts";

function needed(overrides: Record<string, unknown> = {}) {
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
    detail: "Microphone access is off.",
    ...overrides,
  };
}

function apply(
  state: PermissionPromptsState,
  name: string,
  payload: unknown,
  ts = 1_000,
  trace = "trace-1",
): PermissionPromptsState {
  return reducePermissionEvent(state, name, trace, payload, ts) ?? state;
}

describe("PermissionNeeded", () => {
  it("opens an episode keyed by feature and pane family", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed());

    expect(state.episodes).toHaveLength(1);
    expect(state.episodes[0]).toMatchObject({
      key: "dictation:microphone",
      feature: "dictation",
      permissions: ["microphone"],
      reason: "denied",
      phase: "blocked",
      origin: "user",
      trace_id: "trace-1",
      dismissed: false,
    });
  });

  it("folds event_posting into the accessibility pane", () => {
    const state = apply(
      EMPTY_PROMPTS,
      "PermissionNeeded",
      needed({ permissions: ["event_posting", "accessibility", "screen_recording"], feature: "computer_use" }),
    );

    expect(state.episodes[0].permissions).toEqual(["accessibility", "screen_recording"]);
    expect(state.episodes[0].key).toBe(episodeKey("computer_use", ["screen_recording", "accessibility"]));
  });

  it("ignores payloads it cannot render honestly", () => {
    for (const bad of [
      needed({ reason: "mystery" }),
      needed({ phase: "later" }),
      needed({ origin: "nobody" }),
      needed({ permissions: [] }),
      needed({ feature: "" }),
      null,
    ]) {
      expect(reducePermissionEvent(EMPTY_PROMPTS, "PermissionNeeded", "t", bad, 1)).toBeNull();
    }
  });

  it("keeps an unknown feature (a newer backend) so the generic copy can render it", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "teleport" }));

    expect(state.episodes[0].feature).toBe("teleport");
  });

  it("updates the same episode in place when its reason changes", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ reason: "not_determined", phase: "os_dialog" }));
    state = apply(state, "PermissionNeeded", needed({ reason: "denied", phase: "blocked" }), 2_000);

    expect(state.episodes).toHaveLength(1);
    expect(state.episodes[0]).toMatchObject({ reason: "denied", phase: "blocked", updatedAt: 2_000 });
  });

  it("treats a shrunk permission set of the same trace as the same episode", () => {
    let state = apply(
      EMPTY_PROMPTS,
      "PermissionNeeded",
      needed({ feature: "computer_use", permissions: ["screen_recording", "accessibility"] }),
    );
    state = apply(
      state,
      "PermissionNeeded",
      needed({ feature: "computer_use", permissions: ["accessibility"], reason: "needs_settings" }),
      2_000,
    );

    expect(state.episodes).toHaveLength(1);
    expect(state.episodes[0].permissions).toEqual(["accessibility"]);
  });

  it("keeps two disjoint episodes of one feature apart", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "computer_use", permissions: ["accessibility"] }));
    state = apply(state, "PermissionNeeded", needed({ feature: "computer_use", permissions: ["screen_recording"] }));

    expect(state.episodes.map((e) => e.key).sort()).toEqual([
      "computer_use:accessibility",
      "computer_use:screen_recording",
    ]);
  });

  it("caps the list", () => {
    let state = EMPTY_PROMPTS;
    for (let index = 0; index < MAX_EPISODES + 5; index++) {
      state = apply(
        state,
        "PermissionNeeded",
        needed({ feature: `f${index}`, permissions: ["microphone"] }),
        index,
        `t${index}`,
      );
    }

    expect(state.episodes).toHaveLength(MAX_EPISODES);
    expect(state.episodes.some((e) => e.feature === `f${MAX_EPISODES + 4}`)).toBe(true);
  });
});

describe("PermissionResolved", () => {
  it("removes the episode and notes the grant", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed());
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }, 5_000);

    expect(state.episodes).toEqual([]);
    expect(state.resolved).toHaveLength(1);
    expect(state.resolved[0]).toMatchObject({ feature: "dictation", granted: true, ts: 5_000, hadCard: true });
  });

  it("does not claim the card was showing for an episode that never had one", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ origin: "background" }));
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true });

    expect(state.resolved[0].hadCard).toBe(false);
  });

  it("keeps an episode of the same feature on a different permission", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "computer_use", permissions: ["accessibility"] }));
    state = apply(state, "PermissionNeeded", needed({ feature: "computer_use", permissions: ["screen_recording"] }));
    state = apply(state, "PermissionResolved", { permissions: ["accessibility"], feature: "computer_use", granted: true });

    expect(state.episodes.map((e) => e.permissions)).toEqual([["screen_recording"]]);
  });

  it("is a note even when no episode was known (granted in the OS dialog at once)", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionResolved", { permissions: ["microphone"], feature: "voice", granted: true });

    expect(state.resolved[0]).toMatchObject({ feature: "voice", granted: true, hadCard: false });
  });

  it("ignores a malformed payload and unrelated events", () => {
    expect(reducePermissionEvent(EMPTY_PROMPTS, "PermissionResolved", "t", { granted: true }, 1)).toBeNull();
    expect(reducePermissionEvent(EMPTY_PROMPTS, "SomethingElse", "t", needed(), 1)).toBeNull();
  });
});

describe("seeding from needed[]", () => {
  it("replaces the list with the server's truth", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "voice" }), 100);
    const seeded = seedPermissionEpisodes(state, [needed({ feature: "dictation", trace_id: "srv", opened_at_ns: 1 })], 5_000, 4_000);

    expect(seeded?.episodes.map((e) => e.feature)).toEqual(["dictation"]);
  });

  it("clears everything when the server has nothing open", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed(), 100);

    expect(seedPermissionEpisodes(state, [], 5_000, 4_000)?.episodes).toEqual([]);
  });

  it("keeps an episode that arrived after the request was sent", () => {
    // The event landed while the REST answer was in flight; the answer predates it.
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "voice" }), 4_500);

    const seeded = seedPermissionEpisodes(state, [], 5_000, 4_000);

    expect(seeded?.episodes.map((e) => e.feature)).toEqual(["voice"]);
  });

  it("does not resurrect an episode a PermissionResolved closed while the request was in flight", () => {
    // Seed starts at t=2000; the snapshot (built before the grant) still lists the episode;
    // the WS edge closes it at t=2100; the HTTP answer is applied at t=2200.
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed(), 1_000);
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }, 2_100);
    expect(state.episodes).toHaveLength(0);

    const seeded = seedPermissionEpisodes(state, [needed()], 2_200, 2_000);

    expect(seeded?.episodes).toEqual([]);
    expect(cardEpisodes(seeded as PermissionPromptsState, new Set())).toHaveLength(0);
  });

  it("still takes a snapshot episode for a resolve that happened BEFORE the request", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed(), 100);
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }, 500);

    const seeded = seedPermissionEpisodes(state, [needed()], 5_000, 4_000);

    expect(seeded?.episodes.map((e) => e.feature)).toEqual(["dictation"]);
  });

  it("keeps an episode that was reopened after the resolve that happened in flight", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed(), 1_000);
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }, 2_100);
    state = apply(state, "PermissionNeeded", needed({ reason: "needs_settings" }), 2_150);

    const seeded = seedPermissionEpisodes(state, [needed()], 2_200, 2_000);

    expect(seeded?.episodes.map((e) => e.reason)).toEqual(["needs_settings"]);
  });

  it("holds the 'Allowed' confirmation for at least five seconds (screen readers need the time)", () => {
    expect(RESOLVED_HOLD_MS).toBeGreaterThanOrEqual(5_000);
  });

  it("keeps Not now for an unchanged episode and drops it when the reason changed", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed(), 100);
    state = dismissEpisode(state, "dictation:microphone") ?? state;

    const same = seedPermissionEpisodes(state, [needed()], 5_000, 4_000);
    const changed = seedPermissionEpisodes(state, [needed({ reason: "needs_settings" })], 5_000, 4_000);

    expect(same?.episodes[0].dismissed).toBe(true);
    expect(changed?.episodes[0].dismissed).toBe(false);
  });

  it("leaves the state alone for an older backend without needed[]", () => {
    expect(seedPermissionEpisodes(EMPTY_PROMPTS, undefined, 1, 0)).toBeNull();
  });

  it("skips entries it cannot render", () => {
    const seeded = seedPermissionEpisodes(EMPTY_PROMPTS, [needed({ reason: "mystery" }), needed()], 1, 0);

    expect(seeded?.episodes).toHaveLength(1);
  });
});

describe("Not now", () => {
  it("is episode-scoped and memory only: the same key hides, news un-hides", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed());
    state = dismissEpisode(state, "dictation:microphone") ?? state;
    expect(cardEpisodes(state, new Set())).toEqual([]);

    // The same edge again (a re-publish of the same reason/phase): still hidden.
    state = apply(state, "PermissionNeeded", needed(), 2_000);
    expect(cardEpisodes(state, new Set())).toEqual([]);

    // Something new (macOS stopped asking, the user has to act): visible again.
    state = apply(state, "PermissionNeeded", needed({ reason: "needs_settings" }), 3_000);
    expect(cardEpisodes(state, new Set())).toHaveLength(1);
  });

  it("is a no-op for an unknown or already dismissed key", () => {
    expect(dismissEpisode(EMPTY_PROMPTS, "nope")).toBeNull();
  });
});

describe("what the card may show", () => {
  it("opens ONLY for origin user and phase blocked", () => {
    let state = EMPTY_PROMPTS;
    state = apply(state, "PermissionNeeded", needed({ feature: "voice", phase: "os_dialog", reason: "not_determined" }));
    state = apply(state, "PermissionNeeded", needed({ feature: "wake_word", origin: "background" }));
    state = apply(state, "PermissionNeeded", needed({ feature: "dictation" }));

    expect(cardEpisodes(state, new Set()).map((e) => e.feature)).toEqual(["dictation"]);
  });

  it("does not repeat a feature whose inline surface is mounted", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed());

    expect(cardEpisodes(state, new Set(["dictation"]))).toEqual([]);
  });

  it("orders the newest first", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ feature: "voice" }), 100, "a");
    state = apply(state, "PermissionNeeded", needed({ feature: "dictation" }), 200, "b");

    expect(cardEpisodes(state, new Set()).map((e) => e.feature)).toEqual(["dictation", "voice"]);
  });

  it("hands inline surfaces their feature's episode whatever its phase", () => {
    const state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed({ phase: "os_dialog", reason: "not_determined", origin: "background" }));

    expect(episodeForFeature(state, "dictation")?.phase).toBe("os_dialog");
    expect(episodeForFeature(state, "voice")).toBeNull();
  });

  it("finds a recent confirmation by feature and age", () => {
    let state = apply(EMPTY_PROMPTS, "PermissionNeeded", needed());
    state = apply(state, "PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }, 1_000);

    expect(recentResolved(state, ["dictation", "voice"], 5_000, 10_000)?.feature).toBe("dictation");
    expect(recentResolved(state, ["dictation"], 20_000, 10_000)).toBeNull();
    expect(recentResolved(state, ["voice"], 5_000, 10_000)).toBeNull();
  });
});

describe("parseEpisode", () => {
  it("takes the trace id of the entry, else the envelope's", () => {
    expect(parseEpisode({ ...needed(), trace_id: "entry" }, "envelope", 1)?.trace_id).toBe("entry");
    expect(parseEpisode(needed(), "envelope", 1)?.trace_id).toBe("envelope");
  });
});

describe("owner window", () => {
  const owner = { solo: false, embedded: true, macClient: true };

  it("is the main window of an embedded desktop app on a Mac", () => {
    expect(isPermissionOwnerWindow(owner)).toBe(true);
  });

  it("is never a detached solo window, a remote browser or a non-Mac client", () => {
    expect(isPermissionOwnerWindow({ ...owner, solo: true })).toBe(false);
    expect(isPermissionOwnerWindow({ ...owner, embedded: false })).toBe(false);
    expect(isPermissionOwnerWindow({ ...owner, macClient: false })).toBe(false);
  });

  it("draws no card on a headless backend", () => {
    expect(isCardEligible({ owner: true, headless: false })).toBe(true);
    expect(isCardEligible({ owner: true, headless: null })).toBe(true);
    expect(isCardEligible({ owner: true, headless: true })).toBe(false);
    expect(isCardEligible({ owner: false, headless: false })).toBe(false);
  });

  it("recognises a Mac user agent", () => {
    expect(isMacClient("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15")).toBe(true);
    expect(isMacClient("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")).toBe(false);
  });
});
