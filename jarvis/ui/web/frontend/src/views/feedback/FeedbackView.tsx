import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bug,
  ExternalLink,
  HelpCircle,
  ImagePlus,
  Lightbulb,
  MessageSquareWarning,
  X,
} from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import {
  fetchFeedbackStatus,
  submitFeedback,
  type FeedbackChannelStatus,
  type FeedbackType,
} from "@/views/feedback/api";

/**
 * The Feedback section — Discord stays the primary channel (the forum button
 * plus a permanent invite), and below it an in-app form covers everyone the
 * Discord path loses. The form probes GET /api/feedback/status first and
 * presents the ONE path this install really has:
 *
 *   - webhook configured (operator install) → direct dispatch with optional
 *     screenshot, enriched server-side with app version / OS / Python.
 *   - not configured (every fresh download)  → the submit button opens the
 *     report as a PREFILLED GitHub issue instead, system context included, so
 *     the form is never a dead end and nothing has to be retyped.
 *
 * All external links go through {@link openExternalUrl} rather than a bare
 * `<a target="_blank">`, because the desktop shell (WebView2) silently drops
 * `target="_blank"` / `window.open` — the new tab never appears. The bridge
 * asks the backend to open the OS default browser instead (and falls back to
 * `window.open` on a remote/VPS browser).
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
const DESCRIPTION_MAX = 4000;

type ResultKind = "sent" | "error" | "not_configured" | "github_opened";

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
 * Compose the `/issues/new` URL with the report prefilled. System context
 * comes from the status probe — the same fields the backend would attach on a
 * direct dispatch, so a GitHub issue is not a second-class report.
 */
function buildGithubIssueUrl(
  channel: FeedbackChannelStatus,
  type: FeedbackType,
  title: string,
  description: string,
): string {
  const body = [
    description.trim(),
    "",
    "---",
    `- Type: ${type}`,
    `- App version: ${channel.context.app_version}`,
    `- OS: ${channel.context.os}`,
    `- Python: ${channel.context.python}`,
  ].join("\n");
  const params = new URLSearchParams({ title: title.trim(), body });
  return `${channel.github_url}/new?${params.toString()}`;
}

