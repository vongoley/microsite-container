(() => {
  "use strict";

  const CLIENT_VERSION = "20260908-1";
  const status = document.getElementById("runtimeStatus");
  document.documentElement.dataset.runtimeClientVersion = CLIENT_VERSION;

  function showFallback(message) {
    if (!status) return;
    status.textContent = `数据状态：${message}，显示内嵌快照`;
    status.classList.remove("live");
    status.classList.add("stale");
  }

  async function readPublicDocumentDirectly() {
    const endpoint = `/api/runtime/sites/china-bank-drawdown/documents/latest-analysis?client=${CLIENT_VERSION}&_=${Date.now()}`;
    const response = await fetch(endpoint, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {Accept: "application/json"},
    });
    if (!response.ok) throw new Error(`Runtime API ${response.status}`);
    return response.json();
  }

  async function loadLatestAnalysis() {
    if (typeof window.applyRuntimeAnalysis !== "function") {
      showFallback("报告数据绑定组件不可用");
      return;
    }

    let stage = "request";
    try {
      status.textContent = `数据状态：正在读取服务器数据 · client ${CLIENT_VERSION}`;
      const payload = await readPublicDocumentDirectly();
      stage = "apply";
      window.applyRuntimeAnalysis(payload.value, {revision: payload.revision});
    } catch (error) {
      const safeDetail = error && error.message ? error.message.slice(0, 80) : "unknown error";
      const label = stage === "request" ? "服务器接口请求失败" : "服务器数据应用失败";
      showFallback(`${label}（${safeDetail} · client ${CLIENT_VERSION}）`);
      console.warn("Runtime Data unavailable; using embedded snapshot.", error);
    }
  }

  loadLatestAnalysis();
})();
