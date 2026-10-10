/* DeleteToggle — one responsibility: surface that a question is
 * marked for deletion on the edit screen.
 *
 * Ticking the delete box makes the whole card look obviously
 * greyed out. The class knows nothing about form submission; it
 * only toggles state classes on the card around each checkbox.
 */
export default class DeleteToggle {
  constructor({ root = document, selector = "[data-delete-toggle] input" } = {}) {
    this.root = root;
    this.selector = selector;
  }

  init() {
    this.root.querySelectorAll(this.selector).forEach((box) => {
      box.addEventListener("change", () => this.#apply(box, box.checked));
    });
    return this;
  }

  #apply(box, checked) {
    const card = box.closest("[data-question-card]");
    if (!card) {
      return;
    }
    const note = card.querySelector("[data-deleted-note]");
    if (!note) {
      return;
    }
    card.classList.toggle("border-danger", checked);
    card.classList.toggle("opacity-50", checked);
    note.classList.toggle("d-none", !checked);
  }
}