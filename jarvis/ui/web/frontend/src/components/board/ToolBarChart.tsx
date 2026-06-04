import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ToolHistogramEntry } from "@/hooks/useBoard";
import { useT } from "@/i18n";

interface ToolBarChartProps {
  histogram: ToolHistogramEntry[];
  limit?: number;
}

/**
 * Horizontales Bar-Chart der meistgenutzten Tools. Nimmt die Top-N und
 * sortiert absteigend. "Days used" = Anzahl verschiedener Tage in denen
 * das Tool mindestens ein Mal erfolgreich aufgerufen wurde (NICHT pro
 * Invocation — das wuerde Power-Tools wie ``bash`` verzerren).
 */
export function ToolBarChart({ histogram, limit = 15 }: ToolBarChartProps) {
  const t = useT();
  const data = histogram.slice(0, limit).map((entry) => ({
    tool: entry.tool,
    days: entry.days_used,
  }));

  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-border/60 text-xs text-muted-foreground">
        {t("board_view.tools_chart_empty")}
      </div>
    );
  }

  const height = Math.max(180, data.length * 28);

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 24, bottom: 4, left: 8 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            horizontal={false}
          />
          <XAxis
            type="number"
            allowDecimals={false}
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
          />
          <YAxis
            type="category"
            dataKey="tool"
            width={120}
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
          />
          <Tooltip
            cursor={{ fill: "hsl(var(--muted) / 0.2)" }}
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "hsl(var(--foreground))" }}
            formatter={(value) => {
              const n = typeof value === "number" ? value : Number(value) || 0;
              const days = n === 1
                ? t("board_view.tools_chart_days_used_one").replace("{0}", String(n))
                : t("board_view.tools_chart_days_used").replace("{0}", String(n));
              return [days, t("board_view.tools_chart_used_label")];
            }}
          />
          <Bar
            dataKey="days"
            fill="hsl(var(--primary))"
            radius={[0, 4, 4, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
