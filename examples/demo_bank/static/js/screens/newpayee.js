import { next, start } from "../actions.js";
import { button, field, h, note, topbar } from "../ui.js";

export function render(root) {
  const name = h("input", { class: "input", maxlength: "40", autocomplete: "off", autocapitalize: "words" });
  const account = h("input", { class: "input", inputmode: "numeric", maxlength: "18", autocomplete: "off" });
  const again = h("input", { class: "input", inputmode: "numeric", maxlength: "18", autocomplete: "off" });
  const ifsc = h("input", { class: "input", maxlength: "11", autocomplete: "off", autocapitalize: "characters", placeholder: "HDFC0001234" });
  const error = h("p", { class: "form-error", role: "alert" });
  const submit = button("Continue", { type: "submit" });
  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "Name on the account", id: "np-name", input: name }),
    field({ label: "Account number", id: "np-account", input: account }),
    field({ label: "Re-enter account number", id: "np-again", input: again }),
    field({ label: "IFSC", id: "np-ifsc", input: ifsc }),
    note("plain", "info", "New payees always get a quick check, and you can pay them once it's done."),
    error, submit);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const acct = account.value.replace(/\s/g, "");
    const code = ifsc.value.trim().toUpperCase();
    const problem = !/^[A-Za-z][A-Za-z .'&-]{1,39}$/.test(name.value.trim()) ? "Enter the name as it appears on their account."
      : !/^[0-9]{9,18}$/.test(acct) ? "Account numbers are 9 to 18 digits."
        : acct !== again.value.replace(/\s/g, "") ? "The two account numbers don't match."
          : !/^[A-Z]{4}0[A-Z0-9]{6}$/.test(code) ? "IFSC looks like HDFC0001234: 4 letters, a zero, then 6 letters or digits." : "";
    error.textContent = problem;
    if (problem) return;
    submit.disabled = true;
    try {
      next(await start("payee", { name: name.value.trim(), account: acct, ifsc: code }));
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  root.appendChild(h("div", { class: "page" }, topbar({ title: "New payee", back: () => history.back() }), form));
  name.focus();
}
