import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgenticBrowser, browserDestination } from "./AgenticBrowser";
import { openExternalUrl } from "@/lib/openExternal";

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ assistantName: "Nova" }),
}));

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => undefined),
}));

describe("browserDestination", () => {
  it("keeps HTTP addresses and fills in a scheme for domains", () => {
    expect(browserDestination("https://example.com/docs")?.url).toBe(
      "https://example.com/docs",
    );
    expect(browserDestination("example.com/docs")?.url).toBe("https://example.com/docs");
  });

  it("keeps localhost usable without requiring TLS", () => {
    expect(browserDestination("localhost:5173/preview")?.url).toBe(
      "http://localhost:5173/preview",
    );
  });

  it("does not disclose plain words to a hardcoded search provider", () => {
    expect(browserDestination("browser engines")).toBeNull();
  });

  it("rejects active and local-file schemes", () => {
    expect(browserDestination("javascript:alert(1)")).toBeNull();
    expect(browserDestination("file:///tmp/private.txt")).toBeNull();
    expect(browserDestination("data:text/html,hello")).toBeNull();
  });

  it("refuses the application origin inside its own frame", () => {
    expect(
      browserDestination("http://localhost:47821/settings", "http://localhost:47821"),
    ).toBeNull();
  });
});

describe("AgenticBrowser", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("opens on a calm local start page and focuses the address bar", () => {
    render(<AgenticBrowser onClose={vi.fn()} />);

    expect(screen.getByTestId("agentic-browser")).toBeTruthy();
    expect(screen.queryByTestId("agentic-browser-frame")).toBeNull();
    expect(screen.getByLabelText("Enter a web address")).toBe(document.activeElement);
  });

  it("loads a typed destination inside the browser surface", () => {
    render(<AgenticBrowser onClose={vi.fn()} />);
    const address = screen.getByLabelText("Enter a web address");

    fireEvent.change(address, { target: { value: "example.com" } });
    fireEvent.submit(address.closest("form")!);

    expect(screen.getByTestId("agentic-browser-frame").getAttribute("src")).toBe(
      "https://example.com/",
    );
    expect((address as HTMLInputElement).value).toBe("");
    expect(address.getAttribute("placeholder")).toBe("Open another web address");
    expect(screen.getByTestId("agentic-browser-frame").getAttribute("sandbox")).toBe(
      "allow-forms allow-same-origin allow-scripts",
    );
  });

  it("refuses unsafe schemes instead of handing them to the frame", () => {
    render(<AgenticBrowser onClose={vi.fn()} />);
    const address = screen.getByLabelText("Enter a web address");

    fireEvent.change(address, { target: { value: "javascript:alert(1)" } });
    fireEvent.submit(address.closest("form")!);

    expect(screen.getByRole("alert").textContent).toContain("HTTP");
    expect(screen.queryByTestId("agentic-browser-frame")).toBeNull();
  });

  it("can hand the current page to the system browser", () => {
    render(<AgenticBrowser onClose={vi.fn()} />);
    const address = screen.getByLabelText("Enter a web address");
    fireEvent.change(address, { target: { value: "example.com" } });
    fireEvent.submit(address.closest("form")!);

    fireEvent.click(
      screen.getByLabelText("Open the entered address in the system browser"),
    );

    expect(openExternalUrl).toHaveBeenCalledWith("https://example.com/");
  });

  it("closes through its own browser control", () => {
    const onClose = vi.fn();
    render(<AgenticBrowser onClose={onClose} />);

    fireEvent.click(screen.getByTestId("agentic-browser-close"));

    expect(onClose).toHaveBeenCalledOnce();
  });
});
