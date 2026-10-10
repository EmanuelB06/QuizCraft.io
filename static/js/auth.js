/* Entry point for the login and signup pages.
 *
 * This file only wires a component to the live DOM (composition
 * root); the behaviour lives in modules/PasswordReveal.js. Loaded
 * as a <script type="module">, so it is deferred automatically and
 * the fields work whether or not it runs.
 */
import PasswordReveal from "./modules/PasswordReveal.js";

new PasswordReveal().init();
