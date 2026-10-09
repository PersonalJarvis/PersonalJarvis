/**
 * Jarvis's own section icons — the glyphs on the sidebar rows, the rail, the
 * deck and the quick switcher.
 *
 * Drawn for this app instead of picked from lucide's catalogue: the stock
 * picks did not say what the section is (sparkles for a dashboard AND for
 * skills, a pair of chat bubbles for a coding IDE, loose shapes for
 * artifacts), and a mixed bag of strokes reads as a template, not a product.
 *
 * One drawing system for all of them, so they sit together on one column:
 * a 24 × 24 grid with the live area inside 3–21, round caps and joins, a
 * 2.5 corner radius on every box, outlines only (no fills) and one idea per
 * glyph. Every icon is shaped like a lucide icon (same props, same ref), so
 * a `NavItem.icon` slot takes it unchanged and `strokeWidth`/`className`
 * keep working at the call sites.
 */
import { forwardRef, type ReactNode } from "react";
import type { LucideIcon, LucideProps } from "lucide-react";

function defineIcon(name: string, glyph: ReactNode): LucideIcon {
  const Icon = forwardRef<SVGSVGElement, LucideProps>(function SectionIcon(
    {
      size = 24,
      color = "currentColor",
      strokeWidth = 1.6,
      absoluteStrokeWidth,
      className,
      children,
      ...rest
    },
    ref,
  ) {
    const width =
      absoluteStrokeWidth ? (Number(strokeWidth) * 24) / Number(size) : strokeWidth;
    return (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        data-icon={name}
        className={className}
        {...rest}
      >
        {glyph}
        {children}
      </svg>
    );
  });
  Icon.displayName = name;
  return Icon as unknown as LucideIcon;
}

// ── Conversation ──────────────────────────────────────────────────────────

/** One speech bubble, tail low on the left. */
export const ChatIcon = defineIcon(
  "ChatIcon",
  <path d="M6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5h-6L7 20.5V17h-.5A2.5 2.5 0 0 1 4 14.5v-8A2.5 2.5 0 0 1 6.5 4Z" />,
);

/** The speech bubble carrying an exclamation mark: something to report. */
export const FeedbackIcon = defineIcon(
  "FeedbackIcon",
  <>
    <path d="M6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5h-6L7 20.5V17h-.5A2.5 2.5 0 0 1 4 14.5v-8A2.5 2.5 0 0 1 6.5 4Z" />
    <path d="M12 7.5v3.5" />
    <path d="M12 14h.01" />
  </>,
);

/** A live waveform: voice as sound, not as hardware. */
export const VoiceIcon = defineIcon(
  "VoiceIcon",
  <>
    <path d="M4 10.5v3" />
    <path d="M8 7.5v9" />
    <path d="M12 4.5v15" />
    <path d="M16 8.5v7" />
    <path d="M20 10.5v3" />
  </>,
);

/**
 * A head in profile speaking: the dictation section, where the user talks and
 * Jarvis writes. Kept apart from the waveform, which marks live voice mode.
 */
export const SpeechIcon = defineIcon(
  "SpeechIcon",
  <>
    <path d="M5.5 20.5v-2.4A6.5 6.5 0 0 1 3.5 13.3V11a6 6 0 0 1 11.3-2.8l1 2.8a.5.5 0 0 1-.47.7H14.5v2.5a2 2 0 0 1-2 2H11v4.3" />
    <path d="M18 9.5a3.6 3.6 0 0 1 0 5" />
    <path d="M20.25 7.5a6.8 6.8 0 0 1 0 9" />
  </>,
);

/** A studio microphone: recordings and their transcripts. */
export const MicrophoneIcon = defineIcon(
  "MicrophoneIcon",
  <>
    <rect x="9" y="3.5" width="6" height="10.5" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
    <path d="M12 17.5v3" />
  </>,
);

// ── Workspace ─────────────────────────────────────────────────────────────

/** Two people, one in front of the other: a team of agents. */
export const AgentsIcon = defineIcon(
  "AgentsIcon",
  <>
    <circle cx="9.5" cy="8.5" r="3.5" />
    <path d="M3.5 19.5a6 6 0 0 1 12 0" />
    <path d="M15.5 5.3a3.5 3.5 0 0 1 0 6.4" />
    <path d="M17.5 14.2a6 6 0 0 1 3 5.3" />
  </>,
);