export function FeedbackView() {
  const t = useT();

  // null = probe pending or failed → default to the in-app submit path (its
  // own error handling covers a server that is actually down).
  const [channel, setChannel] = useState<FeedbackChannelStatus | null>(null);

  const [type, setType] = useState<FeedbackType>("bug");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
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
        /* probe unreachable — keep the in-app path as default */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const githubMode = channel !== null && !channel.configured;

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

  const canSubmit = title.trim().length > 0 && description.trim().length > 0 && !sending;

  const onSubmit = useCallback(
    async (ev: React.FormEvent) => {
      ev.preventDefault();
      if (!canSubmit) return;
      setResult(null);

      if (githubMode && channel) {
        await openExternalUrl(buildGithubIssueUrl(channel, type, title, description));
        setResult("github_opened");
        return;
      }

      setSending(true);
      try {
        const res = await submitFeedback({
          type,
          title: title.trim(),
          description: description.trim(),
          screenshot,
        });
        if (res.status === "sent") {
          setResult("sent");
          setTitle("");
          setDescription("");
          setScreenshot(null);
        } else if (res.status === "not_configured") {
          // The probe said "configured" but the dispatch disagreed (webhook
          // removed meanwhile). Flip to GitHub mode so the button becomes the
          // working path instead of a repeat failure.
          setResult("not_configured");
          if (channel && res.github_url) {
            setChannel({ ...channel, configured: false, github_url: res.github_url });
          }
        } else {
          setResult("error");
        }
      } catch {
        setResult("error");
      } finally {
        setSending(false);
      }
    },
    [canSubmit, channel, description, githubMode, screenshot, title, type],
  );

  const typeOptions: Array<{ value: FeedbackType; label: string; icon: typeof Bug }> = [
    { value: "bug", label: t("feedback.type_bug"), icon: Bug },
    { value: "idea", label: t("feedback.type_idea"), icon: Lightbulb },
    { value: "question", label: t("feedback.type_question"), icon: HelpCircle },
  ];

  const resultBanner: Record<ResultKind, { text: string; cls: string }> = {
    sent: {
      text: t("feedback.result_sent"),
      cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
    },
    error: {
      text: t("feedback.result_error"),
      cls: "border-red-500/40 bg-red-500/10 text-red-400",
    },
    not_configured: {
      text: t("feedback.result_not_configured"),
      cls: "border-amber-500/40 bg-amber-500/10 text-amber-400",
    },
    github_opened: {
      text: t("feedback.result_github_opened"),
      cls: "border-border bg-muted/30 text-muted-foreground",
    },
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <ViewHeader
        icon={<MessageSquareWarning className="h-4 w-4 text-primary" />}
        title={t("nav.feedback")}
        subtitle={t("feedback.subtitle")}
      />

      <div className="flex flex-1 justify-center p-6">
        <div className="w-full max-w-md space-y-6 pb-8">
          {/* Primary path: the Discord community. */}
          <div className="rounded-2xl border border-[#5865F2]/40 bg-[#5865F2]/10 p-8 text-center">
            <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#5865F2]/20">
              <DiscordIcon className="h-9 w-9 text-[#5865F2]" />
            </div>
            <h2 className="mb-2 text-lg font-semibold text-foreground">
              {t("feedback.discord_cta_title")}
            </h2>
            <p className="mb-6 text-sm text-muted-foreground">
              {t("feedback.discord_cta_subtitle")}
            </p>
            <div className="space-y-3">
              <button
                type="button"
                onClick={() => void openExternalUrl(DISCORD_BUG_FORUM_URL)}
                className="w-full rounded-lg bg-[#5865F2] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#4752c4]"
              >
                {t("feedback.discord_cta_button")}
              </button>
              <button
                type="button"
                onClick={() => void openExternalUrl(DISCORD_INVITE_URL)}
                className="w-full rounded-lg border border-[#5865F2]/40 px-4 py-2.5 text-sm font-medium text-[#8b94ff] transition hover:border-[#5865F2]/70 hover:bg-[#5865F2]/10"
              >
                {t("feedback.discord_join_button")}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3" aria-hidden="true">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs uppercase tracking-wider text-muted-foreground">
              {t("feedback.or_send_here")}
            </span>
            <div className="h-px flex-1 bg-border" />
          </div>

          {/* Secondary path: the in-app form (direct dispatch or GitHub issue). */}
          <form
            onSubmit={(ev) => void onSubmit(ev)}
            className="space-y-4 rounded-2xl border border-border bg-card/60 p-6"
          >
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
                    onClick={() => setType(value)}
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
                htmlFor="feedback-description"
                className="mb-1.5 block text-xs font-medium text-muted-foreground"
              >
                {t("feedback.description_label")}
              </label>
              <textarea
                id="feedback-description"
                rows={5}
                value={description}
                maxLength={DESCRIPTION_MAX}
                onChange={(ev) => setDescription(ev.target.value)}
                placeholder={t("feedback.description_placeholder")}
                className={`${INPUT_CLS} resize-y`}
              />
            </div>

            {/* A GitHub issue URL cannot carry an image, so the screenshot
                picker only appears when the direct channel exists. */}
            {channel?.configured ? (
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
                  <p className="mt-1.5 text-xs text-red-400" role="alert">
                    {t("feedback.screenshot_too_large")}
                  </p>
                ) : null}
              </div>
            ) : null}

            {githubMode ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("feedback.github_hint")}
              </p>
            ) : null}

            {result ? (
              <p
                role="status"
                className={`rounded-lg border px-3 py-2 text-sm ${resultBanner[result].cls}`}
              >
                {resultBanner[result].text}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={!canSubmit}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {githubMode ? <ExternalLink className="h-4 w-4" aria-hidden="true" /> : null}
              {sending
                ? t("feedback.sending")
                : githubMode
                  ? t("feedback.github_submit")
                  : t("feedback.submit")}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
