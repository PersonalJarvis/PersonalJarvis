import { describe, expect, it } from "vitest";
import { MessageSquare } from "lucide-react";

import { NAV_GROUPS, presentNavItem } from "@/components/layout/navGroups";

const chats = NAV_GROUPS.flat().find((i) => i.id === "chats")!;
const agents = NAV_GROUPS.flat().find((i) => i.id === "agents")!;

describe("presentNavItem", () => {
  it("names the front page \"Chat\" in either mode — voice mode is a state of the chat", () => {
    for (const surface of ["voice", "chat"] as const) {
      const row = presentNavItem(chats, surface);
      expect(row.labelKey).toBe("sidebar.surface_chat");
      expect(row.icon).toBe(MessageSquare);
    }
  });

  it("keeps the section id so the row still lands on the front page", () => {
    expect(presentNavItem(chats, "voice").id).toBe("chats");
    expect(presentNavItem(chats, "chat").id).toBe("chats");
  });

  it("passes every other row through untouched", () => {
    expect(presentNavItem(agents, "voice")).toBe(agents);
    expect(presentNavItem(agents, "chat")).toBe(agents);
  });
});
