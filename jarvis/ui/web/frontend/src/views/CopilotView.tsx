import { useCallback, useEffect, useState } from "react";
import {
  Briefcase,
  FileText,
  FolderOpen,
  GraduationCap,
  Loader2,
  Presentation,
  Radar,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/layout/PageHeader";
import { useEventStore } from "@/store/events";
import { useT, useUiLanguage } from "@/i18n";

interface Material {
  id: string;
  title: string;
  order: string;
  slides: number;
  passed: number;
  total: number;
  quality: string;
  files: string[];
}
interface FileEntry {
  name: string;
  size: number;
}
interface Overview {
  materials: Material[];
  lessons: FileEntry[];
  lesson: {
    topic: string;
    minutes: number;
    elapsed: number;
    remaining: number;
    utterances: number;
    summaries: number;
  } | null;
  lesson_plan: { topic: string; minutes: number } | null;
  observing: boolean;
  workflow_report: string;
  candidates: { steps: string[]; repeats: number; seconds_each: number }[];
  drafts: FileEntry[];
}

async function post(path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Copilot: Material decks, Teacher lessons and Workflow observation in one
 * place. Every button sends the same command a spoken or typed request would
 * (POST /api/copilot/run), so the dashboard adds no second code path.
 */
export function CopilotView() {
  const t = useT();
  const lang = useUiLanguage();
  const pushToast = useEventStore((s) => s.pushToast);
  const [data, setData] = useState<Overview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [order, setOrder] = useState("");
  const [page, setPage] = useState("");
  const [fix, setFix] = useState("");
  const [topic, setTopic] = useState("");
  const [minutes, setMinutes] = useState("25");

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/copilot/overview", { cache: "no-store" });
      if (res.ok) setData((await res.json()) as Overview);
    } catch {
      /* the next refresh retries; the view keeps what it has */
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function run(key: string, text: string) {
    setBusy(key);
    setReply("");
    try {
      const res = await post("/api/copilot/run", { text, language: lang });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setReply(String(body.reply ?? ""));
      await refresh();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function open(area: string, path: string) {
    const res = await post("/api/copilot/open", { area, path });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      pushToast("error", body.detail ?? t("copilot_view.open_failed"));
    }
  }

  const spinner = (key: string) =>
    busy === key ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null;
  const lesson = data?.lesson ?? null;

  return (
    <div className="flex h-full flex-col overflow-y-auto px-8 pb-6">
      <div className="mx-auto w-full max-w-4xl">
        <PageHeader
          icon={<Briefcase />}
          title={t("copilot_view.title")}
          description={t("copilot_view.subtitle")}
        />
        <Tabs defaultValue="material">
          <TabsList>
            <TabsTrigger value="material">
              <Presentation className="mr-1 h-4 w-4" />
              {t("copilot_view.tab_material")}
            </TabsTrigger>
            <TabsTrigger value="teacher">
              <GraduationCap className="mr-1 h-4 w-4" />
              {t("copilot_view.tab_teacher")}
            </TabsTrigger>
            <TabsTrigger value="workflow">
              <Radar className="mr-1 h-4 w-4" />
              {t("copilot_view.tab_workflow")}
            </TabsTrigger>
          </TabsList>

          {reply && (
            <Card className="mt-4 whitespace-pre-wrap p-4 text-sm" data-testid="copilot-reply">
              {reply}
            </Card>
          )}

          <TabsContent value="material" className="mt-4 space-y-4">
            <Card className="space-y-3 p-4">
              <div className="text-sm font-medium">{t("copilot_view.order_label")}</div>
              <Textarea
                value={order}
                onChange={(e) => setOrder(e.target.value)}
                placeholder={t("copilot_view.order_placeholder")}
                rows={3}
              />
              <p className="text-xs text-muted-foreground">{t("copilot_view.order_hint")}</p>
              <Button
                size="sm"
                disabled={!order.trim() || busy !== null}
                onClick={() => void run("make", `${order.trim()} make slides`)}
              >
                {spinner("make")}
                {t("copilot_view.make")}
              </Button>
            </Card>
            {data?.materials.length ? (
              <Card className="space-y-2 p-4">
                <div className="text-sm font-medium">{t("copilot_view.revise_label")}</div>
                <div className="flex gap-2">
                  <Input
                    className="w-20"
                    value={page}
                    onChange={(e) => setPage(e.target.value.replace(/\D/g, ""))}
                    placeholder="3"
                    aria-label={t("copilot_view.page")}
                  />
                  <Input
                    value={fix}
                    onChange={(e) => setFix(e.target.value)}
                    placeholder={t("copilot_view.revise_placeholder")}
                  />
                  <Button
                    size="sm"
                    disabled={!page || !fix.trim() || busy !== null}
                    onClick={() => void run("revise", `page ${page} rewrite: ${fix.trim()}`)}
                  >
                    {spinner("revise")}
                    {t("copilot_view.revise")}
                  </Button>
                </div>
              </Card>
            ) : null}
            {(data?.materials ?? []).map((m) => (
              <Card key={m.id} className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{m.title || m.id}</div>
                    <div className="text-xs text-muted-foreground">
                      {t("copilot_view.slides").replace("{n}", String(m.slides))} · {m.id}
                    </div>
                  </div>
                  <Badge variant={m.passed === m.total && m.total > 0 ? "success" : "warning"}>
                    {t("copilot_view.quality")} {m.passed}/{m.total}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {m.files.map((f) => (
                    <Button key={f} size="sm" variant="outline" onClick={() => void open("materials", `${m.id}/${f}`)}>
                      <FileText className="mr-1 h-3.5 w-3.5" />
                      {f}
                    </Button>
                  ))}
                  <Button size="sm" variant="ghost" onClick={() => void open("materials", m.id)}>
                    <FolderOpen className="mr-1 h-3.5 w-3.5" />
                    {t("copilot_view.folder")}
                  </Button>
                </div>
              </Card>
            ))}
            {data && !data.materials.length && (
              <p className="text-sm text-muted-foreground">{t("copilot_view.no_materials")}</p>
            )}
          </TabsContent>

          <TabsContent value="teacher" className="mt-4 space-y-4">
            <Card className="space-y-3 p-4">
              <div className="text-sm font-medium">{t("copilot_view.plan_label")}</div>
              <div className="flex gap-2">
                <Input
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder={t("copilot_view.plan_placeholder")}
                />
                <Input
                  className="w-24"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value.replace(/\D/g, ""))}
                  aria-label={t("copilot_view.minutes")}
                />
                <Button
                  size="sm"
                  disabled={!topic.trim() || busy !== null}
                  onClick={() =>
                    void run("plan", `make a mock lesson on ${topic.trim()} (${minutes || "25"} min)`)
                  }
                >
                  {spinner("plan")}
                  {t("copilot_view.plan")}
                </Button>
              </div>
              {data?.lesson_plan && (
                <p className="text-xs text-muted-foreground">
                  {t("copilot_view.plan_ready").replace("{topic}", data.lesson_plan.topic)}
                </p>
              )}
            </Card>
            <Card className="space-y-3 p-4">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">{t("copilot_view.live_label")}</div>
                {lesson && <Badge variant="secondary">{t("copilot_view.live_on")}</Badge>}
              </div>
              {lesson ? (
                <p className="text-sm">
                  {t("copilot_view.live_status")
                    .replace("{elapsed}", String(Math.round(lesson.elapsed)))
                    .replace("{remaining}", String(Math.round(lesson.remaining)))
                    .replace("{n}", String(lesson.utterances))}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">{t("copilot_view.live_hint")}</p>
              )}
              <div className="flex flex-wrap gap-2">
                {!lesson ? (
                  <Button
                    size="sm"
                    disabled={busy !== null}
                    onClick={() => void run("start", `start the lesson (${minutes || "25"} min)`)}
                  >
                    {spinner("start")}
                    {t("copilot_view.start")}
                  </Button>
                ) : (
                  <>
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void run("sum", "summarize")}>
                      {spinner("sum")}
                      {t("copilot_view.summary")}
                    </Button>
                    <Button size="sm" disabled={busy !== null} onClick={() => void run("end", "end the lesson")}>
                      {spinner("end")}
                      {t("copilot_view.end")}
                    </Button>
                  </>
                )}
              </div>
            </Card>
            <Card className="space-y-2 p-4">
              <div className="text-sm font-medium">{t("copilot_view.lesson_files")}</div>
              {(data?.lessons ?? []).map((f) => (
                <Button key={f.name} size="sm" variant="outline" className="mr-2" onClick={() => void open("lessons", f.name)}>
                  <FileText className="mr-1 h-3.5 w-3.5" />
                  {f.name}
                </Button>
              ))}
              {data && !data.lessons.length && (
                <p className="text-xs text-muted-foreground">{t("copilot_view.no_lessons")}</p>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="workflow" className="mt-4 space-y-4">
            <Card className="space-y-3 p-4">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">{t("copilot_view.observe_label")}</div>
                {data?.observing && <Badge variant="secondary">{t("copilot_view.observing")}</Badge>}
              </div>
              <p className="text-xs text-muted-foreground">{t("copilot_view.observe_hint")}</p>
              {data?.observing ? (
                <Button size="sm" disabled={busy !== null} onClick={() => void run("stop", "stop observing my work")}>
                  {spinner("stop")}
                  {t("copilot_view.observe_stop")}
                </Button>
              ) : (
                <Button size="sm" disabled={busy !== null} onClick={() => void run("obs", "start observing my work")}>
                  {spinner("obs")}
                  {t("copilot_view.observe_start")}
                </Button>
              )}
            </Card>
            {(data?.candidates ?? []).length > 0 && (
              <Card className="space-y-2 p-4">
                <div className="text-sm font-medium">{t("copilot_view.candidates")}</div>
                {data!.candidates.map((c, i) => (
                  <div key={c.steps.join(">")} className="flex items-center justify-between gap-3 text-sm">
                    <span className="min-w-0 truncate">
                      {i + 1}. {c.steps.join(" → ")} · {c.repeats}×
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => void run(`draft${i}`, `automate candidate ${i + 1}`)}
                    >
                      {spinner(`draft${i}`)}
                      {t("copilot_view.draft")}
                    </Button>
                  </div>
                ))}
                <p className="text-xs text-muted-foreground">{t("copilot_view.draft_hint")}</p>
              </Card>
            )}
            {(data?.drafts ?? []).length > 0 && (
              <Card className="space-y-2 p-4">
                <div className="text-sm font-medium">{t("copilot_view.drafts")}</div>
                {data!.drafts.map((f) => (
                  <Button key={f.name} size="sm" variant="outline" className="mr-2" onClick={() => void open("workflow", f.name)}>
                    <FileText className="mr-1 h-3.5 w-3.5" />
                    {f.name}
                  </Button>
                ))}
              </Card>
            )}
            {data?.workflow_report && (
              <Card className="whitespace-pre-wrap p-4 text-sm">{data.workflow_report}</Card>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
