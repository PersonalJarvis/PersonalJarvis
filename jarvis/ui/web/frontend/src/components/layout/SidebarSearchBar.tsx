/**
 * The search field at the top of the sidebar, in the spot the assistant's
 * name row used to take. You type straight into it and the results drop down
 * under it (`InlineQuickSwitch`); the switcher window in the middle of the
 * screen is the chord's, not this field's.
 *
 * The live field is a lazy chunk (its result list carries every locale), so
 * this draws a plain field that looks the same until it is needed. It is
 * fetched once the app has settled, or the moment the field is pointed at or
 * focused — whichever comes first — and whatever was typed meanwhile is
 * handed over with the caret still at the end.
 *
 * It reads "Search": the assistant's name beside the chord hint and the voice
 * dot was cut to "Search Ge…" at the default width. The accessible name still
 * says what is searched ("Search George"). The voice status dot (plus the
 * dev-instance tag) sits at its right edge.
 */
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import { useT } from "@/i18n";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import { chordCaps } from "@/lib/quickSwitchChord";

const loadInline = () =>
  import("@/components/layout/InlineQuickSwitch").then((m) => ({ default: m.InlineQuickSwitch }));
const InlineQuickSwitch = lazy(loadInline);

/** How long after mount the live field is fetched without being asked for. */
const IDLE_LOAD_MS = 2500;

export function SidebarSearchBar({
  assistantName,
  status,
}: {
  assistantName: string;
  /** The voice dot / spinner and the dev tag, drawn at the field's right edge. */
  status?: ReactNode;
}) {
  const t = useT();
  const combo = useQuickSwitchSettings((s) => s.combo);
  const caps = combo ? chordCaps(combo) : [];
  const accessibleName = t("quick_switch.sidebar_placeholder").replace("{name}", assistantName);
  const placeholder = t("quick_switch.sidebar_search");
  const [armed, setArmed] = useState(false);
  const [focused, setFocused] = useState(false);
  const [text, setText] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setArmed(true), IDLE_LOAD_MS);
    return () => window.clearTimeout(timer);
  }, []);

  const plain = (
    <div className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg border border-border bg-input px-2.5 text-sm">
      <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <input
        type="text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onFocus={() => {
          setFocused(true);
          setArmed(true);
        }}
        onPointerEnter={() => setArmed(true)}
        placeholder={placeholder}
        aria-label={accessibleName}
        title={accessibleName}
        data-testid="sidebar-search"
        autoComplete="off"
        spellCheck={false}
        className="h-full min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
      />
      {status}
      {caps.length > 0 && (
        <span className="hidden shrink-0 items-center gap-0.5 sm:inline-flex" aria-hidden>
          {caps.map((cap) => (
            <kbd
              key={cap}
              className="rounded border border-border bg-background px-1 font-sans text-[10px] leading-4 text-muted-foreground"
            >
              {cap}
            </kbd>
          ))}
        </span>
      )}
    </div>
  );

  return (
    <div role="search" className="flex min-w-0 flex-1">
      {armed ? (
        <Suspense fallback={plain}>
          <InlineQuickSwitch
            initialValue={text}
            autoFocus={focused}
            accessibleName={accessibleName}
            placeholder={placeholder}
            status={status}
            caps={caps}
            onValueChange={setText}
          />
        </Suspense>
      ) : (
        plain
      )}
    </div>
  );
}
