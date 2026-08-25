(() => {
  "use strict";

  const DOCUMENT_KEY = "training-plan";
  const THEME_KEY = "training-log-theme";
  const MONTH_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;
  const DATE_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
  const TRAININGS = [
    { id: "chest", label: "胸", hint: "推举与夹胸", symbol: "胸", color: "var(--chest)", soft: "var(--chest-soft)" },
    { id: "back", label: "背", hint: "下拉与划船", symbol: "背", color: "var(--back)", soft: "var(--back-soft)" },
    { id: "legs", label: "大腿", hint: "股四头与腘绳肌", symbol: "腿", color: "var(--legs)", soft: "var(--legs-soft)" },
    { id: "glutes", label: "臀", hint: "臀推与髋部训练", symbol: "臀", color: "var(--glutes)", soft: "var(--glutes-soft)" },
    { id: "shoulders", label: "肩", hint: "推举与侧平举", symbol: "肩", color: "var(--shoulders)", soft: "var(--shoulders-soft)" },
    { id: "biceps", label: "肱二头", hint: "弯举类训练", symbol: "二", color: "var(--biceps)", soft: "var(--biceps-soft)" },
    { id: "triceps", label: "肱三头", hint: "臂屈伸与下压", symbol: "三", color: "var(--triceps)", soft: "var(--triceps-soft)" },
    { id: "core", label: "腹", hint: "核心与腹部", symbol: "腹", color: "var(--core)", soft: "var(--core-soft)" },
    { id: "cardio", label: "心肺", hint: "跑步、骑行等", symbol: "心", color: "var(--cardio)", soft: "var(--cardio-soft)" },
  ];
  const REST = { id: "rest", label: "休息", symbol: "休", color: "var(--rest)", soft: "var(--rest-soft)" };
  const TRAINING_BY_ID = new Map(TRAININGS.map((item) => [item.id, item]));
  const PLAN_IDS = new Set([...TRAINING_BY_ID.keys(), REST.id]);
  const params = new URLSearchParams(window.location.search);
  const isShareView = params.get("view") === "share";
  const siteSlug = detectSiteSlug();

  const elements = {
    body: document.body,
    grid: document.getElementById("calendarGrid"),
    monthTitle: document.getElementById("monthTitle"),
    monthKicker: document.getElementById("monthKicker"),
    monthStats: document.getElementById("monthStats"),
    legend: document.getElementById("legend"),
    prev: document.getElementById("prevMonth"),
    next: document.getElementById("nextMonth"),
    today: document.getElementById("todayButton"),
    share: document.getElementById("shareButton"),
    sharePanel: document.getElementById("sharePanel"),
    shareLink: document.getElementById("shareLink"),
    copyShareLink: document.getElementById("copyShareLink"),
    statusDot: document.getElementById("statusDot"),
    saveIndicator: document.getElementById("saveIndicator"),
    loginLink: document.getElementById("loginLink"),
    modeBadge: document.getElementById("modeBadge"),
    privacyNote: document.getElementById("privacyNote"),
    dialog: document.getElementById("scheduleDialog"),
    form: document.getElementById("scheduleForm"),
    dialogTitle: document.getElementById("dialogTitle"),
    dialogSubtitle: document.getElementById("dialogSubtitle"),
    options: document.getElementById("trainingOptions"),
    close: document.getElementById("closeDialog"),
    cancel: document.getElementById("cancelDialog"),
    clear: document.getElementById("clearDay"),
    save: document.getElementById("saveDay"),
    theme: document.getElementById("themeToggle"),
    themeIcon: document.getElementById("themeIcon"),
    toast: document.getElementById("toast"),
  };

  const now = new Date();
  let viewDate = monthParamToDate(params.get("month")) || new Date(now.getFullYear(), now.getMonth(), 1);
  let plan = {};
  let planDocument = null;
  let activeDateKey = "";
  let draftSelection = new Set();
  let saveInProgress = false;
  let toastTimer = null;

  function detectSiteSlug() {
    const match = window.location.pathname.match(/^\/sites\/([^/]+)(?:\/|$)/);
    return match ? decodeURIComponent(match[1]) : document.body.dataset.siteSlug || "training-log";
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function monthKey(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
  }

  function parseDateKey(key) {
    if (!DATE_RE.test(key)) return null;
    const [year, month, day] = key.split("-").map(Number);
    const date = new Date(year, month - 1, day);
    return dateKey(date) === key ? date : null;
  }

  function monthParamToDate(value) {
    const match = MONTH_RE.exec(value || "");
    return match ? new Date(Number(match[1]), Number(match[2]) - 1, 1) : null;
  }

  function normalizePlan(source) {
    if (!source || typeof source !== "object" || Array.isArray(source)) return {};
    return Object.fromEntries(
      Object.entries(source)
        .filter(([key, value]) => parseDateKey(key) && Array.isArray(value))
        .map(([key, value]) => {
          const unique = [...new Set(value.filter((id) => PLAN_IDS.has(id)))];
          const trainings = unique.filter((id) => id !== REST.id);
          return [key, trainings.length ? trainings : unique.includes(REST.id) ? [REST.id] : []];
        })
        .filter(([, value]) => value.length > 0)
        .sort(([left], [right]) => left.localeCompare(right)),
    );
  }

  function itemForId(id) {
    return id === REST.id ? REST : TRAINING_BY_ID.get(id);
  }

  function trainingStyle(item) {
    return `--training-color:${item.color};--training-soft:${item.soft}`;
  }

  function setStatus(message, state = "neutral") {
    elements.saveIndicator.textContent = message;
    elements.statusDot.classList.toggle("connected", state === "connected");
    elements.statusDot.classList.toggle("error", state === "error");
  }

  function showLoginLink(show) {
    elements.loginLink.hidden = !show || isShareView;
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.add("visible");
    toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    updateThemeButton();
  }

  function updateThemeButton() {
    const dark = document.documentElement.dataset.theme === "dark";
    elements.themeIcon.textContent = dark ? "☀️" : "🌙";
    elements.theme.setAttribute("aria-label", dark ? "切换为浅色主题" : "切换为深色主题");
  }

  function configureViewMode() {
    elements.body.dataset.viewMode = isShareView ? "share" : "edit";
    if (!isShareView) return;
    elements.modeBadge.textContent = "只读分享";
    elements.monthKicker.textContent = "公开训练日志";
    elements.privacyNote.innerHTML = '<span aria-hidden="true">●</span><span><strong>只读访问</strong>：此链接不会授予任何编辑权限，训练数据只能由站点所有者保存。</span>';
  }

  function renderLegend() {
    elements.legend.innerHTML = [...TRAININGS, REST].map((item) => `
      <span class="legend-item" style="${trainingStyle(item)}">
        <span class="legend-dot" aria-hidden="true"></span>${item.label}
      </span>
    `).join("");
  }

  function renderOptions() {
    elements.options.innerHTML = TRAININGS.map((item) => `
      <button
        class="training-option"
        type="button"
        data-training="${item.id}"
        aria-pressed="false"
        style="${trainingStyle(item)}"
      >
        <span class="option-symbol" aria-hidden="true">${item.symbol}</span>
        <span class="option-copy">
          <span class="option-name">${item.label}</span>
          <span class="option-hint">${item.hint}</span>
        </span>
      </button>
    `).join("");
  }

  function renderCalendar() {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const monthStart = new Date(year, month, 1);
    const mondayOffset = (monthStart.getDay() + 6) % 7;
    const gridStart = new Date(year, month, 1 - mondayOffset);
    const todayKey = dateKey(new Date());
    const prefix = `${monthKey(viewDate)}-`;
    const monthEntries = Object.entries(plan).filter(([key]) => key.startsWith(prefix));
    const plannedDays = monthEntries.length;
    const totalItems = monthEntries.reduce(
      (sum, [, ids]) => sum + ids.filter((id) => id !== REST.id).length,
      0,
    );

    elements.monthTitle.textContent = `${year}年${month + 1}月`;
    elements.monthStats.textContent = `本月记录 ${plannedDays} 天 · ${totalItems} 项训练`;
    elements.prev.setAttribute("aria-label", `查看${month === 0 ? year - 1 : year}年${month === 0 ? 12 : month}月`);
    elements.next.setAttribute("aria-label", `查看${month === 11 ? year + 1 : year}年${month === 11 ? 1 : month + 2}月`);

    const cells = [];
    for (let index = 0; index < 42; index += 1) {
      const day = new Date(gridStart);
      day.setDate(gridStart.getDate() + index);
      const key = dateKey(day);
      const items = (plan[key] || []).map(itemForId).filter(Boolean);
      const isRest = items.some((item) => item.id === REST.id);
      const isOutside = day.getMonth() !== month;
      const isToday = key === todayKey;
      const isWeekend = index % 7 >= 5;
      const classNames = ["day-card"];
      if (isOutside) classNames.push("outside");
      if (isToday) classNames.push("today");
      if (isWeekend) classNames.push("weekend");

      const labels = items.map((item) => item.label).join("、");
      const fullDate = `${day.getFullYear()}年${day.getMonth() + 1}月${day.getDate()}日`;
      const weekday = new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(day);
      const ariaLabel = isRest ? `${fullDate}，休息日` : labels ? `${fullDate}，已安排${labels}` : `${fullDate}，未安排训练`;
      const chips = items.map((item) => `
        <span class="training-chip" style="${trainingStyle(item)}">${item.label}</span>
      `).join("");
      const content = `
        <span class="day-date">
          <span class="day-number">${day.getDate()}</span>
          <span class="day-weekday">${weekday}</span>
        </span>
        <span class="chips">${chips || '<span class="empty-hint">未安排</span>'}</span>
      `;

      if (isShareView || !planDocument) {
        cells.push(`<div class="${classNames.join(" ")}" role="gridcell" aria-label="${ariaLabel}">${content}</div>`);
      } else {
        cells.push(`
          <button class="${classNames.join(" ")}" type="button" role="gridcell" data-date="${key}" aria-label="${ariaLabel}">
            ${content}
          </button>
        `);
      }
    }
    elements.grid.innerHTML = cells.join("");
  }

  function updateMonthUrl() {
    if (isShareView) return;
    const url = new URL(window.location.href);
    url.searchParams.set("month", monthKey(viewDate));
    url.searchParams.delete("view");
    history.replaceState(null, "", url);
  }

  function changeMonth(delta) {
    viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() + delta, 1);
    updateMonthUrl();
    renderCalendar();
  }

  function openSchedule(key) {
    if (isShareView || !planDocument || saveInProgress) return;
    const date = parseDateKey(key);
    if (!date) return;
    activeDateKey = key;
    const current = plan[key] || [];
    draftSelection = new Set(current.filter((id) => id !== REST.id));
    const weekday = new Intl.DateTimeFormat("zh-CN", { weekday: "long" }).format(date);
    elements.dialogTitle.textContent = `${date.getMonth() + 1}月${date.getDate()}日 · ${weekday}`;
    elements.dialogSubtitle.textContent = "可多选；不选训练时会保存为休息日";
    updateDraftControls();
    elements.dialog.showModal();
  }

  function updateDraftControls() {
    elements.options.querySelectorAll("[data-training]").forEach((button) => {
      button.setAttribute("aria-pressed", String(draftSelection.has(button.dataset.training)));
    });
    elements.save.textContent = draftSelection.size ? `保存 ${draftSelection.size} 项训练` : "保存休息日";
  }

  async function saveActiveDay() {
    if (!activeDateKey || !planDocument || saveInProgress) return;
    const previousPlan = plan;
    const selected = TRAININGS.map((item) => item.id).filter((id) => draftSelection.has(id));
    const nextPlan = { ...plan, [activeDateKey]: selected.length ? selected : [REST.id] };
    planDocument.saveDraft(nextPlan, planDocument.revision ?? 0);
    plan = normalizePlan(nextPlan);
    saveInProgress = true;
    elements.save.disabled = true;
    setStatus("正在保存到服务器…");
    renderCalendar();

    try {
      const result = await planDocument.save(plan);
      plan = normalizePlan(result.value);
      elements.dialog.close();
      showLoginLink(false);
      setStatus(`已同步到服务器 · Revision ${result.revision}`, "connected");
      showToast(selected.length ? "训练日志已保存" : "已标记为休息日");
    } catch (error) {
      plan = previousPlan;
      planDocument.clearDraft();
      if (error instanceof window.MicrositeData.ConflictError) {
        setStatus("其他设备已更新数据，正在刷新…", "error");
        try {
          const latest = await planDocument.get();
          plan = normalizePlan(latest.value);
          setStatus(`已载入服务器最新版本 · Revision ${latest.revision}`, "connected");
          showToast("发现其他设备的新版本，请重新编辑");
        } catch (_refreshError) {
          setStatus("刷新服务器数据失败，请稍后重试", "error");
        }
      } else if (error?.status === 401 || error?.status === 403) {
        setStatus("当前为访客状态，登录后才能保存", "error");
        showLoginLink(true);
        showToast("训练数据未修改：请先登录站点后台");
      } else {
        setStatus("保存失败，服务器数据未被覆盖", "error");
        showToast("保存失败，请检查网络后重试");
      }
    } finally {
      saveInProgress = false;
      elements.save.disabled = false;
      renderCalendar();
    }
  }

  function buildShareUrl() {
    const activePath = `/sites/${encodeURIComponent(siteSlug)}/`;
    const url = window.location.pathname.startsWith("/sites/")
      ? new URL(window.location.href)
      : new URL(activePath, window.location.origin);
    url.pathname = activePath;
    url.search = "";
    url.hash = "";
    url.searchParams.set("month", monthKey(viewDate));
    url.searchParams.set("view", "share");
    return url.toString();
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    elements.shareLink.focus();
    elements.shareLink.select();
    if (!document.execCommand("copy")) throw new Error("copy failed");
  }

  async function shareCurrentMonth() {
    const url = buildShareUrl();
    elements.shareLink.value = url;
    elements.sharePanel.hidden = false;
    try {
      await copyText(url);
      showToast("只读链接已复制");
    } catch (_error) {
      showToast("链接已生成，请手动复制");
    }

    if (navigator.share) {
      try {
        await navigator.share({
          title: `${viewDate.getFullYear()}年${viewDate.getMonth() + 1}月训练日志`,
          text: "查看这个月的训练日志（只读）",
          url,
        });
      } catch (error) {
        if (error?.name !== "AbortError") console.warn("系统分享失败：", error);
      }
    }
  }

  async function loadSeedFallback() {
    const response = await fetch("data/training-plan.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`seed request failed (${response.status})`);
    return normalizePlan(await response.json());
  }

  async function initializeData() {
    try {
      if (!window.MicrositeData) throw new Error("Runtime SDK unavailable");
      planDocument = window.MicrositeData.document(DOCUMENT_KEY, { site: siteSlug });
      const result = await planDocument.get();
      plan = normalizePlan(result.value);
      if (!isShareView) {
        const draft = planDocument.loadDraft();
        if (draft && draft.baseRevision === result.revision) {
          plan = normalizePlan(draft.value);
          setStatus(`已恢复本地草稿 · 基于 Revision ${result.revision}`);
        } else {
          setStatus(`已从服务器读取 · Revision ${result.revision}`, "connected");
        }
      } else {
        setStatus(`只读数据 · Revision ${result.revision}`, "connected");
      }
    } catch (error) {
      planDocument = null;
      try {
        plan = await loadSeedFallback();
        setStatus("预览模式：显示迁移数据，暂不可编辑", "error");
      } catch (_seedError) {
        plan = {};
        setStatus("无法读取训练数据，请稍后刷新", "error");
      }
      console.warn("Runtime Data 连接失败：", error);
    }
    renderCalendar();
  }

  elements.prev.addEventListener("click", () => changeMonth(-1));
  elements.next.addEventListener("click", () => changeMonth(1));
  elements.today.addEventListener("click", () => {
    const current = new Date();
    viewDate = new Date(current.getFullYear(), current.getMonth(), 1);
    updateMonthUrl();
    renderCalendar();
  });
  elements.share.addEventListener("click", shareCurrentMonth);
  elements.copyShareLink.addEventListener("click", async () => {
    try {
      await copyText(elements.shareLink.value);
      showToast("只读链接已复制");
    } catch (_error) {
      showToast("复制失败，请手动选择链接");
    }
  });
  elements.grid.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-date]");
    if (button) openSchedule(button.dataset.date);
  });
  elements.options.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-training]");
    if (!button) return;
    const id = button.dataset.training;
    draftSelection.has(id) ? draftSelection.delete(id) : draftSelection.add(id);
    updateDraftControls();
  });
  elements.clear.addEventListener("click", () => {
    draftSelection.clear();
    updateDraftControls();
  });
  elements.close.addEventListener("click", () => elements.dialog.close());
  elements.cancel.addEventListener("click", () => elements.dialog.close());
  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveActiveDay();
  });
  elements.dialog.addEventListener("click", (event) => {
    if (event.target === elements.dialog) elements.dialog.close();
  });
  elements.theme.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  configureViewMode();
  renderLegend();
  renderOptions();
  updateThemeButton();
  initializeData();
})();
