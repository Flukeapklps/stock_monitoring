"use client";

import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Row = { date: string; [k: string]: number | string | boolean | null };

const axis = { stroke: "#4b5563", fontSize: 10, tickLine: false };
const tooltipStyle = { backgroundColor: "#0b0f14", border: "1px solid #1f2937", fontSize: 12 };

export function RsiChart({ rows, height = 140 }: { rows: Row[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
        <CartesianGrid stroke="#111827" />
        <XAxis dataKey="date" {...axis} minTickGap={40} />
        <YAxis domain={[0, 100]} ticks={[30, 40, 50, 70]} {...axis} />
        <Tooltip contentStyle={tooltipStyle} />
        <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="3 3" />
        <ReferenceLine y={40} stroke="#22c55e" strokeDasharray="3 3" />
        <ReferenceLine y={50} stroke="#22c55e" strokeDasharray="3 3" />
        <ReferenceLine y={30} stroke="#eab308" strokeDasharray="3 3" />
        <Line type="monotone" dataKey="rsi_14" name="RSI (14)" stroke="#a78bfa" dot={false} strokeWidth={1.5} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function MacdChart({ rows, height = 160 }: { rows: Row[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
        <CartesianGrid stroke="#111827" />
        <XAxis dataKey="date" {...axis} minTickGap={40} />
        <YAxis {...axis} />
        <Tooltip contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <ReferenceLine y={0} stroke="#374151" />
        <Bar dataKey="macd_hist" name="Histogram" fill="#334155" isAnimationActive={false} />
        <Line type="monotone" dataKey="macd" name="MACD" stroke="#38bdf8" dot={false} strokeWidth={1.5} isAnimationActive={false} />
        <Line type="monotone" dataKey="macd_signal" name="Signal" stroke="#f97316" dot={false} strokeWidth={1.2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function EquityChart({ rows, series, height = 280 }: { rows: Row[]; series: { key: string; color: string; label?: string }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#111827" />
        <XAxis dataKey="date" {...axis} minTickGap={50} />
        <YAxis {...axis} domain={["auto", "auto"]} />
        <Tooltip contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {series.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label || s.key} stroke={s.color} dot={false} strokeWidth={1.5} isAnimationActive={false} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function AreaSpark({ rows, dataKey, color = "#22c55e", height = 60 }: { rows: Row[]; dataKey: string; color?: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
        <Area type="monotone" dataKey={dataKey} stroke={color} fill={color} fillOpacity={0.12} dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
