import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { PortraitEditor } from "./PortraitEditor";
import { defaultRecipe, resolvePalette } from "./figures/figureRecipe";
import {
  makeIllustratedPortrait, parseIllustratedPortrait, serializeIllustratedPortrait,
} from "./illustratedPortrait";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("local illustrated portraits", () => {
  test("a recipe is stable and rejects unknown versions or options", () => {
    const recipe = makeIllustratedPortrait(0xf3ab6271);
    const value = serializeIllustratedPortrait(recipe);
    expect(parseIllustratedPortrait(value)).toEqual(recipe);
    expect(parseIllustratedPortrait(value.replace("v1", "v2"))).toBeNull();
    expect(parseIllustratedPortrait(value.replace(recipe.hair, "unknown"))).toBeNull();
  });

  test("the editor changes and previews a local face without an image service", () => {
    const figure = defaultRecipe("modern");
    const colors = resolvePalette(figure);
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    function Harness() {
      const [portrait, setPortrait] = useState(serializeIllustratedPortrait(makeIllustratedPortrait(23)));
      return <PortraitEditor figure={figure} name="New agent"
        palette={{ primary: colors.primary, secondary: colors.secondary, accent: colors.accent }}
        portrait={portrait} onChange={(next) => setPortrait(next ?? "")} />;
    }
    render(<Harness />);
    expect(screen.getByTestId("agent-portrait-editor").querySelector("svg")).toBeTruthy();
    fireEvent.click(screen.getByTestId("portrait-option-glasses"));
    expect(screen.getByTestId("portrait-option-glasses").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("portrait-option-sky"));
    expect(screen.getByTestId("portrait-option-sky").getAttribute("aria-pressed")).toBe("true");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
