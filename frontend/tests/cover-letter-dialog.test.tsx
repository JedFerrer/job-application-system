import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createContext, useContext, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CoverLetterDialog } from "@/components/cover/cover-letter-dialog";

const exportMocks = vi.hoisted(() => ({ downloadPdf: vi.fn() }));
const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("@/lib/cover-letter-export", () => ({ downloadPdf: exportMocks.downloadPdf, downloadDocx: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

const apiMocks = vi.hoisted(() => ({ getByJobId: vi.fn(), update: vi.fn(), generate: vi.fn() }));
const lifecycle = vi.hoisted(() => ({ dialogClose: undefined as undefined | ((event: { preventDefault: () => void }) => void), alertClose: undefined as undefined | ((event: { preventDefault: () => void }) => void) }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error { status = 500; }, coverLettersApi: apiMocks }));
vi.mock("@/components/ui/dialog", () => ({ Dialog: ({ children, onOpenChange, open }: { children: React.ReactNode; onOpenChange: (open: boolean) => void; open: boolean }) => open ? <><button onClick={() => onOpenChange(false)}>Close dialog</button>{children}</> : null, DialogContent: ({ children, onCloseAutoFocus, onFocusCapture }: { children: React.ReactNode; onCloseAutoFocus?: (event: { preventDefault: () => void }) => void; onFocusCapture?: (event: React.FocusEvent) => void }) => { lifecycle.dialogClose = onCloseAutoFocus; return <div onFocusCapture={onFocusCapture}>{children}</div>; }, DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>, DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>, DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
const AlertContext = createContext<((open: boolean) => void) | null>(null);
vi.mock("@/components/ui/alert-dialog", () => ({ AlertDialog: ({ open, children, onOpenChange }: { open: boolean; children: React.ReactNode; onOpenChange: (open: boolean) => void }) => open ? <AlertContext.Provider value={onOpenChange}><div>{children}</div></AlertContext.Provider> : null, AlertDialogContent: ({ children, onCloseAutoFocus }: { children: React.ReactNode; onCloseAutoFocus?: (event: { preventDefault: () => void }) => void }) => { lifecycle.alertClose = onCloseAutoFocus; return <div>{children}</div>; }, AlertDialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, AlertDialogTitle: ({ children }: { children: React.ReactNode }) => <h3>{children}</h3>, AlertDialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>, AlertDialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, AlertDialogAction: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => { const setOpen = useContext(AlertContext); return <button onClick={() => { onClick?.(); setOpen?.(false); if (children === "Discard") queueMicrotask(() => lifecycle.dialogClose?.({ preventDefault: () => undefined })); }}>{children}</button>; }, AlertDialogCancel: ({ children }: { children: React.ReactNode }) => { const setOpen = useContext(AlertContext); return <button onClick={() => { setOpen?.(false); lifecycle.alertClose?.({ preventDefault: () => undefined }); }}>{children}</button>; } }));

describe("CoverLetterDialog", () => {
  let root: Root; let node: HTMLDivElement; let dirty: (value: boolean) => void;
  beforeEach(() => { node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); dirty = vi.fn<(value: boolean) => void>(); lifecycle.dialogClose = undefined; lifecycle.alertClose = undefined; apiMocks.getByJobId.mockResolvedValue({ id: 2, job_id: 7, content: "Persisted text", tone: null, model: "fixture", is_edited: false, created_at: "2026-01-01", updated_at: "2026-01-01" }); apiMocks.update.mockResolvedValue({ id: 2, job_id: 7, content: "Changed text", tone: null, model: "fixture", is_edited: true, created_at: "2026-01-01", updated_at: "2026-01-02" }); });
  afterEach(() => { root.unmount(); node.remove(); vi.clearAllMocks(); });
  async function render(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
    await act(async () => { root.render(<QueryClientProvider client={client}><CoverLetterDialog jobId={7} open onOpenChange={vi.fn()} onDirtyChange={dirty} /></QueryClientProvider>); });
    await vi.waitFor(() => expect((node.querySelector("textarea") as HTMLTextAreaElement | null)?.value).toBe("Persisted text"));
    return client;
  }
  async function edit(value: string) {
    const textarea = node.querySelector("textarea")!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
  }
  it("loads a saved letter, edits and saves without generation", async () => {
    await render();
    await edit("Changed text");
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Save edits")?.click(); });
    expect(apiMocks.update).toHaveBeenCalledWith(2, "Changed text");
    expect(apiMocks.generate).not.toHaveBeenCalled();
  });

  it("handles a failed PDF download and succeeds when clicked again", async () => {
    exportMocks.downloadPdf.mockRejectedValueOnce(new Error("internal chunk URL"))
      .mockResolvedValueOnce(undefined);
    await render();
    const pdf = () => Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "PDF")!;
    await act(async () => { pdf().click(); await Promise.resolve(); });
    expect(toastMocks.error).toHaveBeenCalledWith("Could not create the PDF. Please try again.");
    expect(toastMocks.error).not.toHaveBeenCalledWith(expect.stringContaining("internal chunk URL"));
    expect(pdf().disabled).toBe(false);
    await act(async () => { pdf().click(); await Promise.resolve(); });
    expect(exportMocks.downloadPdf).toHaveBeenCalledTimes(2);
    expect(exportMocks.downloadPdf).toHaveBeenLastCalledWith("Persisted text", "cover_letter");
  });

  it("keeps local edits on Cancel and restores the baseline on Discard", async () => {
    await render();
    await edit("Unsaved local edit");
    await vi.waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    expect(node.textContent).toContain("Save or discard unsaved edits?");
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Cancel")?.click(); });
    expect((node.querySelector("textarea") as HTMLTextAreaElement).value).toBe("Unsaved local edit");
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Discard")?.click(); await Promise.resolve(); });
    expect((node.querySelector("textarea") as HTMLTextAreaElement).value).toBe("Persisted text");
    await vi.waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false));
    expect(apiMocks.update).not.toHaveBeenCalled();
  });

  it("restores editor focus on Cancel and the opener on Discard", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      const openerRef = useRef<HTMLButtonElement>(null);
      return <><button ref={openerRef}>Workspace opener</button><CoverLetterDialog jobId={7} open={open} onOpenChange={setOpen} onDirtyChange={dirty} returnFocusRef={openerRef} /></>;
    }
    await act(async () => { root.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Harness /></QueryClientProvider>); });
    await vi.waitFor(() => expect((node.querySelector("textarea") as HTMLTextAreaElement | null)?.value).toBe("Persisted text"));
    const textarea = node.querySelector("textarea")!;
    textarea.focus();
    await edit("Unsaved local edit");
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Cancel")?.click(); await Promise.resolve(); });
    expect(document.activeElement).toBe(textarea);
    expect(document.activeElement).not.toBe(document.body);
    expect((node.querySelector("textarea") as HTMLTextAreaElement).value).toBe("Unsaved local edit");
    expect(dirty).toHaveBeenLastCalledWith(true);
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Close dialog")?.click(); });
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Discard")?.click(); await Promise.resolve(); });
    expect(node.querySelector("textarea")).toBeNull();
    expect(document.activeElement?.textContent).toBe("Workspace opener");
    expect(dirty).toHaveBeenLastCalledWith(false);
  });

  it("preserves dirty text when its saved-letter query updates", async () => {
    const client = await render();
    await edit("Unsaved local edit");
    await act(async () => { client.setQueryData(["cover-letter-by-job", 7], { id: 2, job_id: 7, content: "New server text", tone: null, model: "fixture", is_edited: true, created_at: "2026-01-01", updated_at: "2026-01-03" }); });
    expect((node.querySelector("textarea") as HTMLTextAreaElement).value).toBe("Unsaved local edit");
    expect(dirty).toHaveBeenLastCalledWith(true);
  });

  it("leaves the dialog and local dirty text open after a failed save", async () => {
    apiMocks.update.mockRejectedValueOnce(new Error("offline"));
    await render();
    await edit("Unsaved local edit");
    (node.querySelector("textarea") as HTMLTextAreaElement).focus();
    await act(async () => { Array.from(node.querySelectorAll("button")).find((button) => button.textContent === "Save edits")?.click(); });
    await vi.waitFor(() => expect(apiMocks.update).toHaveBeenCalledWith(2, "Unsaved local edit"));
    expect((node.querySelector("textarea") as HTMLTextAreaElement).value).toBe("Unsaved local edit");
    expect(dirty).toHaveBeenLastCalledWith(true);
    expect(node.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(document.body);
  });
});
