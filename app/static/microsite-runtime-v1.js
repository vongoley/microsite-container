(() => {
  "use strict";

  const shareToken = window.location.pathname.match(/^\/share\/([A-Za-z0-9_-]+)\//)?.[1];
  const DOCUMENT_KEY_RE = /^[a-z0-9][a-z0-9._-]{0,127}$/;

  class MicrositeDataError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = "MicrositeDataError";
      this.status = options.status || 0;
      this.detail = options.detail;
    }
  }

  class MicrositeDataConflictError extends MicrositeDataError {
    constructor(message, options = {}) {
      super(message, options);
      this.name = "MicrositeDataConflictError";
      this.currentRevision = options.currentRevision;
      this.expectedRevision = options.expectedRevision;
    }
  }

  function detectSiteSlug(pathname = window.location.pathname) {
    const match = pathname.match(/^\/sites\/([^/]+)(?:\/|$)/);
    if (!match) {
      throw new MicrositeDataError(
        "Cannot infer the site slug outside /sites/{slug}/; pass { site } explicitly."
      );
    }
    return decodeURIComponent(match[1]);
  }

  function detailMessage(detail, fallback) {
    if (typeof detail === "string") return detail;
    if (detail && typeof detail.message === "string") return detail.message;
    return fallback;
  }

  function loginUrl() {
    return `/admin/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  }

  let redirectingToLogin = false;
  function redirectToLogin(destination) {
    if (redirectingToLogin) return;
    redirectingToLogin = true;
    window.dispatchEvent(new CustomEvent("microsite:before-login"));
    window.location.assign(typeof destination === "string" ? destination : loginUrl());
  }

  async function readResponse(response) {
    const payload = await response.json().catch(() => null);
    if (response.ok) return payload;
    if (response.status === 401) redirectToLogin();
    const detail = payload?.detail;
    const message = detailMessage(detail, `Runtime data request failed (${response.status})`);
    if (response.status === 409) {
      throw new MicrositeDataConflictError(message, {
        status: response.status,
        detail,
        currentRevision: detail?.currentRevision,
        expectedRevision: detail?.expectedRevision,
      });
    }
    throw new MicrositeDataError(message, { status: response.status, detail });
  }

  function document(documentKey, options = {}) {
    if (!DOCUMENT_KEY_RE.test(documentKey)) {
      throw new MicrositeDataError(`Invalid runtime document key: ${documentKey}`);
    }
    const site = options.site || detectSiteSlug();
    const endpoint = `/api/runtime/sites/${encodeURIComponent(site)}/documents/${encodeURIComponent(documentKey)}`;
    const draftKey = `microsite-runtime:${site}:${documentKey}:draft:v1`;
    const recoveryKey = `${draftKey}:login-recovery`;
    let revision = null;
    let value;

    function loadDraft() {
      try {
        const raw = window.localStorage.getItem(draftKey) || window.localStorage.getItem(recoveryKey);
        return raw ? JSON.parse(raw) : null;
      } catch (error) {
        throw new MicrositeDataError(`Cannot read local draft: ${error.message}`);
      }
    }

    function saveDraft(draftValue, baseRevision = revision ?? 0) {
      const draft = {
        value: draftValue,
        baseRevision,
        savedAt: new Date().toISOString(),
      };
      try {
        window.localStorage.setItem(draftKey, JSON.stringify(draft));
      } catch (error) {
        throw new MicrositeDataError(`Cannot save local draft: ${error.message}`);
      }
      return draft;
    }

    function clearDraft() {
      try {
        window.localStorage.removeItem(draftKey);
        return true;
      } catch (_error) {
        return false;
      }
    }

    return {
      site,
      key: documentKey,
      get revision() {
        return revision;
      },
      get value() {
        return value;
      },
      async get() {
        const response = await fetch(endpoint, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json", ...(shareToken ? {"X-Microsite-Share": shareToken} : {}) },
        });
        const payload = await readResponse(response);
        revision = payload.revision;
        value = payload.value;
        return payload;
      },
      async save(nextValue, saveOptions = {}) {
        const expectedRevision = saveOptions.revision ?? revision;
        if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
          throw new MicrositeDataError(
            "Load the document before saving, or pass a non-negative revision."
          );
        }
        const response = await fetch(endpoint, {
          method: "PUT",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "If-Match": `"rev-${expectedRevision}"`,
          },
          body: JSON.stringify({ value: nextValue }),
        });
        if (response.status === 401) {
          // Separate recovery storage survives older clients clearing their draft on error.
          window.localStorage.setItem(recoveryKey, JSON.stringify({
            value: nextValue, baseRevision: expectedRevision, savedAt: new Date().toISOString(),
          }));
        }
        const payload = await readResponse(response);
        revision = payload.revision;
        value = payload.value;
        window.localStorage.removeItem(recoveryKey);
        clearDraft();
        return payload;
      },
      loadDraft,
      saveDraft,
      clearDraft,
    };
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    const url = new URL(typeof args[0] === "string" ? args[0] : args[0]?.url || args[0], window.location.href);
    if (response.status === 401 && url.origin === window.location.origin) {
      // Let the caller persist the submitted draft before navigation.
      window.setTimeout(redirectToLogin, 0);
    }
    return response;
  };
  let sessionCheck = null;
  function checkBrowserSession() {
    if (shareToken || redirectingToLogin || sessionCheck) return sessionCheck;
    sessionCheck = originalFetch("/api/session?site=" + encodeURIComponent(window.document.body.dataset.siteSlug || window.location.pathname.match(/^\/sites\/([^/]+)/)?.[1] || ""), {credentials: "same-origin", cache: "no-store"})
      .then(async response => {
        if (response.ok) {
          const session = await response.json();
          if (session.login_required) redirectToLogin(session.redirect_url);
        }
      }).catch(() => {}).finally(() => { sessionCheck = null; });
    return sessionCheck;
  }
  window.addEventListener("pageshow", checkBrowserSession);
  window.addEventListener("focus", checkBrowserSession);
  window.document.addEventListener("click", checkBrowserSession, true);
  window.document.addEventListener("change", checkBrowserSession, true);
  window.document.addEventListener("keydown", checkBrowserSession, true);
  checkBrowserSession();

  window.MicrositeData = Object.freeze({
    version: 1,
    document,
    detectSiteSlug,
    loginUrl,
    Error: MicrositeDataError,
    ConflictError: MicrositeDataConflictError,
  });
})();
