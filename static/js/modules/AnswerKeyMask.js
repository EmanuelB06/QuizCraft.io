/* AnswerKeyMask — one responsibility: never mask the creator's own
 * edits on the review screen.
 *
 * While the answer key is hidden, pre-existing answers are masked.
 * A control that differs from what it had at page load gets a
 * data-touched marker, and the CSS only masks untouched controls.
 * That way a checkbox the creator ticks, or a text field they type
 * into, looks unmasked the moment it changes.
 */
export default class AnswerKeyMask {
  constructor({ root = document, selector = "[data-answer-key]" } = {}) {
    this.root = root;
    this.selector = selector;
  }

  init() {
    this.root.querySelectorAll(this.selector).forEach((control) => {
      control.addEventListener("change", () => this.#touch(control));
      control.addEventListener("input", () => this.#touch(control));
    });
    return this;
  }

  #touch(control) {
    control.setAttribute("data-touched", "true");
  }
}