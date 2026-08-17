import { ChatsView } from "@/views/ChatsView";
import { MissionDeckView, SurfaceSwitch } from "@/views/MissionDeckView";
import { useDeckStore } from "@/store/deck";

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
 * The mode lives in the deck store rather than here: the app shell reads it to
 * hide the sidebar while the deck (which brings its own dock) is on screen.
 */
export function ChatsSurface() {
  const mode = useDeckStore((s) => s.mode);
  const setMode = useDeckStore((s) => s.setMode);
  const accessory = <SurfaceSwitch mode={mode} onChange={setMode} />;

  return mode === "classic" ? (
    <ChatsView headerAccessory={accessory} />
  ) : (
    <MissionDeckView headerAccessory={accessory} />
  );
}
