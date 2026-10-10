/* AnswerKeyToggle — one responsibility: show/hide the answer key on
 * the review screen without reloading the page.
 *
 * Toggling is a class swap on the form (qc-key-hidden), so nothing is
 * sent to the server and no unsaved edits are lost. The control is an
 * anchor with a working ?answers= query href for the no-JS fallback;
 * when this module runs it intercepts the click and flips in place,
 * keeping the label, icon, aria-expanded and href in sync.
 */
export default class AnswerKeyToggle {
  constructor({
    root = document,
    trigger = "[data-answer-key-toggle]",
    form = "#question-form",
    label = "[data-answer-key-label]",
    hiddenClass = "qc-key-hidden",
    iconShow = "bi-eye",
    iconHide = "bi-eye-slash",
  } = {}) {
    this.root = root;
    this.triggerSelector = trigger;
    this.formSelector = form;
    this.labelSelector = label;
    this.hiddenClass = hiddenClass;
    this.iconShow = iconShow;
    this.iconHide = iconHide;
  }

  init() {
    this.trigger = this.root.querySelector(this.triggerSelector);
    this.form = this.root.querySelector(this.formSelector);
    if (!this.trigger || !this.form) {
      return this;
    }
    this.label = this.trigger.querySelector(this.labelSelector);
    this.icon = this.trigger.querySelector("i");
    this.trigger.addEventListener("click", (event) => this.#click(event));
    return this;
  }

  #click(event) {
    event.preventDefault();
    const hidden = this.form.classList.toggle(this.hiddenClass);
    this.#render(hidden);
  }

  #render(hidden) {
    if (this.icon) {
      this.icon.classList.toggle(this.iconShow, !hidden);
      this.icon.classList.toggle(this.iconHide, hidden);
    }
    if (this.label) {
      this.label.textContent = hidden ? "Show answers" : "Hide answers";
    }
    this.trigger.setAttribute("aria-expanded", hidden ? "false" : "true");
    this.trigger.href = this.#href(hidden ? 1 : 0);
  }

  #href(answers) {
    const url = new URL(this.trigger.href, window.location.href);
    url.searchParams.set("answers", String(answers));
    return url.pathname + url.search;
  }
}