import { afterEach, describe, expect, it } from "vitest";

import type { OverviewResponse } from "@/hooks/useLocalModels";

import {
  SNAPSHOT_MAX_BYTES,
  SNAPSHOT_VERSION,
  clearOverviewSnapshot,
  readOverviewSnapshot,
  snapshotKey,
  writeOverviewSnapshot,
} from "./localModelsSnapshot";

function payload(extra: Partial<OverviewResponse> = {}): OverviewResponse {
  return {
    server: { running: true } as OverviewResponse["server"],
    roles: { roles: [] } as unknown as OverviewResponse["roles"],
    inventory: { models: [] } as unknown as OverviewResponse["inventory"],
    recommended: { models: [] } as unknown as OverviewResponse["recommended"],
    source: "live",
    fetched_at: 1_700_000_000,
    ...extra,
  };
}

afterEach(() => {
  clearOverviewSnapshot("ollama");
});

describe("localModelsSnapshot", () => {
  it("round-trips a payload and marks the read copy as cache", () => {
    expect(writeOverviewSnapshot("ollama", payload())).toBe(true);
    const back = readOverviewSnapshot("ollama");
    expect(back?.source).toBe("cache");
    expect(back?.fetched_at).toBe(1_700_000_000);
    expect(back?.server.running).toBe(true);
  });

  it("ignores another version, broken JSON and incomplete payloads", () => {
    window.localStorage.setItem(
      snapshotKey("ollama"),
      JSON.stringify({ v: SNAPSHOT_VERSION + 1, data: payload() }),
    );
    expect(readOverviewSnapshot("ollama")).toBeNull();
    window.localStorage.setItem(snapshotKey("ollama"), "{not json");
    expect(readOverviewSnapshot("ollama")).toBeNull();
    window.localStorage.setItem(
      snapshotKey("ollama"),
      JSON.stringify({ v: SNAPSHOT_VERSION, data: { fetched_at: 1 } }),
    );
    expect(readOverviewSnapshot("ollama")).toBeNull();
  });

  it("refuses to store an oversized payload", () => {
    const big = payload({
      inventory: {
        models: [{ name: "x".repeat(SNAPSHOT_MAX_BYTES) }],
      } as unknown as OverviewResponse["inventory"],
    });
    expect(writeOverviewSnapshot("ollama", big)).toBe(false);
    expect(readOverviewSnapshot("ollama")).toBeNull();
  });

  it("neither stores nor reads a sweep taken while the server was silent", () => {
    // Ollama still booting: the payload is truthful now and ruinous later —
    // an empty inventory paints a machine that looks wiped, and each role's
    // picker then offers only the tag already configured (BUG-188).
    const offline = payload({
      inventory: {
        models: [],
        error: "Ollama is not answering on http://localhost:11434.",
      } as unknown as OverviewResponse["inventory"],
    });
    expect(writeOverviewSnapshot("ollama", offline)).toBe(false);
    expect(readOverviewSnapshot("ollama")).toBeNull();

    // A good snapshot already on disk survives the offline sweep.
    writeOverviewSnapshot("ollama", payload());
    expect(writeOverviewSnapshot("ollama", offline)).toBe(false);
    expect(readOverviewSnapshot("ollama")?.fetched_at).toBe(1_700_000_000);

    // One written by an older build is dropped on read rather than painted.
    window.localStorage.setItem(
      snapshotKey("ollama"),
      JSON.stringify({ v: SNAPSHOT_VERSION, data: offline }),
    );
    expect(readOverviewSnapshot("ollama")).toBeNull();
  });

  it("keeps one entry per provider", () => {
    writeOverviewSnapshot("ollama", payload());
    expect(readOverviewSnapshot("other")).toBeNull();
    clearOverviewSnapshot("ollama");
    expect(readOverviewSnapshot("ollama")).toBeNull();
  });
});
