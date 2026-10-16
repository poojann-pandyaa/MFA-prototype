import { api } from "../api.js";
import { go, refresh } from "../app.js";
import { button, field, h, passwordInput, topbar } from "../ui.js";

export function render(root) {
  const username = h("input", { class: "input", autocomplete: "username", maxlength: "24", autocapitalize: "none", spellcheck: "false" });
  const pw = passwordInput("current-password");
  const password = pw.input;
  const formError = h("p", { class: "form-error", role: "alert" });
  const submit = button("Sign in", { type: "submit" });
  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "Username", id: "si-username", input: username }),
    field({ label: "Password", id: "si-password", control: password, input: pw.wrap }),
    formError, submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!username.value.trim() || !password.value) { formError.textContent = "Enter your username and password."; return; }
    submit.disabled = true;
    formError.textContent = "";
    try {
      await api("POST", "/api/login", { username: username.value.trim().toLowerCase(), password: password.value });
      await refresh();
      go("home");
    } catch (err) {
      formError.textContent = err.status === 401 ? "That username and password don't match." : err.message;
      submit.disabled = false;
    }
  });
  root.appendChild(h("div", { class: "page" },
    topbar({ back: () => go("welcome") }),
    h("h1", { text: "Welcome back" }),
    h("p", { class: "lede", text: "Sign in to your Demo Bank account." }),
    form,
    h("p", { class: "muted center" }, "New here? ", h("a", { class: "link", href: "#/signup", text: "Open an account" }))));
  username.focus();
}
