// Thin fetch wrapper around the Nimbus API.

export interface ShortenResponse {
  code: string;
}

export interface Stats {
  code: string;
  clicks: number;
}

export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  async shorten(url: string): Promise<ShortenResponse> {
    const res = await fetch(`${this.baseUrl}/shorten`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error(`shorten failed: ${res.status}`);
    return (await res.json()) as ShortenResponse;
  }

  async stats(code: string): Promise<Stats> {
    const res = await fetch(`${this.baseUrl}/api/stats/${code}`);
    if (!res.ok) throw new Error(`stats failed: ${res.status}`);
    return (await res.json()) as Stats;
  }
}
