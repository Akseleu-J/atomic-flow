/**
 * Typed client for `af serve` — the atomic-flow self-host API.
 * Zero dependencies; fetch is injected so tests can mock the transport.
 * The server's authority rules live in the kernel; this client only speaks
 * the wire contract and surfaces holds/errors as typed results.
 */

export interface AfClientOptions {
  baseUrl: string;
  /** Bearer token; required when the server was started with --token. */
  token?: string;
  /** Transport injection for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface SessionCreated {
  ok: true;
  session_id: string;
  path: string;
}

export interface SessionSnapshot {
  ok: true;
  session_id: string;
  events: number;
  incomplete_tail: boolean;
  pending_call_ids: string[];
  unresolved_call_ids: string[];
}

export interface JourneysReport {
  ok: boolean;
  verified: boolean;
  refusal: unknown;
  session_id: string;
  edits: number;
}

export interface JobsStatus {
  ok: true;
  jobs: Array<Record<string, unknown>>;
  unresolved_unknown_ids: string[];
}

export class AfHttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(`af serve ${status}: ${message}`);
    this.name = "AfHttpError";
  }
}

export class AfClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly fetchImpl: typeof fetch;

  constructor(options: AfClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.headers = options.token ? { Authorization: `Bearer ${options.token}` } : {};
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  private async request(path: string, init?: RequestInit): Promise<Response> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...this.headers, ...init?.headers },
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new AfHttpError(response.status, text || response.statusText);
    }
    return response;
  }

  async health(): Promise<void> {
    await this.request("/health");
  }

  async createSession(id?: string, project?: string): Promise<SessionCreated> {
    const response = await this.request("/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ id, project }),
    });
    return (await response.json()) as SessionCreated;
  }

  async appendEvent(
    sessionId: string,
    eventType: string,
    payload: unknown,
    callId?: string,
    outcome?: string,
  ): Promise<Record<string, unknown>> {
    const response = await this.request(`/v1/sessions/${encodeURIComponent(sessionId)}/events`, {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, payload, call_id: callId, outcome }),
    });
    return (await response.json()) as Record<string, unknown>;
  }

  async readSession(sessionId: string): Promise<SessionSnapshot> {
    const response = await this.request(`/v1/sessions/${encodeURIComponent(sessionId)}`);
    return (await response.json()) as SessionSnapshot;
  }

  async runEditJourney(input: {
    backend: { base_url: string; api_key_env: string; model: string };
    root_id: string;
    file: string;
    replacement: string;
    goal: string;
    session_id?: string;
    host_approved: true;
  }): Promise<JourneysReport> {
    const response = await this.request("/v1/edit-journeys", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return (await response.json()) as JourneysReport;
  }

  async jobStatus(store: string): Promise<JobsStatus> {
    const response = await this.request(`/v1/jobs?store=${encodeURIComponent(store)}`);
    return (await response.json()) as JobsStatus;
  }
}
