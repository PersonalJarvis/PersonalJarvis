import { describe, expect, it } from "vitest";

import { backendMessage } from "./backendMessage";

const dict: Record<string, string> = {
  "ns.ready": "Pronto: {phrase}",
};
const t = (key: string) => dict[key] ?? key;

describe("backendMessage", () => {
  it("renders a known code with its params", () => {
    expect(backendMessage(t, "ns", "ready", { phrase: "Olá" }, "Ready: Olá")).toBe("Pronto: Olá");
  });

  it("falls back to the backend sentence for an empty or unknown code", () => {
    expect(backendMessage(t, "ns", "", null, "English text")).toBe("English text");
    expect(backendMessage(t, "ns", "newer_code", {}, "English text")).toBe("English text");
    expect(backendMessage(t, "ns", undefined, undefined, "English text")).toBe("English text");
  });
});
