import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ComposerBrainPicker, type BrainSection } from "./ComposerBrainPicker";
import { MODEL_ACCESS_KEY } from "@/lib/modelAccess";

const sections: BrainSection[] = [
  { id: "openai", family: "openai", access: "api", label: "OpenAI", icon: <span>O</span> },
  { id: "openai-codex", family: "openai", access: "subscription", label: "Codex", icon: <span>O</span> },
  { id: "gemini", family: "gemini", access: "api", label: "Gemini", icon: <span>G</span> },
];
const groups = sections.map((section) => ({ id: section.id, label: section.label,
  options: [{ value: `${section.id}\u0001model`, label: `${section.label} model` }],
}));
function mount(offered = sections, onChange = vi.fn()) {
  return { onChange, ...render(<ComposerBrainPicker value="openai\u0001model" currentSection="openai"
    sections={offered} groups={groups} onChange={onChange} ariaLabel="Model" fallbackLabel="Model" searchPlaceholder="Search" />) };
}
async function open() {
  fireEvent.click(screen.getByTestId("composer-model"));
  return await screen.findByTestId("composer-model-panel");
}
beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); localStorage.clear(); });

test("one brand mark opens subscription first and remembers API browsing across reopening", async () => {
  const { onChange, unmount } = mount();
  let panel = await open();
  expect(within(panel).getByTestId("composer-model-rail").querySelectorAll("button")).toHaveLength(3);
  expect(screen.queryByTestId("composer-model-rail-openai-codex")).toBeNull();
  expect(within(panel).getByRole("radio", { name: "Subscription" }).getAttribute("aria-checked")).toBe("true");
  expect(within(panel).getByRole("option").textContent).toContain("Codex model");
  fireEvent.click(within(panel).getByRole("radio", { name: "API key" }));
  expect(within(panel).getByRole("option").textContent).toContain("OpenAI model");
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(within(panel).getByRole("radio", { name: "API key" }), { key: "Escape" });
  panel = await open();
  expect(within(panel).getByRole("radio", { name: "API key" }).getAttribute("aria-checked")).toBe("true");
  unmount(); mount(); panel = await open();
  expect(within(panel).getByRole("radio", { name: "API key" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.keyDown(within(panel).getByRole("radio", { name: "API key" }), { key: "ArrowLeft" });
  expect(within(panel).getByRole("radio", { name: "Subscription" }).getAttribute("aria-checked")).toBe("true");
  expect(document.activeElement).toBe(within(panel).getByRole("radio", { name: "Subscription" }));
});

test("API-only access works without a switch, and a disconnected subscription is not the default", async () => {
  const view = mount([sections[0]]);
  let panel = await open();
  expect(within(panel).queryByRole("radiogroup")).toBeNull();
  expect(within(panel).getByRole("option").textContent).toContain("OpenAI model");
  view.unmount(); mount([sections[0], { ...sections[1], muted: true }]); panel = await open();
  expect(within(panel).getByRole("radio", { name: "API key" }).getAttribute("aria-checked")).toBe("true");
});

test("search keeps both access paths explicit and selecting a result remembers its path", async () => {
  const { onChange } = mount();
  const panel = await open();
  fireEvent.change(screen.getByPlaceholderText("Search"), { target: { value: "model" } });
  expect(within(panel).queryByRole("radiogroup")).toBeNull();
  expect(within(panel).getAllByRole("option")).toHaveLength(3);
  fireEvent.click(within(panel).getByRole("option", { name: /Codex model/ }));
  expect(onChange).toHaveBeenCalledWith("openai-codex\u0001model");
  expect(JSON.parse(localStorage.getItem(MODEL_ACCESS_KEY)!)).toEqual({ openai: "subscription" });
});
