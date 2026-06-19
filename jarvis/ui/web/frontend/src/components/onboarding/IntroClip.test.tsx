import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));
import { IntroClip } from "./IntroClip";

afterEach(cleanup);

it("renders the Gigi fallback when no src", () => {
  render(<IntroClip />);
  expect(screen.getByTestId("gigi")).toBeDefined();
});

it("renders a video element when src is given", () => {
  const { container } = render(<IntroClip src="/static/intro.webm" />);
  expect(container.querySelector("video")).not.toBeNull();
});
