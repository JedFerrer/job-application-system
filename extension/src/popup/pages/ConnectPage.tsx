import { useState, useCallback } from "react";
import { sendMessage } from "../../shared/messages";

interface Props {
  onConnected: (user: { id: number; email: string; name: string }) => void;
}

export default function ConnectPage({ onConnected }: Props) {
  const [pairingCode, setPairingCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = useCallback(async () => {
    const code = pairingCode.trim();
    if (!code) {
      setError("Enter a pairing code");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await sendMessage({ type: "START_PAIRING", pairingCode: code });
      if (res.type === "SESSION_STATE" && res.payload.connected && res.payload.user) {
        onConnected(res.payload.user);
      } else if (res.type === "ERROR") {
        setError(res.payload.message);
      } else {
        setError("Connection failed. Check your pairing code and try again.");
      }
    } catch {
      setError("Connection failed. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }, [pairingCode, onConnected]);

  return (
    <div>
      <div style={{ textAlign: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>
          CareerOS Assistant
        </h2>
        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          Connect your CareerOS account to import jobs and fill applications.
        </p>
      </div>

      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 16,
          marginBottom: 12,
        }}
      >
        <p style={{ fontSize: 13, marginBottom: 8 }}>
          1. Open{" "}
          <a
            href="http://localhost:3000/extension/connect"
            target="_blank"
            rel="noopener"
            style={{ color: "var(--accent)" }}
          >
            the connection page
          </a>{" "}
          in your browser
        </p>
        <p style={{ fontSize: 13, marginBottom: 8 }}>
          2. Sign in to CareerOS if needed
        </p>
        <p style={{ fontSize: 13, marginBottom: 8 }}>
          3. Copy the pairing code shown there
        </p>
        <p style={{ fontSize: 13 }}>4. Paste it below and click Connect</p>
      </div>

      <input
        type="text"
        value={pairingCode}
        onChange={(e) => setPairingCode(e.target.value)}
        placeholder="Paste pairing code here"
        disabled={loading}
        style={{
          width: "100%",
          padding: "10px 12px",
          borderRadius: 6,
          border: "1px solid var(--border)",
          fontSize: 14,
          background: "var(--bg)",
          color: "var(--fg)",
          marginBottom: 8,
          outline: "none",
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") void handleConnect();
        }}
      />

      {error && (
        <p style={{ color: "var(--destructive)", fontSize: 12, marginBottom: 8 }}>
          {error}
        </p>
      )}

      <button
        onClick={() => void handleConnect()}
        disabled={loading || !pairingCode.trim()}
        style={{
          width: "100%",
          padding: "10px 0",
          background: loading ? "var(--muted)" : "var(--accent)",
          color: "var(--accent-fg)",
          border: "none",
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 500,
          cursor: loading ? "not-allowed" : "pointer",
        }}
      >
        {loading ? "Connecting…" : "Connect"}
      </button>
    </div>
  );
}
