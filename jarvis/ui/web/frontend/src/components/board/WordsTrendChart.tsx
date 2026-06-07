import { useMemo } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HeatmapCell } from "@/hooks/useBoard";
import { useT } from "@/i18n";

export const TREND_YOU = "hsl(50 100% 52%)"; // signal yellow (primary)
export const TREND_JARVIS = "hsl(199 90% 64%)"; // soft sky

interface WordsTrendChartProps {
  cells: HeatmapCell[];
  days?: number;
}

interface Point {
  date: string;
  you: number;
  jarvis: number;
}

/**
 * Area chart of words per day — "you said" vs "Jarvis said" — over the recent
 * window. Two stacked gradient fills with hairline strokes; axis and grid are
 * stripped to near-nothing so the data shape carries the visual, not chrome.
 */
export function WordsTrendChart({ cells, days = 45 }: WordsTrendChartProps) {
  const t = useT();

  const data: Point[] = useMemo(
    () =>
      cells.slice(-days).map((c) => ({
        date: c.date,
        you: c.user_words,
        jarvis: c.jarvis_words,
      })),
    [cells, days],
  );

  const hasData = data.some((d) => d.you > 0 || d.jarvis > 0);
  if (!hasData) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        {t("board_view.chart_empty")}
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 6, right: 2, bottom: 0, left: 2 }}>
        <defs>
          <linearGradient id="board-grad-you" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={TREND_YOU} stopOpacity={0.42} />
            <stop offset="100%" stopColor={TREND_YOU} stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="board-grad-jarvis" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={TREND_JARVIS} stopOpacity={0.28} />
            <stop offset="100%" stopColor={TREND_JARVIS} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tickFormatter={fmtTick}
          tick={{ fill: "hsl(0 0% 50%)", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          minTickGap={44}
          dy={4}
        />
        <YAxis hide domain={[0, "dataMax"]} />
        <Tooltip
          content={<TrendTooltip t={t} />}
          cursor={{ stroke: "hsl(0 0% 32%)", strokeDasharray: "3 3" }}
        />
        <Area
          type="monotone"
          dataKey="jarvis"
          stroke={TREND_JARVIS}
          strokeWidth={1.5}
          fill="url(#board-grad-jarvis)"
          activeDot={{ r: 3, strokeWidth: 0 }}
        />
        <Area
          type="monotone"
          dataKey="you"
          stroke={TREND_YOU}
          strokeWidth={1.75}
          fill="url(#board-grad-you)"
          activeDot={{ r: 3, strokeWidth: 0 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

interface TooltipPayloadItem {
  dataKey: string;
  value: number;
}

function TrendTooltip(props: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string;
  t: (k: string) => string;
}) {
  const { active, payload, label, t } = props;
  if (!active || !payload || payload.length === 0) return null;
  const you = payload.find((p) => p.dataKey === "you")?.value ?? 0;
  const jarvis = payload.find((p) => p.dataKey === "jarvis")?.value ?? 0;
  return (
    <div className="rounded-lg border border-white/10 bg-[#0c0c0c]/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="mb-1.5 font-medium text-foreground">{fmtFull(label)}</div>
      <Row color={TREND_YOU} label={t("board_view.hero.you_spoke")} value={you} />
      <Row color={TREND_JARVIS} label={t("board_view.hero.jarvis_spoke")} value={jarvis} />
    </div>
  );
}

function Row({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      <span className="text-muted-foreground">{label}</span>
      <span className="ml-auto font-semibold tabular-nums">{value.toLocaleString()}</span>
    </div>
  );
}

function fmtTick(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function fmtFull(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
