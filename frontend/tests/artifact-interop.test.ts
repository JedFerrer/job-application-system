import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { qaLegacyResume } from "./fixtures/tailored-resume-qa";
import { buildCoverLetterDocx, buildCoverLetterPdf, buildResumeDocx, buildResumePdf } from "@/lib/cover-letter-export";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const backend = resolve(root, "backend");
const python = resolve(backend, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const unicode = "José café ‘quoted’ “quoted” – — ₱ $ €";

const bridge = `
import base64, io, json, sys
import pdfplumber
from app.domain.applications.artifacts import preflight_artifact, package_expected_text, validate_in_isolated_process
request = json.load(sys.stdin.buffer)
results = []
for item in request:
    content = base64.b64decode(item['bytes'])
    preflight_artifact(content=content, document_type=item['type'], format=item['format'])
    expected = package_expected_text(item['package'], item['type'])
    report = validate_in_isolated_process(content=content, format=item['format'], expected=expected, document_type=item['type'])
    extracted = ''
    if item['format'] == 'pdf':
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            extracted = '\\n'.join(page.extract_text() or '' for page in pdf.pages)
    results.append({'state': report.state.value, 'checks': {c.name: c.status for c in report.checks}, 'text': extracted, 'pages': report.page_count})
print(json.dumps(results))
`;

describe("real frontend exports through backend artifact validation", () => {
  it("accepts both DOCX files and validates extractable Unicode from both PDFs", async () => {
    const resume = { ...qaLegacyResume(),
      full_name: "José Example",
      professional_summary: `${unicode} Built reliable services.`,
      work_history: [{
        ...qaLegacyResume().work_history[0]!,
        tailored_highlights: Array.from({ length: 35 }, (_, index) => `Phase ${index + 1}: ${unicode} Delivered reliable service improvements.`),
      }],
    };
    const letter = Array.from({ length: 30 }, (_, index) => `Section ${index + 1}: ${unicode} Delivered reliable service improvements.`).join("\n\n");
    const files = [
      { built: await buildResumePdf(resume, "resume"), type: "resume", package: { resume: { content: resume } } },
      { built: await buildResumeDocx(resume, "resume"), type: "resume", package: { resume: { content: resume } } },
      { built: await buildCoverLetterPdf(letter, "cover"), type: "cover_letter", package: { cover_letter: { mode: "selected", content: letter } } },
      { built: await buildCoverLetterDocx(letter, "cover"), type: "cover_letter", package: { cover_letter: { mode: "selected", content: letter } } },
    ];
    const input = await Promise.all(files.map(async ({ built, type, package: pkg }) => ({
      bytes: Buffer.from(await built.blob.arrayBuffer()).toString("base64"), type, format: built.format, package: pkg,
    })));
    const run = spawnSync(python, ["-c", bridge], { cwd: backend, input: JSON.stringify(input), encoding: "utf8", maxBuffer: 12 * 1024 * 1024 });
    expect(run.status, run.stderr).toBe(0);
    const results = JSON.parse(run.stdout) as Array<{ state: string; checks: Record<string, string>; text: string; pages: number | null }>;
    expect(results).toHaveLength(4);
    for (const result of results) {
      expect(["validated", "warning"], JSON.stringify(results)).toContain(result.state);
      expect(Object.values(result.checks)).not.toContain("fail");
    }
    for (const result of [results[0]!, results[2]!]) {
      for (const fragment of ["José", "café", "‘quoted’", "“quoted”", "–", "—", "₱", "$", "€"]) {
        expect(result.text).toContain(fragment);
      }
      expect(result.text).not.toContain("(cid:0)");
      expect(result.text).not.toContain("�");
      expect(result.pages).toBeGreaterThan(1);
    }
    expect(results[0]!.text).toContain("Phase 35");
    expect(results[2]!.text).toContain("Section 30");
  }, 30_000);
});
