import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataStatusBadge, ScorePill, SignalBadge } from "@/components/badges";
import { FlexPreview } from "@/components/flex-preview";
import { StockTable, filterAndSort } from "@/components/stock-table";
import { changeClass, fmtMoney, fmtPct, labelize } from "@/lib/format";
import type { StockRow } from "@/lib/types";

function row(p: Partial<StockRow>): StockRow {
  return {
    symbol: "AAPL",
    name: "Apple Inc.",
    asset_type: "EQUITY",
    exchange: "NASDAQ",
    sector: null,
    price: 100,
    previous_close: 99,
    change: 1,
    change_pct: 1.01,
    trade_date: "2026-11-04",
    return_1y: 20,
    return_3m: 5,
    return_6m: 10,
    rsi_14: 55,
    sma_50: 95,
    sma_200: 90,
    dist_sma_200_pct: 11,
    dist_52w_high_pct: -3,
    trend: "BULLISH",
    score: 80,
    score_classification: "STRONG_LONG_TERM_TREND",
    score_coverage: 0.9,
    latest_signal: "BULLISH",
    latest_signal_date: "2026-11-04",
    provider: "yahoo",
    provider_label: "Yahoo Finance",
    data_status: "END_OF_DAY",
    ...p,
  };
}

describe("format helpers", () => {
  it("formats money, percent and missing values", () => {
    expect(fmtMoney(1234.5)).toBe("$1,234.50");
    expect(fmtMoney(-2)).toBe("-$2.00");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtPct(1.234)).toBe("+1.23%");
    expect(fmtPct(-0.5)).toBe("-0.50%");
    expect(fmtPct(undefined)).toBe("—");
    expect(changeClass(-1)).toBe("text-bear");
    expect(changeClass(0)).toBe("text-bull");
    expect(labelize("ACCUMULATION_WATCH")).toBe("ACCUMULATION WATCH");
  });
});

describe("badges", () => {
  it("never labels end-of-day data as live", () => {
    render(<DataStatusBadge status="END_OF_DAY" />);
    expect(screen.getByTestId("data-status")).toHaveTextContent("END OF DAY");
    expect(screen.getByTestId("data-status")).not.toHaveTextContent("LIVE");
  });
  it("shows missing status as unavailable", () => {
    render(<DataStatusBadge status={null} />);
    expect(screen.getByTestId("data-status")).toHaveTextContent("UNAVAILABLE");
  });
  it("marks low-coverage scores", () => {
    const { container } = render(<ScorePill score={72.4} coverage={0.4} />);
    expect(container).toHaveTextContent("72*");
  });
  it("renders signal labels", () => {
    render(<SignalBadge signal="TREND_WEAKENING" />);
    expect(screen.getByText("TREND WEAKENING")).toBeInTheDocument();
  });
});

describe("stock table", () => {
  const rows = [row({ symbol: "AAPL", score: 80 }), row({ symbol: "MSFT", name: "Microsoft", score: 90, trend: "NEUTRAL" }), row({ symbol: "XOM", name: "Exxon", score: null, trend: "BEARISH" })];

  it("filters and sorts with nulls last", () => {
    expect(filterAndSort(rows, "", "", "score", "desc").map((r) => r.symbol)).toEqual(["MSFT", "AAPL", "XOM"]);
    expect(filterAndSort(rows, "", "", "score", "asc").map((r) => r.symbol)).toEqual(["AAPL", "MSFT", "XOM"]);
    expect(filterAndSort(rows, "micro", "", "symbol", "asc").map((r) => r.symbol)).toEqual(["MSFT"]);
    expect(filterAndSort(rows, "", "BEARISH", "symbol", "asc").map((r) => r.symbol)).toEqual(["XOM"]);
  });

  it("renders rows, search and remove", () => {
    const onRemove = vi.fn();
    render(<StockTable rows={rows} onRemove={onRemove} />);
    const table = screen.getByTestId("stock-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    fireEvent.change(screen.getByLabelText("Filter stocks"), { target: { value: "xom" } });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    fireEvent.click(screen.getByLabelText("Remove XOM"));
    expect(onRemove).toHaveBeenCalledWith("XOM");
  });

  it("sorts when a header is clicked", () => {
    render(<StockTable rows={rows} />);
    fireEvent.click(screen.getByRole("button", { name: /Symbol/ }));
    const links = within(screen.getByTestId("stock-table")).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["AAPL", "MSFT", "XOM"]);
  });
});

describe("flex preview", () => {
  it("renders an alert bubble with text and https button", () => {
    const msg = {
      type: "flex",
      altText: "AAPL",
      contents: {
        type: "bubble",
        body: {
          type: "box",
          layout: "vertical",
          contents: [
            { type: "text", text: "📈 AAPL — ACCUMULATION WATCH", weight: "bold", color: "#F9FAFB" },
            { type: "separator", color: "#1F2937" },
            { type: "box", layout: "horizontal", contents: [{ type: "text", text: "Investment Score" }, { type: "text", text: "85/100", align: "end" }] },
          ],
        },
        footer: { type: "box", layout: "vertical", contents: [{ type: "button", style: "primary", color: "#22C55E", action: { type: "uri", label: "View Stock Details", uri: "https://example.com/stocks/AAPL" } }] },
      },
    };
    render(<FlexPreview message={msg} />);
    expect(screen.getByText("📈 AAPL — ACCUMULATION WATCH")).toBeInTheDocument();
    expect(screen.getByText("85/100")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View Stock Details" })).toHaveAttribute("href", "https://example.com/stocks/AAPL");
  });
  it("renders text messages", () => {
    render(<FlexPreview message={{ type: "text", text: "hello" }} />);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});
