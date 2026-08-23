import { describe, expect, it } from "vitest";
import { MessageSquare, Mic } from "lucide-react";

import { NAV_GROUPS, presentNavItem } from "@/components/layout/navGroups";

const chats = NAV_GROUPS.flat().find((i) => i.id === "chats")!;
const agents = NAV_GROUPS.flat().find((i) => i.id === "agents")!;

describe("presentNavItem", () => {
  it("names the front page after the face the Voice | Chat switch picked", () => {
    const voice = presentNavItem(chats, "voice");
    expect(voice.labelKey).toBe("sidebar.surface_voice");
    expect(voice.icon).toBe(Mic);

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
