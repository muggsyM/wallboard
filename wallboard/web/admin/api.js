// JSON fetch helper shared by the admin pages (settings, photos). Every write carries
// X-Requested-With: wallboard (the server rejects admin writes without it); a 401 sends the
// browser to the login page and back to the current page afterwards.

export const CSRF = { "X-Requested-With": "wallboard" };

export class ApiError extends Error {
  constructor(status, data) {
    const detail = data?.detail;
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.errors = Array.isArray(detail) ? detail : null;
  }
}

// `keepalive` lets a request finish after the page is closed (used for delayed deletes);
// `headers` adds request headers (the settings page sends If-Match with a config save).
export async function api(method, path, body, { keepalive = false, headers: extra = {} } = {}) {
  const headers = method === "GET" ? { ...extra } : { ...extra, ...CSRF };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    keepalive,
  });
  if (res.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new ApiError(401, null);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}
