interface Props {
  pageUrl: string;
  onMarkApplied: () => void;
  onNotYet: () => void;
}

export default function ConfirmationPage({ pageUrl, onMarkApplied, onNotYet }: Props) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 16,
          marginBottom: 16,
        }}
      >
        <p style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
          Did you submit this application?
        </p>
        <p
          style={{
            fontSize: 11,
            color: "var(--muted)",
            wordBreak: "break-all",
            marginBottom: 8,
          }}
        >
          {pageUrl}
        </p>
        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          We could not detect a confirmation message. Please confirm whether you
          successfully submitted the application.
        </p>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={onMarkApplied}
          style={{
            flex: 1,
            padding: "10px 0",
            background: "var(--accent)",
            color: "var(--accent-fg)",
            border: "none",
            borderRadius: 6,
            fontSize: 14,
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Yes, mark as applied
        </button>
        <button
          onClick={onNotYet}
          style={{
            flex: 1,
            padding: "10px 0",
            background: "none",
            border: "1px solid var(--border)",
            borderRadius: 6,
            fontSize: 14,
            cursor: "pointer",
            color: "var(--fg)",
          }}
        >
          Not yet
        </button>
      </div>
    </div>
  );
}
