import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  Bug,
  ExternalLink,
  HelpCircle,
  ImagePlus,
  Lightbulb,
  MessageSquareWarning,
  ThumbsUp,
  X,
} from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import {
  fetchFeedbackBoard,
  fetchFeedbackStatus,
  submitFeedback,
  type BoardEntry,
  type FeedbackBoard,
  type FeedbackChannelStatus,
  type FeedbackType,
} from "@/views/feedback/api";

/**
 * The Feedback section — one place for both report kinds, GitHub as the spine.
 *
 * A bug and a feature request are the same act here: pick the kind, write two
 * fields, and the button opens the matching GitHub issue form with everything
 * already filled in. The report type selects the form
 * (`?template=bug_report.yml` / `feature_request.yml`), and THAT is what puts
 * the `bug` / `enhancement` label, the title prefix and the structure on the
 * issue. Opening `/issues/new` without a template — what this view used to do
 * — lands a blank, unlabelled issue that has to be triaged by hand.
 *
 * Three things this deliberately gets right:
 *
 *   - **GitHub is the normal path, not a fallback.** The direct-dispatch
 *     webhook is the maintainer's own operator credential, so `configured` is
 *     false on every download. Presenting GitHub as what happens "because
 *     this install has no channel" told 100 % of users their copy was
 *     defective. When the webhook DOES exist it adds a second button, because
 *     that path is the only one that works without a GitHub account.
 *   - **The login question is answered up front.** Filing needs a free GitHub
 *     account. If the user is signed out, GitHub asks and then returns them to
 *     the prefilled report — nothing is retyped. Saying so beats a surprise
 *     login wall.
 *   - **A long report cannot be lost.** A prefilled issue travels in the URL,
 *     and browsers/GitHub stop accepting one somewhere past ~8 KB. Anything
 *     over the budget is trimmed for the URL and the FULL text is put on the
 *     clipboard, with the user told to paste it.
 *
 * The board below reads open issues from the public GitHub API (no token, no
 * login) so someone sees their idea is already tracked instead of filing the
 * duplicate. Empty lists are not rendered: on a young tracker "0 requests"
 * reads as "nobody cares", which is both discouraging and untrue.
 *
 * All external links go through {@link openExternalUrl} rather than a bare
 * `<a target="_blank">`, because the desktop shell (WebView2) silently drops
 * `target="_blank"` / `window.open` — the new tab never appears.
 */

// Discord's Community onboarding redirects invite links to #welcome even when
// the invite was created for a different channel. Existing members therefore
// need the canonical channel URL for a guaranteed direct hop to the bug forum.
const DISCORD_BUG_FORUM_URL =
  "https://discord.com/channels/1511102439066177656/1521522036709789736";

// Public, never-expiring server invite for people who are not members yet.
const DISCORD_INVITE_URL = "https://discord.gg/x7USduHxbc";

// Mirror of the backend's decoded-size cap (feedback_routes.py) so an
// oversized screenshot is rejected before a pointless upload round-trip.
const SCREENSHOT_MAX_BYTES = 8 * 1024 * 1024;

// Matches the backend's Pydantic field constraints.
const TITLE_MAX = 200;
const FIELD_MAX = 4000;

/**
 * Budget for a prefilled issue URL. GitHub answers a request whose URL grows
 * past roughly 8 KB with an error instead of the form, and percent-encoding
 * can triple a character, so a 4 000-character field alone can blow it. Well
 * under the ceiling on purpose — the cost of being wrong is a dead link.
 */
const URL_MAX_CHARS = 6000;

type ResultKind =
  | "sent"
  | "error"
  | "not_configured"
  | "github_opened"
  | "github_opened_trimmed";

const INPUT_CLS =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 " +
  "focus-visible:ring-primary/40";

/** The official Discord brand mark (inline so it renders without an asset). */
function DiscordIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M20.317 4.369a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.249a18.27 18.27 0 0 0-5.487 0 12.6 12.6 0 0 0-.617-1.25.077.077 0 0 0-.079-.036A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.1 13.1 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.127 12.3 12.3 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.331c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
    </svg>
  );
}

