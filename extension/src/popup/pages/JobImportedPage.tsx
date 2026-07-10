import type { ExtractedJob } from "../../shared/types";

interface Props {
  job: ExtractedJob;
  response: { job_id: number; match_status: string };
}

export default function JobImportedPage({ job, response }: Props) {
  const dashboardUrl = "http://localhost:3000/dashboard";

  return (
    <div>
      <div
        style={{
          background: "#f0fdf4",
          border: "1px solid #bbf7d0",
          borderRadius: 8,
          padding: 12,
          marginBottom: 12,
          textAlign: "center",
        }}
      >
        <p style={{ fontSize: 14, fontWeight: 600, color: "#166534", marginBottom: 4 }}>
          ✓ Imported Successfully
        </p>
        <p style={{ fontSize: 13, color: "#166534" }}>{job.title}</p>
        <p style={{ fontSize: 12, color: "#15803d" }}>{job.company}</p>
      </div>

      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 12,
          marginBottom: 12,
        }}
      >
        <p style={{ fontSize: 13, marginBottom: 4 }}>
          Match Score:{" "}
          {response.match_status === "queued" ? (
            <span style={{ color: "var(--muted)" }}>Processing…</span>
          ) : (
            <span style={{ color: "var(--accent)" }}>{response.match_status}</span>
          )}
        </p>
      </div>

      <button
        onClick={() => window.open(`${dashboardUrl}/matches`, "_blank")}
        style={{
          width: "100%",
          padding: "10px 0",
          background: "var(--accent)",
          color: "var(--accent-fg)",
          border: "none",
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 500,
          cursor: "pointer",
          marginBottom: 8,
        }}
      >
        Open in Dashboard
      </button>

      <button
        onClick={() => window.open(`${dashboardUrl}/applications`, "_blank")}
        style={{
          width: "100%",
          padding: "10px 0",
          background: "none",
          border: "1px solid var(--border)",
          borderRadius: 6,
          fontSize: 14,
          cursor: "pointer",
          color: "var(--fg)",
        }}
      >
        View in Applications
      </button>
    </div>
  );
}
