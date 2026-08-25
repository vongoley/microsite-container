(() => {
  "use strict";

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

  async function readResponse(response) {
    const payload = await response.json().catch(() => null);
    if (response.ok) return payload;
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
    let revision = null;
    let value;

    function loadDraft() {
      try {
        const raw = window.localStorage.getItem(draftKey);
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
          headers: { Accept: "application/json" },
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
        const payload = await readResponse(response);
        revision = payload.revision;
        value = payload.value;
        clearDraft();
        return payload;
      },
      loadDraft,
      saveDraft,
      clearDraft,
    };
  }

  window.MicrositeData = Object.freeze({
    version: 1,
    document,
    detectSiteSlug,
    Error: MicrositeDataError,
    ConflictError: MicrositeDataConflictError,
  });
})();
