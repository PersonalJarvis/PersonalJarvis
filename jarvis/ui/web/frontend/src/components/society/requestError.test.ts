import { describe, expect, it } from "vitest";
import { requestError, RuntimeProviderUnsupported } from "./data";

describe("requestError", () => {
  it("types a runtime that cannot drive the provider, so the UI can translate it", () => {
    const error = requestError({ detail: { reason: "runtime_provider_unsupported", detail: "English text" } }, "HTTP 422");
    expect(error).toBeInstanceOf(RuntimeProviderUnsupported);
    expect(error.message).toBe("English text");
  });

  it("keeps a plain detail and falls back to the status", () => {
    expect(requestError({ detail: "plain" }, "HTTP 422")).not.toBeInstanceOf(RuntimeProviderUnsupported);
    expect(requestError({ detail: "plain" }, "HTTP 422").message).toBe("plain");
    expect(requestError({ detail: { reason: "other" } }, "HTTP 409").message).toBe("HTTP 409");
    expect(requestError(null, "create 500").message).toBe("create 500");
  });
});
