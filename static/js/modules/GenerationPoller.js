/* GenerationPoller — one responsibility: poll the quiz generation
 * status endpoint and project its state onto the progress page.
 *
 * All of its collaborators are injected: the status URL, the DOM
 * element ids and the rotating status copy. It renders into the
 * elements it is handed, decides between running / ready / failed
 * from the response, and stops polling once a terminal state is
 * reached. It never calls reverse() or knows where an endpoint
 * lives — this module is page-agnostic.
 */
const DEFAULT_WORDS = [
  "Pulling out key ideas",
  "Writing draft questions",
  "Checking the answers",
  "Removing duplicate questions",
  "Balancing difficulty",
  "Almost there",
];

export default class GenerationPoller {
  constructor({
    statusUrl,
    root = document,
    intervalMs = 2000,
    words = DEFAULT_WORDS,
  } = {}) {
    this.statusUrl = statusUrl;
    this.intervalMs = intervalMs;
    this.words = words;
    this.bar = root.getElementById("progress-bar");
    this.label = root.getElementById("progress-label");
    this.statusText = root.getElementById("status-text");
    this.errorText = root.getElementById("error-text");
    this.reviewLink = root.getElementById("review-link");
    this.blocks = ["spinner-block", "done-block", "error-block"].map((id) =>
      root.getElementById(id),
    );
    this.stopped = true;
  }

  start() {
    this.stopped = false;
    this.#poll();
    return this;
  }

  async #poll() {
    if (this.stopped) {
      return;
    }
    try {
      const response = await fetch(this.statusUrl, { headers: { "Accept": "application/json" } });
      if (!response.ok) {
        throw new Error("status " + response.status);
      }
      const data = await response.json();
      if (data.status === "ready") {
        this.#finish(data);
        return;
      }
      if (data.status === "failed") {
        this.#fail(data);
        return;
      }
      this.#applyProgress(data);
    } catch (error) {
      this.statusText.textContent = "Reconnecting...";
    }
    if (!this.stopped) {
      window.setTimeout(() => this.#poll(), this.intervalMs);
    }
  }

  #finish(data) {
    this.stopped = true;
    this.reviewLink.href = data.redirect;
    this.#show("done-block");
    window.location.replace(data.redirect);
  }

  #fail(data) {
    this.stopped = true;
    this.errorText.textContent =
      data.error || "Something went wrong while generating the quiz.";
    this.#show("error-block");
  }

  #applyProgress(data) {
    if (data.total > 0) {
      const percent = Math.max(4, data.percentage);
      this.bar.style.width = percent + "%";
      this.bar.classList.remove("progress-bar-animated");
      this.bar.classList.add("progress-bar-striped");
      this.label.textContent = "Section " + data.done + " of " + data.total;
      this.statusText.textContent = this.words[(data.done - 1) % this.words.length] + "...";
    }
  }

  #show(id) {
    this.blocks.forEach((block) => {
      if (block) {
        block.classList.toggle("d-none", block.id !== id);
      }
    });
  }
}