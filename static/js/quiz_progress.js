/* Entry point for the generation progress screen.
 *
 * This file only wires a component to the live DOM (composition
 * root); the behaviour lives in modules/GenerationPoller.js. The
 * status URL is passed as data, not string-interpolated into a
 * script, so the template stays free of JavaScript.
 */
import GenerationPoller from "./modules/GenerationPoller.js";

const card = document.getElementById("progress-card");

new GenerationPoller({ statusUrl: card.dataset.statusUrl }).start();
