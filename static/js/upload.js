/* Entry point for the new-quiz upload form.
 *
 * This file only wires a component to the live DOM (composition
 * root); the behaviour lives in modules/UploadZone.js. Loaded as a
 * <script type="module">, so it is deferred automatically and the
 * form works whether or not it runs.
 */
import UploadZone from "./modules/UploadZone.js";

new UploadZone({
  input: document.getElementById("id_document"),
  zone: document.querySelector(".qc-upload-zone"),
}).init();
