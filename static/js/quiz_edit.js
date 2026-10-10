/* Entry point for the review/edit screen.
 *
 * This file only wires components to the live DOM (composition
 * root); the behaviour lives under modules/. Loaded as a
 * <script type="module">, so it is deferred automatically.
 */
import DeleteToggle from "./modules/DeleteToggle.js";
import AnswerKeyToggle from "./modules/AnswerKeyToggle.js";
import AnswerKeyMask from "./modules/AnswerKeyMask.js";

new DeleteToggle().init();
new AnswerKeyToggle().init();
new AnswerKeyMask().init();