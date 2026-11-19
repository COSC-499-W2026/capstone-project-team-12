import { bilingual, errorText } from "./i18n.js";

// Builds an element: h("button", { class: "btn", onclick }, "Start")
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function mount(...children) {
  const app = document.getElementById("app");
  app.replaceChildren(...children);
  window.scrollTo(0, 0);
}

let toastTimer;
export function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3500);
}

export function showError(error) {
  const text = errorText(error);
  toast(text.ja ? `${text.ja} / ${text.en}` : text.en);
}

// Two-line prompt: target language, then English
export function bilingualBlock(key, extraClass = "") {
  const text = bilingual(key);
  return h("div", { class: `notice ${extraClass}` }, h("p", { class: "bi-ja" }, text.ja), h("p", { class: "bi-en" }, text.en));
}

// Resolves true when the user confirms (Reset and Quit ask first)
export function confirmBilingual(key) {
  const dialog = document.getElementById("confirm");
  const text = bilingual(key);
  document.getElementById("confirm-ja").textContent = text.ja;
  document.getElementById("confirm-en").textContent = text.en;
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
  });
}

export function formatDate(value) {
  return value ? new Date(value).toLocaleString() : "—";
}

// Analytics rates come from the API as 0 to 100
export function percent(rate) {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate)}%`;
}

// Runs an async action from a button, disabling it meanwhile and showing API errors
export async function busy(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    showError(error);
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}
