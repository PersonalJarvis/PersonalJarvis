import { describe, it, expect, beforeEach } from "vitest";
import { buildAudioSocketUrl } from "./realtimeAudio";

describe("realtime audio client", () => {
  beforeEach(() => {
    // @ts-expect-error test shim
    global.window = { location: { protocol: "https:", host: "app.example" }, __JARVIS_TOKEN: "tok" };
  });

  it("builds a wss /ws/audio url with the token", () => {
    expect(buildAudioSocketUrl()).toBe("wss://app.example/ws/audio?token=tok");
  });
});
