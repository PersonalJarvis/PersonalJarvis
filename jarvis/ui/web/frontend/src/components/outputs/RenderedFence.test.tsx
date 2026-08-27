/**
 * An `html` or `svg` fence in a Markdown deliverable is DRAWN, with its
 * markup one click away — and the HTML runs in the artifact page's sandbox
 * model, never in the app's origin.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { MarkdownProse } from "@/components/outputs/MarkdownProse";
import {
  INLINE_HTML_CSP,
  INLINE_HTML_SIZE_MESSAGE,
  wrapInlineHtml,
} from "@/components/outputs/RenderedFence";

afterEach(() => cleanup());

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>';

describe("wrapInlineHtml", () => {
  it("wraps a fragment in a themed page with the artifact CSP and the size reporter", () => {
    const doc = wrapInlineHtml("<div>hi</div>", "dark", ":r1:");
    expect(doc.startsWith("<!doctype html>")).toBe(true);
    expect(doc).toContain('data-theme="dark"');
    expect(doc).toContain(`content="${INLINE_HTML_CSP}"`);
    expect(doc).toContain("<div>hi</div>");
    expect(doc).toContain(INLINE_HTML_SIZE_MESSAGE);
    expect(doc).toContain('token:":r1:"');
  });

  it("stamps a whole page instead of wrapping it", () => {
    const page = "<html><head><title>t</title></head><body><p>x</p></body></html>";
    const doc = wrapInlineHtml(page, "light", "a");
    expect(doc.match(/<html/g)).toHaveLength(1);
    expect(doc).toContain('<html data-theme="light">');
    expect(doc).toContain(`<head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy"`);
    expect(doc).toContain("</script></body>");
  });

  it("keeps a page's own theme stamp and adds a head when it has none", () => {
    const doc = wrapInlineHtml('<html data-theme="dark"><body>x</body></html>', "light", "a");
    expect(doc).toContain('<html data-theme="dark"><head><meta charset="utf-8">');
    expect(doc).not.toContain('data-theme="light"');
  });
});

describe("MarkdownProse fences", () => {
  it("draws an svg fence as a picture and an html fence in a sandboxed frame", () => {
    render(
      <MarkdownProse
        slug="run-1"
        path="report.md"
        files={[]}
        text={"# Chart\n\n```svg\n" + SVG + "\n```\n\n```html\n<div id=\"card\">hi</div>\n```\n"}
      />,
    );
    const fences = screen.getAllByTestId("rendered-fence");
    expect(fences.map((f) => f.getAttribute("data-language"))).toEqual(["svg", "html"]);

    const picture = within(fences[0]).getByTestId("inline-svg") as HTMLImageElement;
    expect(picture.getAttribute("src")).toBe(
      `data:image/svg+xml;charset=utf-8,${encodeURIComponent(SVG)}`,
    );

    const frame = within(fences[1]).getByTestId("inline-html-frame") as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("srcdoc")).toContain('<div id="card">hi</div>');
    expect(frame.getAttribute("srcdoc")).toContain("Content-Security-Policy");
  });

  it("shows the markup behind the Source switch and comes back to Rendered", () => {
    render(
      <MarkdownProse slug="run-1" path="r.md" files={[]} text={"```html\n<b>bold</b>\n```\n"} />,
    );
    const fence = screen.getByTestId("rendered-fence");
    expect(fence.getAttribute("data-mode")).toBe("rendered");

    fireEvent.click(within(fence).getByRole("button", { name: "Source" }));
    expect(fence.getAttribute("data-mode")).toBe("source");
    expect(within(fence).queryByTestId("inline-html-frame")).toBeNull();
    expect(within(fence).getByText("<b>bold</b>")).toBeDefined();

    fireEvent.click(within(fence).getByRole("button", { name: "Rendered" }));
    expect(within(fence).getByTestId("inline-html-frame")).toBeDefined();
  });

  it("leaves every other fence to the code block", () => {
    render(<MarkdownProse slug="r" path="r.md" files={[]} text={"```python\nprint(1)\n```\n"} />);
    expect(screen.queryByTestId("rendered-fence")).toBeNull();
    expect(screen.getByText("print(1)")).toBeDefined();
  });
});