/** Three tiles and a fourth turned on its corner: something added on. */
export const ExtensionsIcon = defineIcon(
  "ExtensionsIcon",
  <>
    <rect x="4" y="4" width="6.5" height="6.5" rx="1.75" />
    <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.75" />
    <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.75" />
    <path d="m16.75 3.4 3.35 3.35-3.35 3.35-3.35-3.35Z" />
  </>,
);

/** A two-pin plug on its cable. */
export const PluginIcon = defineIcon(
  "PluginIcon",
  <>
    <path d="M9.5 3.5V7" />
    <path d="M14.5 3.5V7" />
    <path d="M6.5 7h11v3.5a5.5 5.5 0 0 1-11 0Z" />
    <path d="M12 16v4.5" />
  </>,
);

/** A bolt: a capability the agent can fire. */
export const SkillIcon = defineIcon(
  "SkillIcon",
  <path d="M13.2 3.5 5.6 13.1a.5.5 0 0 0 .4.8h5.5l-.9 6.6 7.6-9.6a.5.5 0 0 0-.4-.8h-5.5Z" />,
);

/** Two chain links: a protocol that connects tools to the agent. */
export const ConnectorIcon = defineIcon(
  "ConnectorIcon",
  <>
    <path d="M10 13.5a4 4 0 0 0 5.66.5l3-3a4 4 0 0 0-5.66-5.66l-1.2 1.2" />
    <path d="M14 10.5a4 4 0 0 0-5.66-.5l-3 3a4 4 0 0 0 5.66 5.66l1.2-1.2" />
  </>,
);

/** A shopping bag: a store to pick from. */
export const MarketplaceIcon = defineIcon(
  "MarketplaceIcon",
  <>
    <path d="M5.6 8h12.8a1 1 0 0 1 1 1.1l-.9 9.55A1.5 1.5 0 0 1 17 20H7a1.5 1.5 0 0 1-1.5-1.35l-.9-9.55A1 1 0 0 1 5.6 8Z" />
    <path d="M9 10.5V7a3 3 0 0 1 6 0v3.5" />
  </>,
);

/** A card on top of a card: the things a run produced. */
export const ArtifactsIcon = defineIcon(
  "ArtifactsIcon",
  <>
    <rect x="4" y="8" width="12" height="12" rx="2.5" />
    <path d="M8 5.5A2.5 2.5 0 0 1 10.5 3.5H17A3 3 0 0 1 20 6.5V13a2.5 2.5 0 0 1-2 2.45" />
  </>,
);

/** A dashboard layout: four panels of uneven size. */
export const BoardIcon = defineIcon(
  "BoardIcon",
  <>
    <rect x="4" y="4" width="7" height="9" rx="2" />
    <rect x="13" y="4" width="7" height="5" rx="2" />
    <rect x="13" y="11" width="7" height="9" rx="2" />
    <rect x="4" y="15" width="7" height="5" rx="2" />
  </>,
);

/** Linked notes: a small knowledge graph. */
export const WikiIcon = defineIcon(
  "WikiIcon",
  <>
    <circle cx="6.5" cy="6.5" r="2.5" />
    <circle cx="17.5" cy="9" r="2.5" />
    <circle cx="9" cy="17.5" r="2.5" />
    <path d="M8.95 7.05 15.05 8.45" />
    <path d="M7.15 8.9 8.35 15.1" />
    <path d="m15.6 10.65-4.7 5.2" />
  </>,
);

/** An open book. */
export const DocsIcon = defineIcon(
  "DocsIcon",
  <>
    <path d="M12 6.5c-1.6-1.3-3.8-2-6.5-2H4v13.75h1.5c2.7 0 4.9.7 6.5 2 1.6-1.3 3.8-2 6.5-2H20V4.5h-1.5c-2.7 0-4.9.7-6.5 2Z" />
    <path d="M12 6.5v13.75" />
  </>,
);

// ── Tools ─────────────────────────────────────────────────────────────────

/** A pulse trace: what a run did, step by step. */
export const InspectorIcon = defineIcon(
  "InspectorIcon",
  <path d="M3.5 12h3.75l2.5-6.5 4.5 13 2.5-6.5h3.75" />,
);

