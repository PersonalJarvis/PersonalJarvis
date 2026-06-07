/**
 * Regression guard for the "invisible off-switch" bug.
 *
 * On the matte-black theme (--background #0A0A0A) the unchecked switch track used
 * --input (#1A1A1A) and the thumb used --background (#0A0A0A) — both nearly
 * identical to the page background, so an OFF switch vanished. The only disabled
 * skill (memory-save) therefore looked like it had no toggle at all. The off
 * state must use a clearly visible colour.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { Switch } from "@/components/ui/switch";

afterEach(cleanup);

describe("Switch off-state visibility", () => {
  it("an unchecked switch does not paint its track with the near-invisible bg-input", () => {
    const { container } = render(
      <Switch checked={false} onCheckedChange={() => {}} />,
    );
    const root = container.querySelector('[role="switch"]') as HTMLElement;
    expect(root).toBeTruthy();
    expect(root.className).not.toContain("bg-input");
    expect(root.className).toContain("bg-muted-foreground");
  });
});
