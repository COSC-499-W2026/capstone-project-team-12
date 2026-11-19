// Calls the real backend under /api/v1; Vite forwards /api to it, so the page and the API share one origin.
const BASE = "/api/v1";
const TOKEN_KEY = "hunt-demo-token";

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || [];
  }
}

let token = read(TOKEN_KEY);
let onSignedOut = () => {};

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: the token then lives only in memory
  }
}

// One refresh at a time; the refresh token is the HttpOnly cookie the login set (spec, Tokens)
let refreshing = null;

function refreshToken() {
  refreshing ??= fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" })
    .then((response) => (response.ok ? response.json() : null))
    .then((result) => {
      if (result) setToken(result.access_token);
      return Boolean(result);
    })
    .finally(() => (refreshing = null));
  return refreshing;
}

async function request(method, path, { json, form, body, raw, retried } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload = body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  } else if (form) {
    payload = new URLSearchParams(form);
  }
  const response = await fetch(BASE + path, { method, headers, body: payload, credentials: "same-origin" });
  if (response.ok) {
    if (raw) return response;
    if (response.status === 204) return null;
    return response.json();
  }
  let error = { code: "UNKNOWN", message: response.statusText, details: [] };
  try {
    error = (await response.json()).error || error;
  } catch {
    // not the standard error body
  }
  if (response.status === 401 && !path.startsWith("/auth/")) {
    if (!retried && (await refreshToken())) {
      return request(method, path, { json, form, body, raw, retried: true });
    }
    setToken(null);
    onSignedOut();
  }
  throw new ApiError(response.status, error.code, error.message, error.details);
}

export function setToken(value) {
  token = value;
  write(TOKEN_KEY, value);
}

export function signedIn() {
  return Boolean(token);
}

export function whenSignedOut(callback) {
  onSignedOut = callback;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, json) => request("POST", path, { json }),
  patch: (path, json) => request("PATCH", path, { json }),
  // photo uploads: `formData` holds the file under the field the route expects
  upload: (path, formData) => request("POST", path, { body: formData }),
  download: (path) => request("GET", path, { raw: true }),

  async login(email, password) {
    const result = await request("POST", "/auth/login", { form: { username: email, password } });
    setToken(result.access_token);
  },

  async logout() {
    try {
      await request("POST", "/auth/logout", { json: {} });
    } catch {
      // the local token is dropped either way
    }
    setToken(null);
  },
};
