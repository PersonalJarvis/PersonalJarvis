import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GigiAvatar } from "./GigiAvatar";

describe("Gigi portrait", () => {
  it("keeps SVG paints local when the roster and profile mount together", () => {
    const { container } = render(<><GigiAvatar /><GigiAvatar /></>);
    const ids = Array.from(container.querySelectorAll("[id]"), el => el.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const svg of container.querySelectorAll("svg")) {
      const localIds = new Set(Array.from(svg.querySelectorAll("[id]"), el => el.id));
      for (const el of svg.querySelectorAll("[fill], [stroke], [filter]")) {
        for (const attribute of ["fill", "stroke", "filter"]) {
          const reference = el.getAttribute(attribute)?.match(/^url\(#(.+)\)$/);
          if (reference) expect(localIds.has(reference[1])).toBe(true);
        }
      }
    }
  });

  it("keeps tiny message portraits static and the host's accessible name intact", () => {
    const { container } = render(<GigiAvatar size={18} />);
    expect(container.firstElementChild?.getAttribute("data-animated")).toBe("false");
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("button, [tabindex], img, canvas")).toBeNull();
    expect(container.querySelector("svg")?.getAttribute("width")).toBe("18");
  });
});