/**
 * The composed issue URL, plus whether anything had to be trimmed to fit.
 * `trimmed` is not cosmetic — it is the signal that the user's full text now
 * lives only on their clipboard.
 */
interface ComposedIssue {
  url: string;
  trimmed: boolean;
}

/**
 * Compose a prefilled issue URL for one of the repository's issue forms.
 *
 * Issue-form prefill works by FIELD ID: `?template=<file>&<field-id>=<value>`.
 * GitHub ignores an unknown id silently, and rejects a dropdown value that is
 * not an exact option — which is why the OS comes from the backend's
 * `os_choice` rather than the free-form platform string.
 *
 * The title prefix is applied here: passing `title` overrides the form's own
 * default (`"[Bug]: "`), so without this every report would lose it.
 *
 * If the result exceeds {@link URL_MAX_CHARS} the two long fields are trimmed,
 * longest first, until it fits. The caller is expected to hand the untrimmed
 * text to the user another way.
 */
function buildIssueUrl(
  channel: FeedbackChannelStatus,
  type: FeedbackType,
  title: string,
  primary: string,
  secondary: string,
): ComposedIssue | null {
  const template = channel.templates[type];
  if (!template) return null;

  const prefix = type === "bug" ? "[Bug]: " : "[Feature]: ";
  const base = `${channel.github_url}/new`;

  const compose = (a: string, b: string): string => {
    const params = new URLSearchParams({ template });
    params.set("title", `${prefix}${title.trim()}`);
    if (type === "bug") {
      params.set("what-happened", a);
      if (b) params.set("steps", b);
      // Both prefilled from the running install, so nobody has to look them up.
      params.set("os", channel.context.os_choice);
      params.set("python", channel.context.python);
    } else {
      params.set("problem", a);
      if (b) params.set("solution", b);
    }
    return `${base}?${params.toString()}`;
  };

  let a = primary.trim();
  let b = secondary.trim();
  let url = compose(a, b);
  if (url.length <= URL_MAX_CHARS) return { url, trimmed: false };

  // Shave the longer field repeatedly rather than truncating one to nothing:
  // a report keeps more of its meaning when both halves survive.
  const ELLIPSIS = "\n\n[…]";
  while (url.length > URL_MAX_CHARS && (a.length > 40 || b.length > 40)) {
    const step = Math.max(50, Math.ceil((url.length - URL_MAX_CHARS) / 6));
    if (a.length >= b.length) a = a.slice(0, Math.max(40, a.length - step));
    else b = b.slice(0, Math.max(40, b.length - step));
    url = compose(a ? a + ELLIPSIS : a, b ? b + ELLIPSIS : b);
  }
  return { url, trimmed: true };
}

/** The plain-text report, used for the clipboard when the URL had to trim. */
function composePlainText(
  type: FeedbackType,
  title: string,
  primary: string,
  secondary: string,
  channel: FeedbackChannelStatus | null,
): string {
  const lines = [title.trim(), "", primary.trim()];
  if (secondary.trim()) lines.push("", secondary.trim());
  if (channel) {
    lines.push(
      "",
      "---",
      `- Type: ${type}`,
      `- App version: ${channel.context.app_version}`,
      `- OS: ${channel.context.os}`,
      `- Python: ${channel.context.python}`,
    );
  }
  return lines.join("\n");
}

/** One board row: an open issue with its 👍 count. */
function BoardRow({ entry, label }: { entry: BoardEntry; label: string }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => void openExternalUrl(entry.url)}
        className="group flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition hover:bg-muted/50"
      >
        <span
          className="flex shrink-0 items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground"
          aria-label={`${entry.upvotes} ${label}`}
        >
          <ThumbsUp className="h-3 w-3" aria-hidden="true" />
          {entry.upvotes}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm text-foreground">{entry.title}</span>
        <ArrowUpRight
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100"
          aria-hidden="true"
        />
      </button>
    </li>
  );
}

