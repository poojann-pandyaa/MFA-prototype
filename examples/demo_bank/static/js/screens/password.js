import { next, start } from "../actions.js";
import { button, field, h, topbar } from "../ui.js";

export function render(root) {
  const current = h("input", { class: "input", type: "password", autocomplete: "current-password", maxlength: "128" });
  const fresh = h("input", { class: "input", type: "password", autocomplete: "new-password", maxlength: "128" });
  const again = h("input", { class: "input", type: "password", autocomplete: "new-password", maxlength: "128" });
  const error = h("p", { class: "form-error", role: "alert" });
  const submit = button("Continue", { type: "submit" });
  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "Current password", id: "pw-current", input: current }),
    field({ label: "New password", id: "pw-new", input: fresh }),
    field({ label: "Type the new password again", id: "pw-again", input: again }),
    error, submit);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const problem = !current.value ? "Enter your current password."
      : fresh.value.length < 8 ? "Use at least 8 characters for the new password."
        : fresh.value !== again.value ? "The two new passwords don't match."
          : fresh.value === current.value ? "Choose a password you haven't used here." : "";
    error.textContent = problem;
    if (problem) return;
    submit.disabled = true;
    try {
      next(await start("password", { current: current.value, new: fresh.value }));
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  root.appendChild(h("div", { class: "page" }, topbar({ title: "Change password", back: () => history.back() }), form));
  current.focus();
}
