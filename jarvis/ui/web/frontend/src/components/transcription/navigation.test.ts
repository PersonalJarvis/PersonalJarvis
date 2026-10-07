import { expect, it } from "vitest";
import { initialSectionFromSearch, resolveSectionId, SECTION_IDS, SECTION_LABELS } from "@/store/events";
import { NAV_GROUPS } from "@/components/layout/navGroups";

it("retires Run Inspector while preserving old links and the transcription destination", () => {
  expect(SECTION_IDS).not.toContain("run_inspector");
  expect(Object.values(SECTION_LABELS)).not.toContain("Run Inspector");
  expect(JSON.stringify(NAV_GROUPS)).not.toContain("run_inspector");
  expect(JSON.stringify(NAV_GROUPS)).toContain("sessions");
  expect(initialSectionFromSearch("?view=run_inspector&solo=1")).toBe("sessions");
  expect(resolveSectionId("run_inspector")).toBe("sessions");
});
