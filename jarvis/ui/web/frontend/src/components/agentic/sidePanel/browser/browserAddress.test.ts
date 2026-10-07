import { describe, expect, it } from "vitest";
import { normalizeBrowserAddress } from "./browserAddress";

describe("normalizeBrowserAddress", () => {
  it("sends bare ports and local hosts over http", () => {
    expect(normalizeBrowserAddress("5173")).toBe("http://localhost:5173/");
    expect(normalizeBrowserAddress(":3000/app")).toBe("http://localhost:3000/app");
    expect(normalizeBrowserAddress("localhost:8080")).toBe("http://localhost:8080/");
    expect(normalizeBrowserAddress("127.0.0.1:4321/x?y=1")).toBe("http://127.0.0.1:4321/x?y=1");
  });

  it("adds https to other hosts and keeps explicit web schemes", () => {
    expect(normalizeBrowserAddress("  example.com/docs ")).toBe("https://example.com/docs");
    expect(normalizeBrowserAddress("http://intranet.test")).toBe("http://intranet.test/");
  });

  it("refuses non-web schemes, blanks and text with spaces", () => {
    expect(normalizeBrowserAddress("javascript:alert(1)")).toBeNull();
    expect(normalizeBrowserAddress("file:///etc/passwd")).toBeNull();
    expect(normalizeBrowserAddress("")).toBeNull();
    expect(normalizeBrowserAddress("two words")).toBeNull();
  });
});
