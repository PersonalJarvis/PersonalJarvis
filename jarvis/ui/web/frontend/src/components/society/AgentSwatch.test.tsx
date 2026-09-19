import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentSwatch } from "./AgentSwatch";
import type { FigureRecipe } from "./figures/figureRecipe";

const { faceCrop } = vi.hoisted(() => ({ faceCrop: vi.fn() }));
vi.mock("./figures/faceCrop", () => ({ faceCrop }));

const palette = { primary: "#112233", secondary: "#445566", accent: "#778899" };
const figure: FigureRecipe = { contract: 1, archetype: "biped", base: "rogue", parts: {} };
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("agents without a figure keep the lightweight fallback", async () => {
  const { container } = render(<AgentSwatch agent={{ name: "Scout", figure: null, palette }} />);
  await act(async () => {});
  expect(container.querySelector("img")).toBeNull();
  expect(faceCrop).not.toHaveBeenCalled();
});

test("a requested portrait replaces the fallback after the renderer loads", async () => {
  faceCrop.mockResolvedValue("data:image/png;base64,cG9ydHJhaXQ=");
  const { container } = render(<AgentSwatch agent={{ name: "Scout", figure, palette }} />);
  await waitFor(() => expect(container.querySelector("img")?.src).toContain("cG9ydHJhaXQ="));
  expect(faceCrop).toHaveBeenCalledWith(figure);
});

test("unmounting before the deferred load prevents starting portrait work", async () => {
  const pending = { ...figure, heightM: 1.234 };
  const mounted = render(<AgentSwatch agent={{ name: "Scout", figure: pending, palette }} />);
  mounted.unmount();
  await act(async () => {});
  expect(faceCrop).not.toHaveBeenCalled();
});
