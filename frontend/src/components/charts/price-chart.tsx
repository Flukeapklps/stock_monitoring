"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

export interface Bar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface Overlay {
  key: string;
  label: string;
  color: string;
  points: { date: string; value: number | null }[];
}

export interface Marker {
  date: string;
  text: string;
  color: string;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
}

export function PriceChart({
  bars,
  overlays = [],
  markers = [],
  levels = [],
  height = 420,
}: {
  bars: Bar[];
  overlays?: Overlay[];
  markers?: Marker[];
  levels?: { price: number; color: string; title: string }[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9ca3af", fontSize: 11 },
      grid: { vertLines: { color: "#111827" }, horzLines: { color: "#111827" } },
      rightPriceScale: { borderColor: "#1f2937" },
      timeScale: { borderColor: "#1f2937" },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    const valid = bars.filter((b) => b.open !== null && b.high !== null && b.low !== null && b.close !== null);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    candles.setData(valid.map((b) => ({ time: b.date as Time, open: b.open!, high: b.high!, low: b.low!, close: b.close! })));

    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, color: "#334155" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      valid
        .filter((b) => b.volume !== null)
        .map((b) => ({ time: b.date as Time, value: b.volume!, color: b.close! >= b.open! ? "#14532d" : "#7f1d1d" })),
    );

    for (const o of overlays) {
      const s = chart.addSeries(LineSeries, { color: o.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: o.label });
      s.setData(o.points.filter((p) => p.value !== null).map((p) => ({ time: p.date as Time, value: p.value! })));
    }
    for (const lv of levels) {
      candles.createPriceLine({ price: lv.price, color: lv.color, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: lv.title });
    }
    if (markers.length) {
      const dates = new Set(valid.map((b) => b.date));
      const ms: SeriesMarker<Time>[] = markers
        .filter((m) => dates.has(m.date))
        .sort((a, b) => a.date.localeCompare(b.date))
        .map((m) => ({ time: m.date as Time, position: m.position, color: m.color, shape: m.shape, text: m.text }));
      createSeriesMarkers(candles, ms);
    }
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, overlays, markers, levels, height]);

  return <div ref={ref} className="w-full" style={{ height }} data-testid="price-chart" />;
}
