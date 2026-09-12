import { renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import type { Point } from "./kinematics";
import { usePendingCompanionFocus } from "./usePendingCompanionFocus";

it("retains the first focus request while hidden until a pose is published", () => {
  const position: { current: Point | null } = { current: null };
  const applied: Point[] = [];
  const apply = (point: Point) => { applied.push([...point]); return true; };
  const hook = renderHook(({ version, request }) => usePendingCompanionFocus(request, position, version, apply), { initialProps: { version: 0, request: 0 } });
  hook.rerender({ version: 0, request: 1 });
  expect(applied).toEqual([]);
  position.current = [1, 2, 3];
  hook.rerender({ version: 1, request: 1 });
  expect(applied).toEqual([[1, 2, 3]]);
  position.current = [4, 5, 6];
  hook.rerender({ version: 2, request: 1 });
  expect(applied).toHaveLength(1);
  hook.rerender({ version: 2, request: 2 });
  expect(applied).toEqual([[1, 2, 3], [4, 5, 6]]);
  hook.unmount();
});
