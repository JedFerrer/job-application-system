import type { ExtractedJob } from "../../shared/types";

interface Props {
  job: ExtractedJob;
  onImport: (job: ExtractedJob) => void;
}

export default function JobDetectedPage({ job, onImport }: Props) {
  return (
    <div>
      <div
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 12,
          marginBottom: 12,
        }}
      >
        <p style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
          CareerOS detected a job
        </p>
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 2 }}>
          {job.title}
        </h3>
        <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 2 }}>
          {job.company}
        </p>
        {job.location && (
          <p style={{ fontSize: 12, color: "var(--muted)" }}>{job.location}</p>
        )}
        {job.salaryText && (
          <p style={{ fontSize: 12, color: "var(--accent)", marginTop: 4 }}>
            {job.salaryText}
          </p>
        )}
      </div>

      <button
        onClick={() => onImport(job)}
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
        }}
      >
        Import &amp; Analyze
      </button>
    </div>
  );
}
