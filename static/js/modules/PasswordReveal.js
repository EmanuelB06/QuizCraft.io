/* PasswordReveal — one responsibility: toggle a password field's
 * visibility, keeping the button's icon and ARIA state in sync.
 *
 * Dependencies are injected: the root node to search and the icon
 * class names, so the same class serves every form and can be
 * tested with any DOM. The field to flip is addressed through the
 * button's data-reveal attribute (a field name).
 */
export default class PasswordReveal {
  constructor({
    root = document,
    showIconClass = "bi-eye",
    hideIconClass = "bi-eye-slash",
  } = {}) {
    this.root = root;
    this.showIconClass = showIconClass;
    this.hideIconClass = hideIconClass;
  }

  init() {
    this.root.querySelectorAll("[data-reveal]").forEach((button) => {
      const field = this.root.querySelector(`[name="${button.dataset.reveal}"]`);
      if (!field) {
        return;
      }
      button.addEventListener("click", () => this.#toggle(button, field));
    });
    return this;
  }

  #toggle(button, field) {
    const shown = field.type === "password";
    field.type = shown ? "text" : "password";
    this.#render(button, shown);
    field.focus();
  }

  #render(button, shown) {
    const icon = button.querySelector("i");
    if (icon) {
      icon.classList.toggle(this.showIconClass, !shown);
      icon.classList.toggle(this.hideIconClass, shown);
    }
    button.setAttribute("aria-pressed", shown ? "true" : "false");
    button.setAttribute("aria-label", shown ? "Hide password" : "Show password");
  }
}