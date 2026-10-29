import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AccuracyData, EvolutionData, Point } from "./api";
import { clock, dayLabel, number } from "./common";

const grid = "#e7ecee";
const axis = { fontSize: 11, fill: "#6d7d84" };
const power = (value: number) => number(value / 1000, 1);
const tooltipStyle = {
  border: "1px solid #dce4e6",
  borderRadius: 8,
  fontSize: 12,
  boxShadow: "0 4px 20px #142b3210",
};

export function Key({
  actual = false,
  band = true,
}: {
  actual?: boolean;
  band?: boolean;
}) {
  return (
    <div className="chart-key">
      <span>
        <i className="key-line" />
        Decision-time forecast
      </span>
      {band && (
        <span>
          <i className="key-band" />
          Historical P10–P90
        </span>
      )}
      {actual && (
        <span>
          <i className="key-actual" />
          Actual
        </span>
      )}
    </div>
  );
}

export function WindChart({
  points,
  reveal = false,
  long = false,
  band = true,
}: {
  points: Point[];
  reveal?: boolean;
  long?: boolean;
  band?: boolean;
}) {
  const data = points.map((point) => ({
    ...point,
    t: new Date(point.delivery_time).getTime(),
    range:
      point.lower_mw != null && point.upper_mw != null
        ? [point.lower_mw, point.upper_mw]
        : null,
  }));
  return (
    <div
      className="chart-frame"
      role="img"
      aria-label="Decision-time wind forecast, historical empirical error range, and revealed actual generation in gigawatts"
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 15, right: 14, bottom: 23, left: 9 }}
        >
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(value) =>
              long
                ? dayLabel(new Date(value).toISOString()).replace(" 2026", "")
                : clock(value)
            }
            tick={axis}
            minTickGap={35}
            axisLine={false}
            tickLine={false}
            label={{
              value: long
                ? "Delivery date · Europe/London"
                : "Delivery time · GMT",
              position: "insideBottom",
              offset: -17,
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <YAxis
            tick={axis}
            tickFormatter={power}
            axisLine={false}
            tickLine={false}
            width={55}
            domain={[0, "auto"]}
            label={{
              value: "Wind generation (GW)",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelFormatter={(value) =>
              long
                ? `${dayLabel(new Date(Number(value)).toISOString())} · ${clock(Number(value))}`
                : `${clock(Number(value))} GMT`
            }
            formatter={(value, name) => [
              Array.isArray(value)
                ? `${number(Number(value[0]))} – ${number(Number(value[1]))} MW`
                : `${number(Number(value))} MW`,
              name,
            ]}
          />
          {band && (
            <Area
              type="linear"
              dataKey="range"
              name="Historical empirical range"
              fill="#d8eee8"
              stroke="none"
              connectNulls={false}
              isAnimationActive={false}
            />
          )}
          <Line
            type="linear"
            dataKey="forecast_mw"
            name="Decision-time forecast"
            stroke="#238274"
            strokeWidth={2.3}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          {reveal && (
            <Line
              type="linear"
              dataKey="actual_mw"
              name="Actual"
              stroke="#647a9b"
              strokeWidth={1.7}
              dot={false}
              strokeDasharray="5 3"
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function EvolutionChart({ data }: { data: EvolutionData }) {
  const selected = data.versions.find((version) => version.selected);
  const rows = data.versions.map((version) => ({
    ...version,
    t: new Date(version.publish_time).getTime(),
    eligible_mw: version.eligible ? version.forecast_mw : null,
    future_mw:
      !version.eligible || version.selected ? version.forecast_mw : null,
  }));
  const cutoff = new Date(data.decision_cutoff).getTime();
  return (
    <div
      className="chart-frame evolution-chart"
      role="img"
      aria-label="Wind forecast revisions by publication time with decision cutoff and selected vintage"
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={rows}
          margin={{ top: 30, right: 30, bottom: 26, left: 9 }}
        >
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={[
              Math.min(cutoff, ...rows.map((row) => row.t)),
              Math.max(cutoff, ...rows.map((row) => row.t)),
            ]}
            tickFormatter={(value) =>
              `${dayLabel(new Date(value).toISOString()).split(" 2026")[0]} ${clock(value)}`
            }
            tick={axis}
            minTickGap={45}
            axisLine={false}
            tickLine={false}
            label={{
              value: "Forecast publication time · GMT",
              position: "insideBottom",
              offset: -20,
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <YAxis
            tick={axis}
            tickFormatter={power}
            axisLine={false}
            tickLine={false}
            width={55}
            label={{
              value: "Wind generation (GW)",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelFormatter={(value) =>
              `${dayLabel(new Date(Number(value)).toISOString())} · ${clock(Number(value))} GMT`
            }
            formatter={(value) => `${number(Number(value))} MW`}
          />
          <ReferenceLine
            x={cutoff}
            stroke="#b78237"
            strokeDasharray="4 4"
            label={{
              value: "DECISION CUTOFF",
              position: "insideTopRight",
              fontSize: 10,
              fill: "#9b6b26",
            }}
          />
          {data.actual_mw != null && (
            <ReferenceLine
              y={data.actual_mw}
              stroke="#647a9b"
              strokeDasharray="5 3"
              label={{
                value: "ACTUAL",
                position: "insideTopRight",
                fontSize: 10,
                fill: "#647a9b",
              }}
            />
          )}
          <Line
            type="stepAfter"
            dataKey="eligible_mw"
            name="Available forecast"
            stroke="#238274"
            strokeWidth={2.2}
            dot={{ r: 3, fill: "#238274" }}
            isAnimationActive={false}
          />
          <Line
            type="stepAfter"
            dataKey="future_mw"
            name="Later forecast (revealed)"
            stroke="#94a3b8"
            strokeDasharray="4 4"
            dot={{ r: 2 }}
            isAnimationActive={false}
          />
          {selected && (
            <ReferenceDot
              x={new Date(selected.publish_time).getTime()}
              y={selected.forecast_mw}
              r={6}
              fill="#238274"
              stroke="white"
              strokeWidth={2}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScatterPlot({ points }: { points: Point[] }) {
  const max =
    Math.ceil(
      Math.max(
        1000,
        ...points.flatMap((point) => [
          point.forecast_mw || 0,
          point.actual_mw || 0,
        ]),
      ) / 5000,
    ) * 5000;
  return (
    <div
      className="chart-frame small-chart"
      role="img"
      aria-label="Forecast versus actual scatter plot, with ideal y equals x reference"
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 10, right: 15, bottom: 25, left: 10 }}>
          <CartesianGrid stroke={grid} />
          <XAxis
            type="number"
            dataKey="forecast_mw"
            name="Forecast"
            domain={[0, max]}
            tickFormatter={power}
            tick={axis}
            label={{
              value: "Forecast (GW)",
              position: "insideBottom",
              offset: -18,
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <YAxis
            type="number"
            dataKey="actual_mw"
            name="Actual"
            domain={[0, max]}
            tickFormatter={power}
            tick={axis}
            width={50}
            label={{
              value: "Actual (GW)",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) => `${number(Number(value))} MW`}
          />
          <ReferenceLine
            segment={[
              { x: 0, y: 0 },
              { x: max, y: max },
            ]}
            stroke="#9ba9ad"
            strokeDasharray="4 4"
          />
          <Scatter
            data={points}
            fill="#238274"
            fillOpacity={0.35}
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Histogram({ data }: { data: AccuracyData["histogram"] }) {
  const rows = data.map((bin) => ({
    ...bin,
    label: `${number(bin.lower_mw / 1000, 1)}`,
  }));
  return (
    <div
      className="chart-frame small-chart"
      role="img"
      aria-label="Histogram of actual minus forecast errors in gigawatts"
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          margin={{ top: 10, right: 15, bottom: 25, left: 4 }}
        >
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={axis}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            label={{
              value: "Signed error (GW) · bin lower edge",
              position: "insideBottom",
              offset: -18,
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <YAxis
            tick={axis}
            width={42}
            axisLine={false}
            tickLine={false}
            label={{
              value: "Observations",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelFormatter={(_, payload) =>
              payload[0]
                ? `${number(payload[0].payload.lower_mw)} to ${number(payload[0].payload.upper_mw)} MW`
                : ""
            }
          />
          <Bar
            dataKey="count"
            name="Observations"
            fill="#607e89"
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ErrorBuckets({
  buckets,
  axisLabel,
}: {
  buckets: AccuracyData["time_of_day"];
  axisLabel: string;
}) {
  const rows = buckets
    .filter((bucket) => bucket.metrics.sample_count > 0)
    .map((bucket) => ({
      label: bucket.label,
      mae: bucket.metrics.mae_mw,
      n: bucket.metrics.sample_count,
    }));
  return (
    <div
      className="chart-frame small-chart"
      role="img"
      aria-label={`Mean absolute error by ${axisLabel}`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          margin={{ top: 10, right: 15, bottom: 25, left: 10 }}
        >
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={axis}
            axisLine={false}
            tickLine={false}
            label={{
              value: axisLabel,
              position: "insideBottom",
              offset: -18,
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <YAxis
            tick={axis}
            tickFormatter={power}
            axisLine={false}
            tickLine={false}
            width={45}
            label={{
              value: "MAE (GW)",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "#6d7d84",
            }}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) => `${number(Number(value))} MW`}
            labelFormatter={(label, payload) =>
              `${label} · n=${payload[0]?.payload.n || 0}`
            }
          />
          <Bar
            dataKey="mae"
            name="MAE"
            fill="#5b9588"
            radius={[3, 3, 0, 0]}
            maxBarSize={42}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
