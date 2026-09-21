import { describe, expect, it } from "vitest";
import { chatLinkAction, chatUrlTransform, nativePathFromMarkdownUrl } from "./chatLinks";

describe("chat file links", () => {
  it.each([
    ["C:/Users/me/Clip.mp4", "C:/Users/me/Clip.mp4"],
    ["C:\\Users\\me\\Clip.mp4", "C:\\Users\\me\\Clip.mp4"],
    ["file:///C:/Users/me/My%20Clip.mp4", "C:/Users/me/My Clip.mp4"],
    ["file://C:/Users/me/Clip.mp4", "C:/Users/me/Clip.mp4"],
    ["file://localhost/home/me/clip.mp4", "/home/me/clip.mp4"],
    ["~/Downloads/clip.mp4", "~/Downloads/clip.mp4"],
    ["/home/me/Videos/clip.mp4", "/home/me/Videos/clip.mp4"],
  ])("keeps %s as a local path", (raw, expected) => {
    expect(nativePathFromMarkdownUrl(raw)).toBe(expected);
    expect(chatUrlTransform(raw)).toBe("#jarvis-local=" + encodeURIComponent(expected));
  });

  it.each([
    "clip.mp4",
    "https://example.test/clip.mp4",
    "javascript:alert(1)",
    "file://server/share/clip.mp4",
    "/api/outputs/run/files/clip.mp4/download",
  ])("does not treat %s as a local file", raw => {
    expect(nativePathFromMarkdownUrl(raw)).toBeNull();
  });

  it("opens a preserved local path and still sends web links out", () => {
    const href = chatUrlTransform("C:/Users/me/Clip.mp4");
    expect(chatLinkAction(href)).toEqual({ type: "local", path: "C:/Users/me/Clip.mp4" });
    expect(chatLinkAction("https://example.test/clip.mp4")).toEqual({
      type: "external",
      url: "https://example.test/clip.mp4",
    });
  });

  it("refuses to navigate the window for an empty or relative link", () => {
    expect(chatLinkAction("")).toEqual({ type: "stay" });
    expect(chatLinkAction("Clip.mp4")).toEqual({ type: "stay" });
    expect(chatLinkAction("javascript:alert(1)")).toEqual({ type: "stay" });
    expect(chatLinkAction("/api/outputs/run/download")).toEqual({ type: "allow" });
  });
});
