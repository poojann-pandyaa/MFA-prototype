import { next, start } from "../actions.js";
import { state } from "../app.js";
import { button, field, h, note, topbar } from "../ui.js";

export function render(root) {
  const me = state.me;
  const which = h("select", { class: "input" }, h("option", { value: "phone", text: "Mobile number" }), h("option", { value: "email", text: "Email" }));
  const value = h("input", { class: "input", autocomplete: "off", maxlength: "254" });
  const error = h("p", { class: "form-error", role: "alert" });
  const submit = button("Continue", { type: "submit" });
  function hint() {
    const phone = which.value === "phone";
    value.type = phone ? "tel" : "email";
    value.setAttribute("inputmode", phone ? "numeric" : "email");
    value.placeholder = phone ? "98123 45678" : "you@example.com";
  }
  which.addEventListener("change", hint);
  hint();
  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "What do you want to change?", id: "ct-which", input: which }),
    field({ label: "New value", id: "ct-value", input: value }),
    note("warn", "warn", h("strong", { text: "Only change this yourself." }), "No one from the bank will ever ask you to change your mobile number or email."),
    error, submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone = which.value === "phone";
    const raw = value.value.trim();
    const v = phone ? "+91" + raw.replace(/\D/g, "").slice(-10) : raw;
    const ok = phone ? /^[6-9][0-9]{9}$/.test(raw.replace(/\D/g, "").slice(-10)) : /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(raw);
    if (!ok) { error.textContent = phone ? "Enter a 10-digit mobile number." : "Enter an email address like you@example.com."; return; }
    submit.disabled = true;
    error.textContent = "";
    try {
      next(await start("contact", { field: which.value, value: v }));
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  root.appendChild(h("div", { class: "page" }, topbar({ title: "Mobile and email", back: () => history.back() }),
    h("p", { class: "lede", text: `Mobile ${me.phone ? "+91 " + me.phone : "not set"} · Email ${me.email || "not set"}` }), form));
}
