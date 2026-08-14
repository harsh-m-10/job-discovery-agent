"use client";

import { useState } from "react";

export default function Copyable({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="copy-row">
      <div style={{ minWidth: 0 }}>
        <div className="copy-k">{label}</div>
        <div className="copy-v">{value}</div>
      </div>
      <button onClick={copy} style={{ flexShrink: 0, minHeight: 34, padding: "6px 12px" }}>
        {copied ? "copied ✓" : "copy"}
      </button>
    </div>
  );
}
