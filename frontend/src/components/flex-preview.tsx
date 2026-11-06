"use client";

/**
 * Approximate HTML renderer for LINE Flex Message bubbles, used to preview
 * notifications on the dashboard. LINE's own renderer is authoritative.
 */
import type { CSSProperties, ReactNode } from "react";

type Node = { type: string; [k: string]: unknown };

const SIZE: Record<string, string> = { xxs: "10px", xs: "11px", sm: "13px", md: "14px", lg: "17px", xl: "19px", xxl: "22px", "3xl": "28px", "4xl": "34px", "5xl": "40px" };
const SPACE: Record<string, string> = { none: "0", xs: "2px", sm: "4px", md: "8px", lg: "12px", xl: "16px", xxl: "20px" };
const sp = (v: unknown) => (typeof v === "string" ? SPACE[v] ?? v : undefined);

function render(n: Node, key: number | string, parentLayout?: string): ReactNode {
  const style: CSSProperties = {};
  if (n.margin) style.marginTop = parentLayout === "horizontal" || parentLayout === "baseline" ? undefined : sp(n.margin);
  if (n.margin && (parentLayout === "horizontal" || parentLayout === "baseline")) style.marginLeft = sp(n.margin);
  if (typeof n.flex === "number") style.flex = n.flex === 0 ? "0 0 auto" : `${n.flex} 1 0%`;
  else if (parentLayout === "horizontal" || parentLayout === "baseline") style.flex = "1 1 0%";

  switch (n.type) {
    case "box": {
      const layout = n.layout as string;
      const s: CSSProperties = {
        ...style,
        display: "flex",
        flexDirection: layout === "vertical" ? "column" : "row",
        alignItems: layout === "baseline" ? "baseline" : layout === "horizontal" ? "center" : undefined,
        gap: sp(n.spacing),
        backgroundColor: n.backgroundColor as string,
        borderRadius: n.cornerRadius as string,
        padding: (n.paddingAll as string) ?? undefined,
        paddingLeft: n.paddingStart as string,
        paddingRight: n.paddingEnd as string,
        paddingTop: n.paddingTop as string,
        paddingBottom: n.paddingBottom as string,
      };
      return (
        <div key={key} style={s}>
          {((n.contents as Node[]) || []).map((c, i) => render(c, i, layout))}
        </div>
      );
    }
    case "text":
      return (
        <div
          key={key}
          style={{
            ...style,
            fontSize: SIZE[(n.size as string) || "md"],
            fontWeight: n.weight === "bold" ? 700 : 400,
            color: (n.color as string) || "#111",
            textAlign: n.align === "end" ? "right" : n.align === "center" ? "center" : "left",
            whiteSpace: n.wrap ? "normal" : "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            lineHeight: 1.35,
          }}
        >
          {n.text as string}
        </div>
      );
    case "separator":
      return <div key={key} style={{ ...style, flex: "none", height: 1, backgroundColor: (n.color as string) || "#ddd", marginTop: sp(n.margin) || "8px" }} />;
    case "filler":
      return <div key={key} style={{ flex: 1 }} />;
    case "button": {
      const a = n.action as { label?: string; uri?: string };
      const primary = n.style === "primary";
      return (
        <a
          key={key}
          href={a?.uri}
          target="_blank"
          rel="noreferrer"
          style={{
            ...style,
            display: "block",
            textAlign: "center",
            borderRadius: 8,
            padding: n.height === "sm" ? "8px" : "12px",
            backgroundColor: primary ? (n.color as string) : "transparent",
            color: primary ? "#fff" : (n.color as string) || "#42659a",
            fontWeight: 600,
            fontSize: 14,
          }}
        >
          {a?.label}
        </a>
      );
    }
    default:
      return null;
  }
}

export function FlexBubble({ bubble }: { bubble: Node }) {
  const styles = (bubble.styles as Record<string, { backgroundColor?: string }>) || {};
  const blocks = ["header", "hero", "body", "footer"] as const;
  return (
    <div className="w-full max-w-[380px] overflow-hidden rounded-2xl border border-white/10 shadow-lg" style={{ backgroundColor: styles.body?.backgroundColor || "#fff" }}>
      {blocks.map((b) =>
        bubble[b] ? (
          <div key={b} style={{ backgroundColor: styles[b]?.backgroundColor, padding: b === "footer" ? "10px 16px 16px" : undefined }}>
            {render(bubble[b] as Node, b, "vertical")}
          </div>
        ) : null,
      )}
    </div>
  );
}

export function FlexPreview({ message }: { message: Node }) {
  if (message.type === "text") {
    return <div className="max-w-[380px] whitespace-pre-wrap rounded-2xl bg-[#e5e7eb] p-3 text-sm text-black">{message.text as string}</div>;
  }
  const contents = message.contents as Node;
  if (contents?.type === "carousel") {
    return (
      <div className="flex gap-3 overflow-x-auto pb-2">
        {(contents.contents as Node[]).map((b, i) => (
          <div key={i} className="shrink-0">
            <FlexBubble bubble={b} />
          </div>
        ))}
      </div>
    );
  }
  return <FlexBubble bubble={contents} />;
}
