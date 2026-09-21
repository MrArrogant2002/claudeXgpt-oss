// A minimal "paste a URL, get a short code" form.
import { ApiClient } from "../api-client";
import { shortUrl } from "../util/format.js";

export class LinkForm {
  readonly el: HTMLElement;
  private input: HTMLInputElement;
  private output: HTMLElement;

  constructor(private readonly client: ApiClient, private readonly base = "http://nmb.us") {
    this.el = document.createElement("form");
    this.input = document.createElement("input");
    this.input.placeholder = "https://…";
    this.output = document.createElement("p");

    this.el.appendChild(this.input);
    this.el.appendChild(this.output);
    this.el.addEventListener("submit", (e) => this.onSubmit(e));
  }

  private async onSubmit(e: Event): Promise<void> {
    e.preventDefault();
    try {
      const { code } = await this.client.shorten(this.input.value);
      this.output.textContent = shortUrl(this.base, code);
    } catch (err) {
      this.output.textContent = `error: ${(err as Error).message}`;
    }
  }
}
