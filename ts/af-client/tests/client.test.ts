import { describe, expect, it } from "vitest";
import { AfClient, AfHttpError } from "../src/index.js";

function clientWith(responses: Array<{ status: number; body: unknown }>): AfClient {
  let call = 0;
  const fetchImpl = (async () => {
    const response = responses[call] ?? { status: 500, body: "unexpected" };
    call += 1;
    return new Response(JSON.stringify(response.body), {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return new AfClient({ baseUrl: "http://127.0.0.1:8787", fetchImpl });
}

describe("AfClient", () => {
  it("sends bearer token when configured", async () => {
    let captured: Record<string, string> = {};
    const fetchImpl = (async (_url: unknown, init?: RequestInit) => {
      captured = Object.fromEntries(new Headers(init?.headers).entries());
      return new Response("{}", { status: 200 });
    }) as typeof fetch;
    const client = new AfClient({
      baseUrl: "http://127.0.0.1:8787",
      token: "s3cret",
      fetchImpl,
    });
    await client.health();
    expect(captured["Authorization"]).toBe("Bearer s3cret");
  });

  it("creates sessions and reads snapshots", async () => {
    const client = clientWith([
      { status: 200, body: { ok: true, session_id: "s1", path: "p" } },
      {
        status: 200,
        body: {
          ok: true,
          session_id: "s1",
          events: 2,
          incomplete_tail: false,
          pending_call_ids: [],
          unresolved_call_ids: [],
        },
      },
    ]);
    const created = await client.createSession("s1");
    expect(created.session_id).toBe("s1");
    const snapshot = await client.readSession("s1");
    expect(snapshot.events).toBe(2);
    expect(snapshot.unresolved_call_ids).toEqual([]);
  });

  it("surfaces HTTP errors as AfHttpError", async () => {
    const client = clientWith([{ status: 401, body: "unauthorized" }]);
    await expect(client.readSession("nope")).rejects.toThrow(AfHttpError);
  });

  it("job status carries unresolved holds", async () => {
    const client = clientWith([
      { status: 200, body: { ok: true, jobs: [], unresolved_unknown_ids: ["j1"] } },
    ]);
    const status = await client.jobStatus("/store");
    expect(status.unresolved_unknown_ids).toEqual(["j1"]);
  });
});
