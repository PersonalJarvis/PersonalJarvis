import { afterEach, describe, expect, it, vi } from "vitest";
import { buildMissionSocketUrl, fetchMissionToken } from "./missionAuth";

describe("mission socket authorization", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetches a short-lived token with the authenticated session cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ token: " mission-token " }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchMissionToken()).resolves.toBe("mission-token");
    expect(fetchMock).toHaveBeenCalledWith("/api/missions/auth/token", {
      cache: "no-store",
      credentials: "same-origin",
    });
  });

  it("rejects an empty token response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({}),
    }));

    await expect(fetchMissionToken()).rejects.toThrow(
      "Mission authorization returned no token",
    );
  });

  it("builds a token-free same-origin WebSocket URL", () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example" },
    });

    expect(buildMissionSocketUrl("/api/missions/ws")).toBe(
      "wss://app.example/api/missions/ws",
    );
  });
});
