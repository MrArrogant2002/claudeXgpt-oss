// Shows the click count for a short code, formatted for humans.
import { ApiClient } from "../api-client";
import { formatCount } from "../util/format.js";

export class StatsPanel {
  readonly el: HTMLElement;

  constructor(private readonly client: ApiClient) {
    this.el = document.createElement("section");
    this.el.className = "stats";
  }

  async refresh(code: string): Promise<void> {
    const stats = await this.client.stats(code);
    this.el.textContent = `${code}: ${formatCount(stats.clicks)} clicks`;
  }
}
