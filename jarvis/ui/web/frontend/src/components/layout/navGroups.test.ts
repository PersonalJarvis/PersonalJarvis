import { describe, expect, it } from "vitest";
import { MessageSquare } from "lucide-react";

import { NAV_GROUPS, presentNavItem } from "@/components/layout/navGroups";

const chats = NAV_GROUPS.flat().find((i) => i.id === "chats")!;
const agents = NAV_GROUPS.flat().find((i) => i.id === "agents")!;

describe("presentNavItem", () => {
  it("keeps the front page named Chat while voice mode is active", () => {
    const voice = presentNavItem(chats, "voice");
    expect(voice.labelKey).toBe("sidebar.surface_chat");
    expect(voice.icon).toBe(MessageSquare);

    const chat = presentNavItem(chats, "chat");
    expect(chat.labelKey).toBe("sidebar.surface_chat");
    expect(chat.icon).toBe(MessageSquare);
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
