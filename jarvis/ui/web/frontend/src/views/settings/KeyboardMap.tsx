import { useMemo } from "react";
import { codeToKeyToken, codeToModifierToken } from "@/hooks/useHotkey";
import { useT } from "@/i18n";
import {
  ARROW_ROWS,
  NAV_ROWS,
  mainRows,
  type KeyCap,
  type KeyboardPlatform,
  type KeyRow,
} from "./keyboardLayout";

/** The bindable jarvis token for a cap, or null when it cannot be bound. */
function capToken(cap: KeyCap): string | null {
  if (cap.dead) return null;
  return codeToModifierToken(cap.code) ?? codeToKeyToken(cap.code);
}

interface KeyboardMapProps {
  /** Physical ``event.code``s currently held — drives the LIVE highlight. */
  pressedCodes: Set<string>;
  /** Tokens that make up the combo being built — drives the selected ring. */
  selectedTokens: Set<string>;
  /** token → label of ANOTHER action already bound to it (the "used" marker). */
  boundTokens: Record<string, string>;
  platform: KeyboardPlatform;
  /** Toggle a bindable key in/out of the combo (click-to-assign). */
  onToggleToken: (token: string) => void;
}

function Key({
  cap,
  pressedCodes,
  selectedTokens,
  boundTokens,
  onToggleToken,
}: {
  cap: KeyCap;
  pressedCodes: Set<string>;
  selectedTokens: Set<string>;
  boundTokens: Record<string, string>;
  onToggleToken: (token: string) => void;
}) {
  const t = useT();
  const token = capToken(cap);
  // The Windows key is reserved by the OS (the backend rejects every win+…
  // combo), so offering it as clickable only manufactures a doomed save —
  // render it like a dead key and say why in the tooltip.
  const reserved = token === "win";
  const bindable = token !== null && !reserved;
  const pressed = pressedCodes.has(cap.code);
  const selected = bindable && selectedTokens.has(token);
  const boundLabel = bindable ? boundTokens[token] : undefined;

  // Visual precedence: a live press always wins (you must SEE the key you hit),
  // then the in-combo selection, then the "used by another action" marker.
  const cls = pressed
    ? "border-primary bg-primary text-primary-foreground"
    : selected
      ? "border-primary bg-primary/20 text-primary"
      : boundLabel
        ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
        : bindable
          ? // Hover stays clearly weaker than the "pressed" fill — a full
            // bg-accent hover read as "this key is pressed" in live testing.
            "border-input bg-background text-foreground hover:border-primary/60 hover:bg-accent/40"
          : "border-transparent bg-muted/30 text-muted-foreground/50";

  return (
    <button
      type="button"
      data-testid={`key-${cap.code}`}
      aria-label={cap.code}
      aria-pressed={selected}
      title={
        reserved
          ? `${cap.label} — ${t("settings_view.keybinds.keyboard.reserved")}`
          : boundLabel
            ? `${cap.label} — ${boundLabel}`
            : cap.label
      }
      disabled={!bindable}
      onClick={bindable ? () => onToggleToken(token) : undefined}
      style={{ flexGrow: cap.width ?? 1, flexBasis: 0 }}
      className={`relative flex h-7 min-w-0 items-center justify-center rounded border px-0.5 font-mono text-[10px] leading-none transition-colors ${cls} ${
        bindable ? "cursor-pointer" : "cursor-default"
      }`}
    >
      <span className="truncate">{cap.label}</span>
      {boundLabel && !pressed && (
        <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-amber-400" />
      )}
    </button>
  );
}

function Row({
  row,
  ...rest
}: {
  row: KeyRow;
} & Omit<KeyboardMapProps, "platform">) {
  return (
    <div className="flex gap-1">
      {row.map((cap) => (
        <Key key={cap.code} cap={cap} {...rest} />
      ))}
    </div>
  );
}

/**
 * A live on-screen keyboard for the keybind picker. Keys light up as the user
 * presses them (so you can SEE that F5+F6 actually registered), keys already
 * bound to another action are flagged so you can pick a free one, and clicking a
 * key toggles it into the combo (click-to-assign — for keys that are awkward to
 * hold). Platform-aware modifier labels (⌘/⌥/⌃ on Mac, Ctrl/Win/Alt on PC).
 */
export function KeyboardMap({
  pressedCodes,
  selectedTokens,
  boundTokens,
  platform,
  onToggleToken,
}: KeyboardMapProps) {
  const t = useT();
  const keyProps = { pressedCodes, selectedTokens, boundTokens, onToggleToken };
  const rows = useMemo(() => mainRows(platform), [platform]);

  return (
    <div className="mt-3 rounded-md border border-border/60 bg-background/40 p-2">
      <div className="flex flex-wrap items-start gap-3">
        {/* Main alpha block */}
        <div className="flex min-w-[280px] flex-1 flex-col gap-1">
          {rows.map((row, i) => (
            <Row key={i} row={row} {...keyProps} />
          ))}
        </div>

        {/* Nav cluster + inverted-T arrows */}
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1">
            {NAV_ROWS.map((row, i) => (
              <div key={i} className="flex gap-1">
                {row.map((cap) => (
                  <div key={cap.code} className="w-10">
                    <Key cap={cap} {...keyProps} />
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="flex flex-col gap-1">
            {ARROW_ROWS.map((row, i) => (
              <div key={i} className="flex gap-1">
                {row.map((cap, j) =>
                  cap ? (
                    <div key={cap.code} className="w-10">
                      <Key cap={cap} {...keyProps} />
                    </div>
                  ) : (
                    <div key={`pad-${j}`} className="w-10" />
                  ),
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-primary" />
          {t("settings_view.keybinds.keyboard.legend_pressed")}
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm border border-primary bg-primary/20" />
          {t("settings_view.keybinds.keyboard.legend_selected")}
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-amber-400" />
          {t("settings_view.keybinds.keyboard.legend_bound")}
        </span>
        <span className="ml-auto">
          {t("settings_view.keybinds.keyboard.hint")}
        </span>
      </div>
    </div>
  );
}
