import { useState } from "react";
import { ChatsView } from "@/views/ChatsView";
import { MissionDeckView, SurfaceSwitch } from "@/views/MissionDeckView";
import { readDeckMode, type DeckMode } from "@/lib/deckMode";

/**
 * The "chats" section: the mission deck, with the classic chat view one click
 * behind it.
 *
 * Why a switch instead of a replacement: the classic view still owns things the
 * deck does not do — the conversation list, resuming a stored thread, deleting
 * one, "speak in this conversation". Shipping the deck as a hard cutover would
 * take those away, so both surfaces stay reachable and the choice is
 * remembered. When the deck grows those features the switch can go; until then
 * it is what makes this a safe change rather than a lossy one.
 *
 * The mode lives here rather than in the event store on purpose: nothing on the
 * backend cares which surface is on screen, and a preference that never leaves
 * the browser has no business on the event bus.
 */
export function ChatsSurface() {
  const [mode, setMode] = useState<DeckMode>(() => readDeckMode());
  const accessory = <SurfaceSwitch mode={mode} onChange={setMode} />;

  return mode === "classic" ? (
    <ChatsView headerAccessory={accessory} />
  ) : (
    <MissionDeckView headerAccessory={accessory} />
  );
}
