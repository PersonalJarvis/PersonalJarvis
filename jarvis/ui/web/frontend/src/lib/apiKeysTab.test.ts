import { describe, expect, it } from "vitest";

import { apiKeysHealthError } from "@/lib/apiKeysTab";

const error = { status: "error", reason: "bad_key", detail: "", subject_id: "x" } as const;
const ok = { status: "ok", reason: "ok", detail: "", subject_id: "x" } as const;

describe("apiKeysHealthError", () => {
  it("flags a failing section the page shows", () => {
    expect(apiKeysHealthError({ realtime: error })).toBe(true);
    expect(apiKeysHealthError({ subagents: error })).toBe(true);
    expect(apiKeysHealthError({ advanced: error })).toBe(true);
  });

  it("ignores sections the page does not show", () => {
    expect(
      apiKeysHealthError({ brain: error, tts: error, stt: error, dictation: error, local_models: error }),
    ).toBe(false);
  });

  it("stays calm on healthy or missing sections", () => {
    expect(apiKeysHealthError({})).toBe(false);
    expect(apiKeysHealthError({ realtime: ok, subagents: undefined })).toBe(false);
  });
});
