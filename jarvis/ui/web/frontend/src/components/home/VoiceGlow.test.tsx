import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VoiceGlow } from "@/components/home/VoiceGlow";

describe("VoiceGlow", () => {
  it("falls back to the CSS light where WebGL does not exist", () => {
    // jsdom has no WebGLRenderingContext — the same as a GPU-less WebView.
    render(<VoiceGlow active />);
    const glow = screen.getByTestId("voice-glow");
    expect(glow.getAttribute("data-renderer")).toBe("css");
    expect(glow.hasAttribute("aria-hidden")).toBe(true);
    expect(glow.children).toHaveLength(3);
  });

  it("rests as a faint, still wash while no call is open", () => {
    render(<VoiceGlow active={false} />);
    for (const pool of Array.from(screen.getByTestId("voice-glow").children) as HTMLElement[]) {
      expect(pool.style.opacity).toBe("0.3");
      expect(pool.style.transform).toBe("translateX(-50%)");
    }
  });
});
