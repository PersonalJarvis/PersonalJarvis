import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./IntroSequence", () => ({ IntroSequence: () => <div data-testid="intro-seq" /> }));
import { IntroClip } from "./IntroClip";

afterEach(cleanup);

it("renders the IntroSequence fallback when no src", () => {
  render(<IntroClip />);
  expect(screen.getByTestId("intro-seq")).toBeDefined();
});

it("renders a video element when src is given", () => {
  const { container } = render(<IntroClip src="/static/intro.webm" />);
  expect(container.querySelector("video")).not.toBeNull();
});