export function FeedbackView() {
  const t = useT();

  // null = probe pending or failed. The GitHub path needs the probe (it
  // carries the template names), so the form stays disabled until it lands.
  const [channel, setChannel] = useState<FeedbackChannelStatus | null>(null);
  const [board, setBoard] = useState<FeedbackBoard | null>(null);

  const [type, setType] = useState<FeedbackType>("bug");
  const [title, setTitle] = useState("");
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [screenshotTooLarge, setScreenshotTooLarge] = useState(false);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<ResultKind | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFeedbackStatus()
      .then((status) => {
        if (!cancelled) setChannel(status);
      })
      .catch(() => {
        /* probe unreachable — the form reports it on submit */
      });
    fetchFeedbackBoard()
      .then((data) => {
        if (!cancelled) setBoard(data);
      })
      .catch(() => {
        /* board is optional context, never an error the user must see */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The operator webhook exists → offer direct dispatch as a second path. It
  // is the only one that works without a GitHub account, so it is worth its
  // own button rather than replacing the primary one.
  const canDispatchDirect = channel?.configured === true;
  const isQuestion = type === "question";

  const onPickScreenshot = useCallback((ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    // Allow re-picking the same file after a remove.
    ev.target.value = "";
    if (!file) return;
    if (file.size > SCREENSHOT_MAX_BYTES) {
      setScreenshotTooLarge(true);
      return;
    }
    setScreenshotTooLarge(false);
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") setScreenshot(reader.result);
    };
    reader.readAsDataURL(file);
  }, []);

  const canSubmit =
    title.trim().length > 0 && primary.trim().length > 0 && !sending && channel !== null;

  const resetForm = useCallback(() => {
    setTitle("");
    setPrimary("");
    setSecondary("");
    setScreenshot(null);
  }, []);

  /** Primary path: open the matching issue form with everything prefilled. */
  const onOpenGithub = useCallback(async () => {
    if (!canSubmit || !channel) return;
    setResult(null);
    const composed = buildIssueUrl(channel, type, title, primary, secondary);
    if (!composed) return;

    if (composed.trimmed) {
      // The URL could not carry the whole report. Put the full text on the
      // clipboard BEFORE navigating, so nothing the user wrote is lost.
      try {
        await navigator.clipboard.writeText(
          composePlainText(type, title, primary, secondary, channel),
        );
      } catch {
        // No clipboard permission — the trimmed issue still opens, and the
        // text remains in the form fields behind it.
      }
    }
    await openExternalUrl(composed.url);
    setResult(composed.trimmed ? "github_opened_trimmed" : "github_opened");
  }, [canSubmit, channel, primary, secondary, title, type]);

  /** Secondary path (operator installs only): straight to the webhook. */
  const onSendDirect = useCallback(async () => {
    if (!canSubmit) return;
    setResult(null);
    setSending(true);
    try {
      const description = secondary.trim()
        ? `${primary.trim()}\n\n${secondary.trim()}`
        : primary.trim();
      const res = await submitFeedback({
        type,
        title: title.trim(),
        description,
        screenshot,
      });
      if (res.status === "sent") {
        setResult("sent");
        resetForm();
      } else if (res.status === "not_configured") {
        // The probe said "configured" but the dispatch disagreed (webhook
        // removed meanwhile). Drop the direct button; GitHub still works.
        setResult("not_configured");
        if (channel) setChannel({ ...channel, configured: false });
      } else {
        setResult("error");
      }
    } catch {
      setResult("error");
    } finally {
      setSending(false);
    }
  }, [canSubmit, channel, primary, resetForm, screenshot, secondary, title, type]);

  const typeOptions: Array<{ value: FeedbackType; label: string; icon: typeof Bug }> = [
    { value: "bug", label: t("feedback.type_bug"), icon: Bug },
    { value: "idea", label: t("feedback.type_idea"), icon: Lightbulb },
    { value: "question", label: t("feedback.type_question"), icon: HelpCircle },
  ];

  const fieldLabels = useMemo(() => {
    if (type === "bug") {
      return {
        primary: t("feedback.bug_what_label"),
        primaryPh: t("feedback.bug_what_placeholder"),
        secondary: t("feedback.bug_steps_label"),
        secondaryPh: t("feedback.bug_steps_placeholder"),
      };
    }
    return {
      primary: t("feedback.idea_problem_label"),
      primaryPh: t("feedback.idea_problem_placeholder"),
      secondary: t("feedback.idea_solution_label"),
      secondaryPh: t("feedback.idea_solution_placeholder"),
    };
  }, [t, type]);

  const resultBanner: Record<ResultKind, { text: string; cls: string }> = {
    sent: {
      text: t("feedback.result_sent"),
      cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    },
    error: {
      text: t("feedback.result_error"),
      cls: "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400",
    },
    not_configured: {
      text: t("feedback.result_not_configured"),
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    },
    github_opened: {
      text: t("feedback.result_github_opened"),
      cls: "border-border bg-muted/30 text-muted-foreground",
    },
    github_opened_trimmed: {
      text: t("feedback.result_github_trimmed"),
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    },
  };

  const ideas = board?.available ? board.ideas : [];
  const bugs = board?.available ? board.bugs : [];
  const hasBoard = ideas.length > 0 || bugs.length > 0;

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <ViewHeader
        icon={<MessageSquareWarning className="h-4 w-4 text-primary" />}
        title={t("nav.feedback")}
        subtitle={t("feedback.subtitle")}
      />

      <div className="flex flex-1 justify-center p-6">
        <div className="w-full max-w-md space-y-6 pb-8">
          {/* What kind of report — this also picks the GitHub issue form. */}
          <div>
            <span
              id="feedback-type-label"
              className="mb-1.5 block text-xs font-medium text-muted-foreground"
            >
              {t("feedback.type_label")}
            </span>
            <div
              role="radiogroup"
              aria-labelledby="feedback-type-label"
              className="grid grid-cols-3 gap-2"
            >
              {typeOptions.map(({ value, label, icon: Icon }) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={type === value}
                  onClick={() => {
                    setType(value);
                    setResult(null);
                  }}
                  className={
                    "flex items-center justify-center gap-1.5 rounded-lg border px-2 py-2 text-sm transition " +
                    (type === value
                      ? "border-primary/60 bg-primary/15 font-medium text-foreground"
                      : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground")
                  }
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {isQuestion ? (
            /* A question is not a tracker item — it belongs where people
               answer, so this branch has no form at all. */
            <div className="space-y-3 rounded-2xl border border-[#5865F2]/40 bg-[#5865F2]/10 p-6 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-[#5865F2]/20">
                <DiscordIcon className="h-7 w-7 text-[#5865F2]" />
              </div>
              <h2 className="text-base font-semibold text-foreground">
                {t("feedback.question_title")}
              </h2>
              <p className="pb-1 text-sm text-muted-foreground">
                {t("feedback.question_hint")}
              </p>
              <button
                type="button"
                onClick={() => void openExternalUrl(DISCORD_INVITE_URL)}
                className="w-full rounded-lg bg-[#5865F2] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#4752c4]"
              >
                {t("feedback.question_discord_button")}
              </button>
            </div>
          ) : (
            <form
              onSubmit={(ev) => {
                ev.preventDefault();
                void onOpenGithub();
              }}
              className="space-y-4 rounded-2xl border border-border bg-card/60 p-6"
            >
              <div>
                <label
                  htmlFor="feedback-title"
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                >
                  {t("feedback.title_label")}
                </label>
                <input
                  id="feedback-title"
                  type="text"
                  value={title}
                  maxLength={TITLE_MAX}
                  onChange={(ev) => setTitle(ev.target.value)}
                  placeholder={t("feedback.title_placeholder")}
                  className={INPUT_CLS}
                />
              </div>

              <div>
                <label
                  htmlFor="feedback-primary"
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                >
                  {fieldLabels.primary}
                </label>
                <textarea
                  id="feedback-primary"
                  rows={4}
                  value={primary}
                  maxLength={FIELD_MAX}
                  onChange={(ev) => setPrimary(ev.target.value)}
                  placeholder={fieldLabels.primaryPh}
                  className={`${INPUT_CLS} resize-y`}
                />
              </div>

              <div>
                <label
                  htmlFor="feedback-secondary"
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                >
                  {fieldLabels.secondary}
                </label>
                <textarea
                  id="feedback-secondary"
                  rows={3}
                  value={secondary}
                  maxLength={FIELD_MAX}
                  onChange={(ev) => setSecondary(ev.target.value)}
                  placeholder={fieldLabels.secondaryPh}
                  className={`${INPUT_CLS} resize-y`}
                />
              </div>

              {/* A GitHub issue URL cannot carry an image, so the screenshot
                  picker only appears when the direct channel exists. */}
              {canDispatchDirect ? (
                <div>
                  <span className="mb-1.5 block text-xs font-medium text-muted-foreground">
                    {t("feedback.screenshot_label")}
                  </span>
                  {screenshot ? (
                    <div className="relative overflow-hidden rounded-lg border border-border">
                      <img
                        src={screenshot}
                        alt={t("feedback.screenshot_preview_alt")}
                        className="max-h-48 w-full bg-background object-contain"
                      />
                      <button
                        type="button"
                        onClick={() => setScreenshot(null)}
                        aria-label={t("feedback.screenshot_remove")}
                        className="absolute right-2 top-2 rounded-md bg-background/80 p-1 text-muted-foreground transition hover:text-foreground"
                      >
                        <X className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => fileRef.current?.click()}
                      className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
                    >
                      <ImagePlus className="h-4 w-4" aria-hidden="true" />
                      {t("feedback.screenshot_pick")}
                    </button>
                  )}
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={onPickScreenshot}
                  />
                  {screenshotTooLarge ? (
                    <p className="mt-1.5 text-xs text-red-500" role="alert">
                      {t("feedback.screenshot_too_large")}
                    </p>
                  ) : null}
                </div>
              ) : null}

              {result ? (
                <p
                  role="status"
                  className={`rounded-lg border px-3 py-2 text-sm ${resultBanner[result].cls}`}
                >
                  {resultBanner[result].text}
                </p>
              ) : null}

              <div className="space-y-2">
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                  {type === "bug"
                    ? t("feedback.github_submit_bug")
                    : t("feedback.github_submit_idea")}
                </button>

                {canDispatchDirect ? (
                  <button
                    type="button"
                    onClick={() => void onSendDirect()}
                    disabled={!canSubmit}
                    className="w-full rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {sending ? t("feedback.sending") : t("feedback.send_without_github")}
                  </button>
                ) : null}
              </div>

              {/* The account requirement, said before they hit the wall. */}
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("feedback.github_login_note")}
              </p>
            </form>
          )}

          {/* What is already tracked — public read, no login required. */}
          {hasBoard ? (
            <div className="space-y-4 rounded-2xl border border-border bg-card/40 p-5">
              {ideas.length > 0 ? (
                <div>
                  <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("feedback.board_ideas_title")}
                  </h3>
                  <ul>
                    {ideas.map((entry) => (
                      <BoardRow
                        key={entry.number}
                        entry={entry}
                        label={t("feedback.board_upvotes")}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}

              {bugs.length > 0 ? (
                <div>
                  <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t("feedback.board_bugs_title")}
                  </h3>
                  <ul>
                    {bugs.map((entry) => (
                      <BoardRow
                        key={entry.number}
                        entry={entry}
                        label={t("feedback.board_upvotes")}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}

              <button
                type="button"
                onClick={() => void openExternalUrl(channel?.github_url ?? "")}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground"
              >
                {t("feedback.board_see_all")}
                <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          ) : null}

          {/* The community, kept as a quiet footer rather than the headline:
              it is where conversation happens, not where reports are filed. */}
          {!isQuestion ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card/30 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <DiscordIcon className="h-5 w-5 shrink-0 text-[#5865F2]" />
                <span className="truncate text-sm text-muted-foreground">
                  {t("feedback.community_hint")}
                </span>
              </div>
              <button
                type="button"
                onClick={() => void openExternalUrl(DISCORD_BUG_FORUM_URL)}
                className="shrink-0 text-sm font-medium text-[#5865F2] transition hover:underline"
              >
                {t("feedback.community_open")}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
