import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatMarkdown, mediaKind, normalizeMediaMarkup, safeMediaUrl } from "./ChatMarkdown";

afterEach(cleanup);

describe("chat visual fences", () => {
  it.each(["HTML", "htm", "text/html"])("previews %s instead of leaving source", async language => {
    render(<ChatMarkdown text={`\`\`\`${language}\n<div>Visual card</div>\n\`\`\``} />);
    expect((await screen.findByTestId("inline-html-frame")).getAttribute("srcdoc")).toContain("Visual card");
  });

  it.each(["SVG", "xml", "image/svg+xml", ""])("previews SVG with the %s label", async language => {
    render(<ChatMarkdown text={`\`\`\`${language}\n<svg width="80" height="40"><rect width="80" height="40"/></svg>\n\`\`\``} />);
    const image = await screen.findByTestId("inline-svg");
    expect(decodeURIComponent(image.getAttribute("src")!)).toContain('xmlns="http://www.w3.org/2000/svg"');
  });

  it.each(["~~~", "````", "```"])("preserves HTML media during streaming in %s fences", async marker => {
    const code = '<div><img src="https://example.test/chart.png"></div>';
    const text = `${marker}html\n${code}\n`;
    expect(normalizeMediaMarkup(text)).toBe(text);
    expect(normalizeMediaMarkup(text + marker)).toBe(text + marker);
    const { rerender } = render(<ChatMarkdown text={text} />);
    expect((await screen.findByTestId("inline-html-frame")).getAttribute("srcdoc")).toContain(code);
    rerender(<ChatMarkdown text={text + marker} />);
    expect(screen.getByTestId("inline-html-frame").getAttribute("srcdoc")).toContain(code);
  });

  it("keeps JSON results and explicit text as code", () => {
    const { container } = render(<ChatMarkdown text={'```json\n{"ok":true,"artifacts":[]}\n```\n\n```text\n<svg><rect/></svg>\n```'} />);
    expect(container.querySelectorAll("pre")).toHaveLength(2);
    expect(screen.queryByTestId("rendered-fence")).toBeNull();
  });
});

describe("shared chat media", () => {
  it("renders every image and video in a mixed response", () => {
    const { container } = render(<ChatMarkdown text={'![First](https://example.test/a.png)\n\n![Second](https://example.test/b.webp)\n\n[Clip](https://example.test/video.mp4)\n\n[Audio](https://example.test/audio.mp3)'} />);
    expect(screen.getAllByRole("img")).toHaveLength(2);
    const video = container.querySelector("video")!;
    expect(video.getAttribute("src")).toBe("https://example.test/video.mp4");
    expect(video.hasAttribute("controls")).toBe(true);
    expect(video.hasAttribute("autoplay")).toBe(false);
    expect(video.getAttribute("preload")).toBe("metadata");
    expect(container.querySelector("audio")?.hasAttribute("controls")).toBe(true);
  });

  it.each(["mp4", "webm", "mov", "ogv"])("renders archived %s receipts with video controls", extension => {
    const url = `/api/outputs/run/files/tasks/chat/artifacts/files/clip.${extension}/download?disposition=inline`;
    render(<ChatMarkdown text={`[Clip](<${url}>)`} />);
    expect(screen.getByLabelText("Clip").tagName).toBe("VIDEO");
    expect(screen.getByLabelText("Clip").getAttribute("src")).toBe(url);
  });

  it("honors an extensionless video URL without altering its signed query", () => {
    const url = "https://example.test/object?signature=a%2Bb#jarvis-media=video%2Fmp4";
    render(<ChatMarkdown text={`[Video](<${url}>)`} />);
    expect(screen.getByLabelText("Video").getAttribute("src")).toBe(url);
  });

  it("renders a video even when a provider used Markdown image syntax", () => {
    const { container } = render(<ChatMarkdown text="![Movie](https://example.test/movie.webm)" />);
    expect(container.querySelector("video")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("keeps a download link without duplicating an image preview", () => {
    const base = "/api/outputs/run/files/tasks/chat/artifacts/files/photo.png/download";
    render(<ChatMarkdown text={`![Photo](${base}?disposition=inline)\n\n[Download](${base})`} />);
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("link", {name:"Download"})).toBeTruthy();
  });

  it("opens images in an accessible viewer", () => {
    render(<ChatMarkdown text="![Photo](https://example.test/photo.png)" />);
    fireEvent.click(screen.getByRole("button", {name:"Enlarge Photo"}));
    expect(within(screen.getByRole("dialog")).getByRole("img").getAttribute("src")).toBe("https://example.test/photo.png");
  });

  it("shows an explicit error and retains access to a failed video", () => {
    render(<ChatMarkdown text="[Clip](https://example.test/clip.mp4)" />);
    fireEvent.error(screen.getByLabelText("Clip"));
    expect(screen.getByRole("status").textContent).toContain("cannot be loaded or played");
    expect(screen.getByRole("link", {name:/Open file/}).getAttribute("href")).toBe("https://example.test/clip.mp4");
  });

  it("retries a changed source without retaining the previous error", () => {
    const {rerender} = render(<ChatMarkdown text="![Photo](https://example.test/old.png)" />);
    fireEvent.error(screen.getByRole("img"));
    rerender(<ChatMarkdown text="![Photo](https://example.test/new.png)" />);
    expect(screen.getByRole("img").getAttribute("src")).toBe("https://example.test/new.png");
  });

  it("preserves playback when streamed text and another image arrive", () => {
    const text = "[Clip](https://example.test/clip.mp4)";
    const {rerender} = render(<ChatMarkdown text={text} />);
    (screen.getByLabelText("Clip") as HTMLVideoElement).currentTime = 1.25;
    rerender(<ChatMarkdown text={text + "\n\nMore text.\n\n![Photo](https://example.test/photo.png)"} />);
    expect((screen.getByLabelText("Clip") as HTMLVideoElement).currentTime).toBe(1.25);
  });

  it("retries without changing a signed media URL", () => {
    const url = "https://example.test/clip.mp4?signature=abc";
    render(<ChatMarkdown text={`[Clip](${url})`} />);
    fireEvent.error(screen.getByLabelText("Clip"));
    fireEvent.click(screen.getByRole("button", {name:"Retry"}));
    expect(screen.getByLabelText("Clip").getAttribute("src")).toBe(url);
  });

  it("recognizes safe HTML media tags without running their handlers", () => {
    const {container} = render(<ChatMarkdown text={'<video controls><source src="https://example.test/clip.mp4" type="video/mp4"></video>\n\n<img src="https://example.test/photo.png" onerror="alert(1)" alt="Photo">'} />);
    expect(container.querySelector("video")).toBeTruthy();
    expect(screen.getByRole("img").hasAttribute("onerror")).toBe(false);
    expect(container.querySelector("script")).toBeNull();
  });

  it("does not turn HTML code examples into media tags", () => {
    const text = '```text\n<video src="https://example.test/clip.mp4"></video>\n```';
    expect(normalizeMediaMarkup(text)).toBe(text);
  });

  it.each(["javascript:alert(1)", "file:///private/image.png", "C:\\private\\image.png", "data:text/html;base64,PHNjcmlwdD4=", "https://user:password@example.test/image.png"])("rejects unsafe or unarchived media URL %s", url => {
    expect(safeMediaUrl(url)).toBeNull();
    expect(mediaKind(url)).toBeNull();
  });
});
