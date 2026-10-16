import { api } from "../api.js";
import { currentDevice, go, refresh } from "../app.js";
import { button, field, h, icon, topbar } from "../ui.js";

const RULES = {
  name: (v) => v.trim().length >= 2 || "Enter your full name.",
  phone: (v) => /^[6-9][0-9]{9}$/.test(v) || "Enter your 10-digit mobile number.",
  email: (v) => /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(v) || "Enter an email address like you@example.com.",
  username: (v) => /^[a-z0-9][a-z0-9_-]{2,23}$/.test(v) || "3 to 24 lowercase letters, digits, - or _.",
  password: (v) => v.length >= 8 || "Use at least 8 characters.",
};

export function render(root) {
  const inputs = {
    name: h("input", { class: "input", autocomplete: "name", maxlength: "60" }),
    phone: h("input", { type: "tel", inputmode: "numeric", autocomplete: "tel-national", maxlength: "10", placeholder: "98123 45678" }),
    email: h("input", { class: "input", type: "email", inputmode: "email", autocomplete: "email", maxlength: "254",
      autocapitalize: "none", spellcheck: "false", placeholder: "you@example.com" }),
    username: h("input", { class: "input", autocomplete: "username", maxlength: "24", autocapitalize: "none", spellcheck: "false" }),
    password: h("input", { type: "password", autocomplete: "new-password", maxlength: "128" }),
  };
  const errs = Object.fromEntries(Object.keys(inputs).map((k) => [k, h("span", { class: "err", id: `${k}-err`, "aria-live": "polite" })]));
  for (const [k, el] of Object.entries(inputs)) el.setAttribute("aria-describedby", `${k}-err`);
  const show = h("button", { type: "button", class: "link", "aria-label": "Show password", onclick: () => {
    const on = inputs.password.type === "password";
    inputs.password.type = on ? "text" : "password";
    show.replaceChildren(icon(on ? "eyeOff" : "eye", 20));
  } }, icon("eye", 20));
  const formError = h("p", { class: "form-error", role: "alert" });
  const submit = button("Open account", { type: "submit" });

  const form = h("form", { class: "stack", novalidate: true },
    field({ label: "Full name", id: "su-name", input: inputs.name, err: errs.name }),
    field({ label: "Email (we send your one-time codes here)", id: "su-email", input: inputs.email, err: errs.email }),
    field({ label: "Mobile number", id: "su-phone", control: inputs.phone,
      input: h("div", { class: "input-wrap" }, h("span", { class: "prefix", text: "+91" }), inputs.phone), err: errs.phone }),
    field({ label: "Username", id: "su-username", input: inputs.username, err: errs.username }),
    field({ label: "Password", id: "su-password", control: inputs.password,
      input: h("div", { class: "input-wrap" }, inputs.password, show), err: errs.password }),
    formError, submit);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formError.textContent = "";
    const values = { name: inputs.name.value, phone: inputs.phone.value.replace(/\D/g, ""), email: inputs.email.value.trim(),
      username: inputs.username.value.trim().toLowerCase(), password: inputs.password.value };
    let ok = true;
    for (const [k, rule] of Object.entries(RULES)) {
      const r = rule(values[k]);
      errs[k].textContent = r === true ? "" : r;
      if (r !== true) { if (ok) inputs[k].focus(); ok = false; }
    }
    if (!ok) return;
    submit.disabled = true;
    try {
      await api("POST", "/api/register", { name: values.name.trim(), phone: "+91" + values.phone, email: values.email,
        username: values.username,
        password: values.password, deviceId: currentDevice() });
      await refresh();
      go("setup");
    } catch (err) {
      formError.textContent = err.message;
      submit.disabled = false;
    }
  });

  root.appendChild(h("div", { class: "page" },
    topbar({ back: () => go("welcome") }),
    h("h1", { text: "Open an account" }),
    h("p", { class: "lede", text: "It takes about a minute. Use an email you can open on this phone: your one-time codes go there." }),
    form,
    h("p", { class: "muted center" }, "Already a customer? ", h("a", { class: "link", href: "#/signin", text: "Sign in" }))));
  inputs.name.focus();
}
