import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, downloadExport, request, uploadFile, api } from "./client";
import { demoExtension, demoFile, demoKey } from "./demoKey";
import { DEMO_MESSAGE } from "../config";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("demoKey", () => {
  it("names a path without the api prefix", () => {
    expect(demoKey("GET", "/api/runs/12/artifact")).toBe("runs__12__artifact");
    expect(demoKey("get", "/api/workspace")).toBe("workspace");
  });
  it("is stable under query order and changes with query content", () => {
    const a = demoKey("GET", "/api/runs/3/claims?sort=date&dir=asc");
    expect(a).toBe(demoKey("GET", "/api/runs/3/claims?dir=asc&sort=date"));
    expect(a).not.toBe(demoKey("GET", "/api/runs/3/claims?sort=date&dir=desc"));
    expect(a).toMatch(/^runs__3__claims~[0-9a-f]{8}$/);
    expect(demoKey("GET", "/api/runs/3/claims")).toBe("runs__3__claims");
  });
  it("sanitizes unsafe characters and prefixes non-GET methods", () => {
    expect(demoKey("GET", "/api/templates/a b%20c")).toBe("templates__a_b_20c");
    expect(demoKey("POST", "/api/runs")).toBe("post__runs");
  });
  it("picks the file extension", () => {
    expect(demoExtension("/api/documents/4/file")).toBe("pdf");
    expect(demoExtension("/api/documents/4/sheet-pages/2")).toBe("pdf");
    expect(demoExtension("/api/certificates/9/issues/1/pdf")).toBe("pdf");
    expect(demoExtension("/api/documents/4/original")).toBe("bin");
    expect(demoExtension("/api/documents/4/sheets")).toBe("json");
    expect(demoFile("GET", "/api/runs?limit=20")).toMatch(/^runs~[0-9a-f]{8}\.json$/);
  });
});

describe("request() outside demo mode", () => {
  it("calls the API base, not /demo", async () => {
    vi.stubEnv("VITE_DEMO", "");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/api/workspace")).resolves.toEqual({ ok: 1 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8000/api/workspace");
    expect(api.fileUrl(5)).toBe("http://127.0.0.1:8000/api/documents/5/file");
  });
  it("sends writes to the API", async () => {
    vi.stubEnv("VITE_DEMO", "");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await request("/api/tasks", { method: "POST", body: "{}" });
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
  });
});

describe("request() in demo mode", () => {
  const json = (body: unknown, init: ResponseInit = {}) =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" }, ...init });

  it("serves a GET from the recorded file", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    const fetchMock = vi.fn().mockResolvedValue(json({ runs: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/api/runs?limit=20")).resolves.toEqual({ runs: [] });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/demo/${demoFile("GET", "/api/runs?limit=20")}`);
  });
  it("maps binary URLs to recorded files", () => {
    vi.stubEnv("VITE_DEMO", "1");
    expect(api.fileUrl(5)).toBe("/demo/documents__5__file.pdf");
    expect(api.originalUrl(5)).toBe("/demo/documents__5__original.bin");
    expect(api.certificatePdfUrl(9, 2)).toBe("/demo/certificates__9__issues__2__pdf.pdf");
  });
  it("treats a missing snapshot as not found (also when the host answers with HTML)", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html></html>", { status: 200, headers: { "Content-Type": "text/html" } })));
    await expect(request("/api/runs/99")).rejects.toMatchObject({ status: 404, code: "not_found" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));
    await expect(request("/api/runs/99")).rejects.toBeInstanceOf(ApiError);
  });
  it.each(["POST", "PATCH", "PUT", "DELETE"])("rejects %s with the read-only message and never fetches", async (method) => {
    vi.stubEnv("VITE_DEMO", "1");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const err = await request("/api/tasks", { method, body: "{}" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("This is a read-only demo. Live uploads are coming soon.");
    expect((err as ApiError).message).toBe(DEMO_MESSAGE);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("refuses uploads and exports with the same message", async () => {
    vi.stubEnv("VITE_DEMO", "1");
    await expect(uploadFile(new File(["x"], "a.pdf"), () => {})).rejects.toMatchObject({ message: DEMO_MESSAGE });
    await expect(downloadExport(1, "pdf", 1)).rejects.toMatchObject({ message: DEMO_MESSAGE });
    await expect(api.startRun([1, 2])).rejects.toMatchObject({ message: DEMO_MESSAGE });
    await expect(api.ask(1, "hi")).rejects.toMatchObject({ message: DEMO_MESSAGE });
  });
});