/** A terminal window with a prompt. */
export const TerminalIcon = defineIcon(
  "TerminalIcon",
  <>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <path d="m7.5 9.5 2.5 2.5-2.5 2.5" />
    <path d="M12.5 14.5h4" />
  </>,
);

/** Angle brackets around a slash: code being written. */
export const CodeIcon = defineIcon(
  "CodeIcon",
  <>
    <path d="M8 7.5 3.5 12 8 16.5" />
    <path d="m16 7.5 4.5 4.5-4.5 4.5" />
    <path d="m13.5 5-3 14" />
  </>,
);

/** A window split into four panes. */
export const PaneGridIcon = defineIcon(
  "PaneGridIcon",
  <>
    <rect x="3.5" y="4" width="17" height="16" rx="2.5" />
    <path d="M12 4v16" />
    <path d="M3.5 12h17" />
  </>,
);

/** A lab flask: the place CLIs are put through their paces. */
export const TestFlaskIcon = defineIcon(
  "TestFlaskIcon",
  <>
    <path d="M9 3.5h6" />
    <path d="M10 3.5v5.25L5.3 16.9A2.4 2.4 0 0 0 7.4 20.5h9.2a2.4 2.4 0 0 0 2.1-3.6L14 8.75V3.5" />
    <path d="M7.5 15h9" />
  </>,
);

/** A folder. */
export const FolderIcon = defineIcon(
  "FolderIcon",
  <path d="M3.5 7A2.5 2.5 0 0 1 6 4.5h3.2a1.5 1.5 0 0 1 1.1.48L11.8 6.5H18A2.5 2.5 0 0 1 20.5 9v8.5A2.5 2.5 0 0 1 18 20H6a2.5 2.5 0 0 1-2.5-2.5Z" />,
);

// ── You ───────────────────────────────────────────────────────────────────

/** A person inside a circle: the user's own profile. */
export const ProfileIcon = defineIcon(
  "ProfileIcon",
  <>
    <circle cx="12" cy="12" r="8.5" />
    <circle cx="12" cy="10" r="3" />
    <path d="M6.6 18.6a6.5 6.5 0 0 1 10.8 0" />
  </>,
);

/** A page with a folded corner and written lines. */
export const InstructionsIcon = defineIcon(
  "InstructionsIcon",
  <>
    <path d="M7 3.5h7l4.5 4.5v11a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z" />
    <path d="M14 3.5V8h4.5" />
    <path d="M8.75 12.5h6.5" />
    <path d="M8.75 16h4.5" />
  </>,
);

/** A head in profile with a spark at its brow: the assistant itself. */
export const AssistantIcon = defineIcon(
  "AssistantIcon",
  <>
    <path d="M9.5 20.5v-2.6a6.5 6.5 0 1 1 8.4-6.2l1.6 2.8h-1.9v2.2a1.8 1.8 0 0 1-1.8 1.8h-1.3v2" />
    <path d="M12.5 6.6l.55 1.35 1.35.55-1.35.55-.55 1.35-.55-1.35-1.35-.55 1.35-.55Z" />
  </>,
);

/** An address book with index tabs. */
export const ContactsIcon = defineIcon(
  "ContactsIcon",
  <>
    <rect x="4.5" y="3.5" width="13.5" height="17" rx="2.5" />
    <circle cx="11.25" cy="10.25" r="2.5" />
    <path d="M7.5 16.75a4 4 0 0 1 7.5 0" />
    <path d="M18 7.5h2" />
    <path d="M18 12h2" />
    <path d="M18 16.5h2" />
  </>,
);

/** A wallet with its pocket: what was spent. */
export const SpendIcon = defineIcon(
  "SpendIcon",
  <>
    <rect x="3.5" y="7" width="17" height="13" rx="2.5" />
    <path d="M6.5 7 15 4.2a1.5 1.5 0 0 1 2 1.4V7" />
    <path d="M20.5 11.5h-3.25a2 2 0 0 0 0 4h3.25" />
  </>,
);

/** An at-sign: the user's handles on social networks. */
export const SocialsIcon = defineIcon(
  "SocialsIcon",
  <>
    <circle cx="12" cy="12" r="3.5" />
    <path d="M15.5 8.5v5a2.5 2.5 0 0 0 5 0V12a8.5 8.5 0 1 0-3.4 6.8" />
  </>,
);

