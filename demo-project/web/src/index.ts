// Web entry point: wires the API client into the two UI components.
import { ApiClient } from "./api-client";
import { LinkForm } from "./components/LinkForm";
import { StatsPanel } from "./components/StatsPanel";

const DEFAULT_API_BASE = "http://localhost:8000";

export function mount(root: HTMLElement, apiBase: string = DEFAULT_API_BASE): void {
  const client = new ApiClient(apiBase);
  const form = new LinkForm(client);
  const stats = new StatsPanel(client);
  root.appendChild(form.el);
  root.appendChild(stats.el);
}
