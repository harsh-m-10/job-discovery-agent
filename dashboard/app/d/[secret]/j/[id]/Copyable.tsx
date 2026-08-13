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
    <div style={{ marginBottom: 10 }}>
      <div className="spread" style={{ alignItems: "center" }}>
        <span className="muted small">{label}</span>
        <button onClick={copy}>{copied ? "copied" : "copy"}</button>
      </div>
      <div style={{ marginTop: 2 }}>{value}</div>
    </div>
  );
}