// ── System ────────────────────────────────────────────────────────────────

/** A cloud: the machines that live somewhere other than this one. */
export const ComputersIcon = defineIcon(
  "ComputersIcon",
  <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />,
);

/** A gear: the general settings. */
export const SettingsIcon = defineIcon(
  "SettingsIcon",
  <>
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </>,
);

/** A keyboard: keys and a space bar. */
export const ShortcutsIcon = defineIcon(
  "ShortcutsIcon",
  <>
    <rect x="2.5" y="6" width="19" height="12" rx="2.5" />
    <path d="M6.5 10h.01" />
    <path d="M10 10h.01" />
    <path d="M14 10h.01" />
    <path d="M17.5 10h.01" />
    <path d="M8 14h8" />
  </>,
);

/** A viewfinder's four corners around a point: a captured screen. */
export const CaptureIcon = defineIcon(
  "CaptureIcon",
  <>
    <path d="M4 8.5v-2A2.5 2.5 0 0 1 6.5 4h2" />
    <path d="M15.5 4h2A2.5 2.5 0 0 1 20 6.5v2" />
    <path d="M20 15.5v2a2.5 2.5 0 0 1-2.5 2.5h-2" />
    <path d="M8.5 20h-2A2.5 2.5 0 0 1 4 17.5v-2" />
    <circle cx="12" cy="12" r="2.75" />
  </>,
);

/** A paw print. */
export const PetsIcon = defineIcon(
  "PetsIcon",
  <>
    <path d="M12 12.5c-2.8 0-5 2.6-5 4.6 0 1.3 1 2.4 2.4 2.4.9 0 1.6-.4 2.6-.4s1.7.4 2.6.4c1.4 0 2.4-1.1 2.4-2.4 0-2-2.2-4.6-5-4.6Z" />
    <ellipse cx="5.6" cy="10.4" rx="1.6" ry="2" transform="rotate(-20 5.6 10.4)" />
    <ellipse cx="9.3" cy="6.2" rx="1.7" ry="2.2" transform="rotate(-6 9.3 6.2)" />
    <ellipse cx="14.7" cy="6.2" rx="1.7" ry="2.2" transform="rotate(6 14.7 6.2)" />
    <ellipse cx="18.4" cy="10.4" rx="1.6" ry="2" transform="rotate(20 18.4 10.4)" />
  </>,
);

/** A shield with a check: what Jarvis may do on its own. */
export const PermissionsIcon = defineIcon(
  "PermissionsIcon",
  <>
    <path d="M12 3.5 5 6v5.5c0 4.3 2.9 7.6 7 9 4.1-1.4 7-4.7 7-9V6Z" />
    <path d="m9 12 2.1 2.1L15 10.2" />
  </>,
);

/** A telephone handset. */
export const PhoneIcon = defineIcon(
  "PhoneIcon",
  <path d="M8.6 4.3 6.4 3.9a1.5 1.5 0 0 0-1.6.9c-.9 2-.8 4.7 1 7.8a17 17 0 0 0 5.6 5.6c3.1 1.8 5.8 1.9 7.8 1a1.5 1.5 0 0 0 .9-1.6l-.4-2.2a1.5 1.5 0 0 0-1-1.2l-2.4-.8a1.5 1.5 0 0 0-1.6.4l-.9 1a11 11 0 0 1-4.6-4.6l1-.9a1.5 1.5 0 0 0 .4-1.6l-.8-2.4a1.5 1.5 0 0 0-1.2-1Z" />,
);

/** A globe: language and locale. */
export const LanguageIcon = defineIcon(
  "LanguageIcon",
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17" />
    <path d="M12 3.5c2.3 2.3 3.5 5.1 3.5 8.5s-1.2 6.2-3.5 8.5c-2.3-2.3-3.5-5.1-3.5-8.5S9.7 5.8 12 3.5Z" />
  </>,
);

/** A bound book with a letter on its cover: the custom vocabulary. */
export const DictionaryIcon = defineIcon(
  "DictionaryIcon",
  <>
    <path d="M5.5 18.75V5.5a2 2 0 0 1 2-2h11V17h-11a1.75 1.75 0 0 0 0 3.5h11" />
    <path d="m10 13.5 2-5.5 2 5.5" />
    <path d="M10.75 11.6h2.5" />
  </>,
);
