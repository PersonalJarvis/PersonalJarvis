import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useCityReducedMotion } from "./cityMotion";

it("reacts to an accessibility preference changed after the city mounts", () => {
  const original = Object.getOwnPropertyDescriptor(window, "matchMedia");
  class Media extends EventTarget { matches = false; }
  const media = new Media();
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => media });
  const hook = renderHook(useCityReducedMotion);
  try {
    expect(hook.result.current).toBe(false);
    act(() => { media.matches = true; media.dispatchEvent(new Event("change")); });
    expect(hook.result.current).toBe(true);
    act(() => { media.matches = false; media.dispatchEvent(new Event("change")); });
    expect(hook.result.current).toBe(false);
  } finally {
    hook.unmount();
    if (original) Object.defineProperty(window, "matchMedia", original);
    else Reflect.deleteProperty(window, "matchMedia");
  }
});
