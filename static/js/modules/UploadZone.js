/* UploadZone — one responsibility: wire the click/drop upload zone
 * to its (visually hidden) file input.
 *
 * The hidden <input type="file"> stays the single source of truth;
 * this class only mirrors the chosen filename into the zone, adds
 * drag-over feedback and copies a dropped FileList onto the input
 * before letting the normal change handling take over.
 *
 * Progressive enhancement: without it the form still posts, the
 * zone just has no filename mirroring. Everything it needs is
 * injected (input, zone, state classes), so it has no knowledge
 * of the surrounding page.
 */
export default class UploadZone {
  constructor({
    input,
    zone,
    nameSelector = "[data-upload-name]",
    chosenClass = "qc-upload-chosen",
    dragClass = "qc-upload-drag",
  }) {
    this.input = input;
    this.zone = zone;
    this.nameSelector = nameSelector;
    this.chosenClass = chosenClass;
    this.dragClass = dragClass;
  }

  init() {
    if (!this.input || !this.zone) {
      return this;
    }
    this.nameOut = this.zone.querySelector(this.nameSelector);
    this.prompt = this.nameOut ? this.nameOut.textContent : "";
    this.#bindChange();
    this.#bindDrag();
    this.#bindDrop();
    this.#bindKeyboard();
    this.#showName();
    return this;
  }

  #bindChange() {
    this.input.addEventListener("change", () => this.#showName());
  }

  #bindDrag() {
    ["dragenter", "dragover"].forEach((type) => {
      this.zone.addEventListener(type, (event) => {
        this.#stop(event);
        this.zone.classList.add(this.dragClass);
      });
    });
    ["dragleave", "dragend", "drop"].forEach((type) => {
      this.zone.addEventListener(type, (event) => {
        this.#stop(event);
        this.zone.classList.remove(this.dragClass);
      });
    });
  }

  #bindDrop() {
    this.zone.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (!files || !files.length) {
        return;
      }
      try {
        this.input.files = files;
      } catch (error) {
        return; // Assignment unsupported; the click-to-choose path still works.
      }
      this.input.dispatchEvent(new Event("change"));
    });
  }

  // A <label for> opens the picker on click, but not from the keyboard,
  // so the zone also activates the input on Enter/Space.
  #bindKeyboard() {
    this.zone.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      this.input.click();
    });
  }

  #showName() {
    if (!this.nameOut) {
      return;
    }
    const file = this.input.files && this.input.files[0];
    this.nameOut.textContent = file ? file.name : this.prompt;
    this.zone.classList.toggle(this.chosenClass, Boolean(file));
  }

  #stop(event) {
    event.preventDefault();
    event.stopPropagation();
  }
}