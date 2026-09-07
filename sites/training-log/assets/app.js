(() => {
  "use strict";

  const NOTE_ICON = "<svg aria-hidden=\"true\" class=\"note-icon\"\n  class=\"lucide lucide-notebook-pen\"\n  xmlns=\"http://www.w3.org/2000/svg\"\n  width=\"24\"\n  height=\"24\"\n  viewBox=\"0 0 24 24\"\n  fill=\"none\"\n  stroke=\"currentColor\"\n  stroke-width=\"2\"\n  stroke-linecap=\"round\"\n  stroke-linejoin=\"round\"\n>\n  <path d=\"M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4\" />\n  <path d=\"M2 6h4\" />\n  <path d=\"M2 10h4\" />\n  <path d=\"M2 14h4\" />\n  <path d=\"M2 18h4\" />\n  <path d=\"M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z\" />\n</svg>";
  const DOCUMENT_KEY = "training-plan";
  const THEME_KEY = "training-log-theme";
  const LOCAL_PLAN_KEY = "training-log-local-plan";
  const LOCAL_DRAFT_KEY = "training-log-local-draft";
  const MONTH_RE = /^(\d{4})-(0[1-9]|1[0-2])$/;
  const DATE_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
  const TRAININGS = [
    { id: "chest", label: "胸", symbol: "胸", color: "var(--chest)", soft: "var(--chest-soft)" },
    { id: "back", label: "背", symbol: "背", color: "var(--back)", soft: "var(--back-soft)" },
    { id: "legs", label: "大腿", symbol: "腿", color: "var(--legs)", soft: "var(--legs-soft)" },
    { id: "glutes", label: "臀", symbol: "臀", color: "var(--glutes)", soft: "var(--glutes-soft)" },
    { id: "biceps", label: "肱二头", symbol: "二", color: "var(--biceps)", soft: "var(--biceps-soft)" },
    { id: "triceps", label: "肱三头", symbol: "三", color: "var(--triceps)", soft: "var(--triceps-soft)" },
    { id: "shoulders", label: "肩", symbol: "肩", color: "var(--shoulders)", soft: "var(--shoulders-soft)" },
    { id: "core", label: "腹", symbol: "腹", color: "var(--core)", soft: "var(--core-soft)" },
    { id: "cardio", label: "心肺", symbol: "心", color: "var(--cardio)", soft: "var(--cardio-soft)" },
  ];
  const EXERCISE_OPTIONS = {
    chest: ["平板卧推", "上斜卧推", "哑铃卧推", "夹胸", "双杠臂屈伸"],
    back: ["引体向上", "高位下拉", "杠铃划船", "坐姿划船", "单臂哑铃划船"],
    legs: ["深蹲", "腿举", "箭步蹲", "腿屈伸", "腿弯举"],
    glutes: ["臀推", "罗马尼亚硬拉", "保加利亚分腿蹲", "髋外展", "绳索后踢"],
    biceps: ["杠铃弯举", "哑铃弯举", "锤式弯举", "牧师凳弯举", "绳索弯举"],
    triceps: ["绳索下压", "窄距卧推", "仰卧臂屈伸", "过顶臂屈伸", "双杠臂屈伸"],
    shoulders: ["哑铃推举", "杠铃推举", "侧平举", "俯身飞鸟", "面拉"],
    core: ["卷腹", "悬垂举腿", "平板支撑", "俄罗斯转体", "健腹轮"],
    cardio: ["跑步", "骑行", "划船机", "椭圆机", "游泳"],
  };
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
    monthStats: document.getElementById("monthStats"),
    legend: document.getElementById("legend"),
    prev: document.getElementById("prevMonth"),
    next: document.getElementById("nextMonth"),
    today: document.getElementById("todayButton"),
    share: document.getElementById("shareButton"),
    sharePanel: document.getElementById("sharePanel"),
    shareLink: document.getElementById("shareLink"),
    copyShareLink: document.getElementById("copyShareLink"),
    privacyNote: document.getElementById("privacyNote"),
    dialog: document.getElementById("scheduleDialog"),
    form: document.getElementById("scheduleForm"),
    dialogKicker: document.getElementById("dialogKicker"),
    dialogTitle: document.getElementById("dialogTitle"),
    options: document.getElementById("trainingOptions"),
    details: document.getElementById("trainingDetails"),
    close: document.getElementById("closeDialog"),
    cancel: document.getElementById("cancelDialog"),
    lock: document.getElementById("toggleLock"),
    save: document.getElementById("saveDay"),
    theme: document.getElementById("themeToggle"),
    themeIcon: document.getElementById("themeIcon"),
    toast: document.getElementById("toast"),
  };

  const now = new Date();
  let viewDate = monthParamToDate(params.get("month")) || new Date(now.getFullYear(), now.getMonth(), 1);
  let plan = {};
  let dataStore = null;
  let activeDateKey = "";
  let draftSelection = new Set();
  let draftDetails = {};
  let draftLocked = false;
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

  function normalizeOptionalNumber(value, minimum, maximum, integer = false) {
    if (value === "" || value === null || value === undefined) return undefined;
    const number = Number(value);
    if (!Number.isFinite(number)) return undefined;
    const normalized = integer ? Math.round(number) : Math.round(number * 100) / 100;
    return Math.min(maximum, Math.max(minimum, normalized));
  }

  function normalizeTrainingDetail(id, source) {
    if (!source || typeof source !== "object" || Array.isArray(source)) return {};
    const note = typeof source.note === "string" ? source.note.trim().slice(0, 500) : "";
    const exercise = typeof source.exercise === "string" ? source.exercise.trim().slice(0, 80) : "";
    if (id === "cardio") {
      const averageHeartRate = normalizeOptionalNumber(source.averageHeartRate, 30, 250, true);
      const durationMinutes = normalizeOptionalNumber(source.durationMinutes, 1, 1440, true);
      return {
        ...(exercise ? { exercise } : {}),
        ...(note ? { note } : {}),
        ...(averageHeartRate === undefined ? {} : { averageHeartRate }),
        ...(durationMinutes === undefined ? {} : { durationMinutes }),
      };
    }

    const weight = normalizeOptionalNumber(source.weight, 0, 5000);
    const sets = normalizeOptionalNumber(source.sets, 1, 100, true);
    const reps = normalizeOptionalNumber(source.reps, 1, 1000, true);
    return {
      ...(exercise ? { exercise } : {}),
        ...(note ? { note } : {}),
      ...(weight === undefined ? {} : { weight }),
      ...(sets === undefined ? {} : { sets }),
      ...(reps === undefined ? {} : { reps }),
    };
  }

  function normalizeTrainingDetails(id, source) {
    const rows = Array.isArray(source) ? source : source && typeof source === "object" ? [source] : [];
    return rows
      .map((row) => normalizeTrainingDetail(id, row))
      .filter((row) => Object.keys(row).length > 0)
      .slice(0, 50);
  }

  function normalizePlanEntry(source) {
    const sourceIds = Array.isArray(source) ? source : Array.isArray(source?.ids) ? source.ids : [];
    const unique = [...new Set(sourceIds.filter((id) => PLAN_IDS.has(id)))];
    const trainings = unique.filter((id) => id !== REST.id);
    const ids = trainings.length ? trainings : unique.includes(REST.id) ? [REST.id] : [];
    const sourceDetails = !Array.isArray(source) && source?.details && typeof source.details === "object"
      ? source.details
      : {};
    const details = Object.fromEntries(
      trainings
        .map((id) => [id, normalizeTrainingDetails(id, sourceDetails[id])])
        .filter(([, rows]) => rows.length > 0),
    );
    return {
      ids,
      details,
      ...(!Array.isArray(source) && source?.locked === true ? { locked: true } : {}),
    };
  }

  function normalizePlan(source) {
    if (!source || typeof source !== "object" || Array.isArray(source)) return {};
    return Object.fromEntries(
      Object.entries(source)
        .filter(([key]) => parseDateKey(key))
        .map(([key, value]) => [key, normalizePlanEntry(value)])
        .filter(([, value]) => value.ids.length > 0)
        .sort(([left], [right]) => left.localeCompare(right)),
    );
  }

  function escapeAttribute(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function exerciseOptionsFor(id) {
    const values = new Set(EXERCISE_OPTIONS[id] || []);
    Object.values(plan).forEach((entry) => {
      (entry.details?.[id] || []).forEach((detail) => {
        if (detail.exercise) values.add(detail.exercise);
      });
    });
    (draftDetails[id] || []).forEach((detail) => {
      if (detail.exercise) values.add(String(detail.exercise).trim());
    });
    const edits = optionEdits[id] || {};
    return [...new Set([...values, ...Object.keys(edits)].map(value => Object.hasOwn(edits, value) ? edits[value] : value).filter(Boolean))];
  }

  function isLocalPreview() {
    return ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  }

  function readLocalJson(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || "null");
    } catch (_error) {
      return null;
    }
  }

  function createLocalDataStore(seedPlan) {
    const stored = readLocalJson(LOCAL_PLAN_KEY);
    let revision = Number.isInteger(stored?.revision) ? stored.revision : 0;
    let value = normalizePlan(stored?.value || seedPlan);

    return {
      get revision() {
        return revision;
      },
      async get() {
        return { value, revision };
      },
      async save(nextValue) {
        value = normalizePlan(nextValue);
        revision += 1;
        localStorage.setItem(LOCAL_PLAN_KEY, JSON.stringify({ value, revision }));
        this.clearDraft();
        return { value, revision };
      },
      saveDraft(nextValue, baseRevision) {
        localStorage.setItem(LOCAL_DRAFT_KEY, JSON.stringify({ value: nextValue, baseRevision }));
      },
      loadDraft() {
        return readLocalJson(LOCAL_DRAFT_KEY);
      },
      clearDraft() {
        localStorage.removeItem(LOCAL_DRAFT_KEY);
      },
    };
  }

  function itemForId(id) {
    return id === REST.id ? REST : TRAINING_BY_ID.get(id);
  }

  function trainingStyle(item) {
    return `--training-color:${item.color};--training-soft:${item.soft}`;
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
        <span class="option-name">${item.label}</span>
      </button>
    `).join("");
  }

  const OPTION_ICONS = {"more": "<svg\n  class=\"lucide lucide-ellipsis-vertical\"\n  xmlns=\"http://www.w3.org/2000/svg\"\n  width=\"24\"\n  height=\"24\"\n  viewBox=\"0 0 24 24\"\n  fill=\"none\"\n  stroke=\"currentColor\"\n  stroke-width=\"2\"\n  stroke-linecap=\"round\"\n  stroke-linejoin=\"round\"\n>\n  <circle cx=\"12\" cy=\"12\" r=\"1\" />\n  <circle cx=\"12\" cy=\"5\" r=\"1\" />\n  <circle cx=\"12\" cy=\"19\" r=\"1\" />\n</svg>", "cancel": "<svg\n  class=\"lucide lucide-x\"\n  xmlns=\"http://www.w3.org/2000/svg\"\n  width=\"24\"\n  height=\"24\"\n  viewBox=\"0 0 24 24\"\n  fill=\"none\"\n  stroke=\"currentColor\"\n  stroke-width=\"2\"\n  stroke-linecap=\"round\"\n  stroke-linejoin=\"round\"\n>\n  <path d=\"M18 6 6 18\" />\n  <path d=\"m6 6 12 12\" />\n</svg>", "delete": "<svg\n  class=\"lucide lucide-trash-2\"\n  xmlns=\"http://www.w3.org/2000/svg\"\n  width=\"24\"\n  height=\"24\"\n  viewBox=\"0 0 24 24\"\n  fill=\"none\"\n  stroke=\"currentColor\"\n  stroke-width=\"2\"\n  stroke-linecap=\"round\"\n  stroke-linejoin=\"round\"\n>\n  <path d=\"M3 6h18\" />\n  <path d=\"M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6\" />\n  <path d=\"M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2\" />\n  <line x1=\"10\" x2=\"10\" y1=\"11\" y2=\"17\" />\n  <line x1=\"14\" x2=\"14\" y1=\"11\" y2=\"17\" />\n</svg>"};
  const OPTION_EDITS_KEY = "training-log-option-edits";
  const optionEdits = readLocalJson(OPTION_EDITS_KEY) || {};

  function exerciseOptionMarkup(value, selected = false) {
    return `<div class="exercise-option-row" data-exercise-value="${escapeAttribute(value)}">
      <button class="exercise-option${selected ? " selected" : ""}" type="button" role="option" aria-selected="${selected}" data-exercise-value="${escapeAttribute(value)}"><span>${escapeAttribute(value)}</span></button>
      <button class="exercise-option-action option-more" type="button" data-option-action="edit" aria-label="编辑选项：${escapeAttribute(value)}">${OPTION_ICONS.more}</button>
    </div>`;
  }

  function refreshExerciseOptions(training) {
    elements.details.querySelectorAll('.exercise-combobox').forEach(combo => {
      if (combo.dataset.training !== training) return;
      const input = combo.querySelector('.exercise-combobox-input');
      combo.querySelector('.exercise-option-list').innerHTML = exerciseOptionsFor(training).map(v => exerciseOptionMarkup(v, v === input.value)).join('');
      updateExerciseMenu(input);
    });
  }

  function finishOptionEdit(row, action, restoreFocus = true) {
    const combo = row.closest('.exercise-combobox');
    const editor = row.querySelector('.option-edit-input');
    if (!editor) return;
    const oldValue = row.dataset.originalValue;
    const value = editor.value.trim();
    if (action === 'save' && !value) { editor.setCustomValidity('请输入选项文字'); editor.reportValidity(); return; }
    if (action !== 'cancel') {
      const edits = optionEdits[combo.dataset.training] ||= {};
      const replacement = action === 'delete' ? null : value;
      Object.keys(edits).forEach(key => { if (edits[key] === oldValue) edits[key] = replacement; });
      edits[oldValue] = replacement;
      try { localStorage.setItem(OPTION_EDITS_KEY, JSON.stringify(optionEdits)); }
      catch (_) { showToast('无法保存选项设置，请检查浏览器存储'); }
      // Update only the current unsaved form, never historical training records.
      if (action === 'save' || action === 'delete') {
        const nextValue = action === 'delete' ? '' : value;
        (draftDetails[combo.dataset.training] || []).forEach(detail => { if (detail.exercise === oldValue) detail.exercise = nextValue; });
        elements.details.querySelectorAll('.exercise-combobox-input').forEach(input => {
          if (input.dataset.training === combo.dataset.training && input.value.trim() === oldValue) input.value = nextValue;
        });
      }
    }
    refreshExerciseOptions(combo.dataset.training);
    const trigger = combo.querySelector(".exercise-combobox-input");
    if (restoreFocus) {
      trigger.focus({ preventScroll: true });
      openExerciseCombobox(trigger);
    }
  }

  const triggerPointerState = new WeakMap();
  elements.details.addEventListener('pointerdown', event => {
    const combo = event.target.closest('.exercise-combobox');
    if (!combo) return;
    const menu = event.target.closest('.exercise-menu');
    if (!menu) {
      const input = combo.querySelector('.exercise-combobox-input');
      triggerPointerState.set(combo, document.activeElement === input && combo.classList.contains('open'));
      if (event.target.closest('[data-combobox-action="toggle"]')) event.preventDefault();
    }
    if (event.target.closest('[data-option-action]') ||
        (menu?.querySelector('.exercise-option-row.editing') && !event.target.closest('.option-edit-input'))) {
      event.preventDefault();
    }
  });
  elements.details.addEventListener('click', event => {
    const menu = event.target.closest('.exercise-menu');
    const editing = menu?.querySelector('.exercise-option-row.editing');
    if (!editing || event.target.closest('[data-option-action], .option-edit-input')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    finishOptionEdit(editing, 'cancel');
  }, true);
  elements.details.addEventListener('click', event => {
    const action = event.target.closest('[data-option-action]');
    if (!action || draftLocked || saveInProgress) return;
    const row = action.closest('.exercise-option-row');
    if (action.dataset.optionAction !== 'edit') { finishOptionEdit(row, action.dataset.optionAction); return; }
    elements.details.querySelectorAll('.exercise-option-row.editing').forEach(other => {
      const old = other.dataset.originalValue;
      const selected = other.closest('.exercise-combobox').querySelector('.exercise-combobox-input').value === old;
      other.outerHTML = exerciseOptionMarkup(old, selected);
    });
    const value = row.querySelector('.exercise-option').dataset.exerciseValue;
    row.dataset.originalValue = value;
    row.classList.add('editing');
    row.classList.toggle('editing-selected', row.closest('.exercise-combobox').querySelector('.exercise-combobox-input').value.trim() === value);
    row.innerHTML = `<input class="option-edit-input" aria-label="编辑选项文字" maxlength="80" value="${escapeAttribute(value)}"><button type="button" class="exercise-option-action" data-option-action="cancel" aria-label="取消编辑">${OPTION_ICONS.cancel}</button><button type="button" class="exercise-option-action" data-option-action="delete" aria-label="删除选项">${OPTION_ICONS.delete}</button>`;
    row.querySelector('input').focus();
    row.querySelector('input').select();
  });
  elements.details.addEventListener('keydown', event => {
    const editor = event.target.closest('.option-edit-input');
    if (!editor) return;
    event.stopPropagation();
    if (event.key === 'Enter' || event.key === 'Escape') {
      event.preventDefault();
      finishOptionEdit(editor.closest('.exercise-option-row'), event.key === 'Enter' ? 'save' : 'cancel');
    }
  });
  elements.details.addEventListener('focusout', event => {
    const editor = event.target.closest('.option-edit-input');
    if (!editor) return;
    const row = editor.closest('.exercise-option-row');
    setTimeout(() => {
      if (row.isConnected && !row.contains(document.activeElement)) finishOptionEdit(row, 'save', false);
    }, 0);
  });
  function renderExerciseCombobox(item, detail, rowIndex, optionValues) {
    const selectedValue = String(detail.exercise || "");
    const inputId = `exercise-input-${item.id}-${rowIndex}`;
    const menuId = `exercise-menu-${item.id}-${rowIndex}`;
    const optionMarkup = optionValues.map(value => exerciseOptionMarkup(value, value === selectedValue)).join("");

    return `
      <div class="detail-field detail-field-exercise">
        <label class="visually-hidden" for="${inputId}">${item.label}第${rowIndex + 1}项训练项目</label>
        <div class="exercise-combobox" data-training="${item.id}" data-row="${rowIndex}">
          <input
            class="exercise-combobox-input"
            id="${inputId}"
            type="text"
            maxlength="80"
            autocomplete="off"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded="false"
            aria-controls="${menuId}"
            aria-label="${item.label}第${rowIndex + 1}项训练项目"
            data-training="${item.id}"
            data-row="${rowIndex}"
            data-field="exercise"
            value="${escapeAttribute(selectedValue)}"
            placeholder="选择或输入"
          >
          <button class="exercise-combobox-toggle" type="button" tabindex="-1" aria-label="展开${item.label}训练项目选项" data-combobox-action="toggle">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg>
          </button>
          <div class="exercise-menu" id="${menuId}" role="listbox" hidden>
            <div class="exercise-option-list">${optionMarkup}</div>
            <button class="exercise-create" type="button" data-combobox-action="create" hidden></button>
            <div class="exercise-empty" hidden>暂无可选项目</div>
          </div>
        </div>
      </div>
    `;
  }

  function renderLockedDetailFields(item, detail) {
    const display = (value) => value === "" || value === null || value === undefined
      ? '<span class="detail-value-empty">—</span>'
      : escapeAttribute(value);
    const metric = (value, unit) => `<span class="locked-metric"><strong>${display(value)}</strong><span class="locked-unit">${unit}</span></span>`;
    const separator = (symbol) => `<span class="locked-separator" aria-hidden="true">${symbol}</span>`;
    const summary = item.id === "cardio"
      ? `${metric(detail.averageHeartRate, 'bpm')}${separator('·')}${metric(detail.durationMinutes, '分钟')}`
      : `${metric(detail.weight, 'kg')}${separator('·')}${metric(detail.sets, '组')}${separator('×')}${metric(detail.reps, '次')}`;
    return `
      <span class="locked-exercise" title="${escapeAttribute(detail.exercise || "")}">${display(detail.exercise)}</span>
      <span class="locked-summary">${summary}</span>
      <span class="locked-note">${escapeAttribute(detail.note || "")}</span>
    `;
  }

  function renderTrainingDetails() {
    const selected = TRAININGS.filter((item) => draftSelection.has(item.id));
    elements.details.hidden = false;
    if (!selected.length) {
      elements.details.innerHTML = draftLocked
        ? `
          <div class="locked-rest-state" style="${trainingStyle(REST)}">
            <span class="option-symbol" aria-hidden="true">${REST.symbol}</span>
            <strong>休息日</strong>
          </div>
        `
        : `
          <div class="training-details-empty">
            <span aria-hidden="true">＋</span>
            <strong>选择训练部位</strong>
            <p>选中上方部位后，在这里安排一项或多项训练。</p>
          </div>
        `;
      return;
    }

    elements.details.innerHTML = selected.map((item) => {
      const rows = Array.isArray(draftDetails[item.id]) && draftDetails[item.id].length
        ? draftDetails[item.id]
        : [{}];
      draftDetails[item.id] = rows;
      const optionValues = exerciseOptionsFor(item.id);
      const rowMarkup = rows.map((detail, rowIndex) => {
        const fields = draftLocked
          ? renderLockedDetailFields(item, detail)
          : item.id === "cardio"
          ? `${renderExerciseCombobox(item, detail, rowIndex, optionValues)}
            <label class="detail-field detail-field-number">
              <span class="visually-hidden">${item.label}第${rowIndex + 1}项平均心率</span>
              <input type="number" inputmode="numeric" min="30" max="250" step="1" aria-label="${item.label}第${rowIndex + 1}项平均心率" data-training="${item.id}" data-row="${rowIndex}" data-field="averageHeartRate" value="${escapeAttribute(detail.averageHeartRate)}" placeholder="145">
              <span class="detail-input-unit" aria-hidden="true">bpm</span>
            </label>
            <label class="detail-field detail-field-number">
              <span class="visually-hidden">${item.label}第${rowIndex + 1}项时长（分钟）</span>
              <input type="number" inputmode="numeric" min="1" max="1440" step="1" aria-label="${item.label}第${rowIndex + 1}项时长（分钟）" data-training="${item.id}" data-row="${rowIndex}" data-field="durationMinutes" value="${escapeAttribute(detail.durationMinutes)}" placeholder="30">
              <span class="detail-input-unit" aria-hidden="true">分钟</span>
            </label>
          `
          : `${renderExerciseCombobox(item, detail, rowIndex, optionValues)}
            <label class="detail-field detail-field-number">
              <span class="visually-hidden">${item.label}第${rowIndex + 1}项重量（kg）</span>
              <input type="number" inputmode="decimal" min="0" max="5000" step="0.01" aria-label="${item.label}第${rowIndex + 1}项重量（kg）" data-training="${item.id}" data-row="${rowIndex}" data-field="weight" value="${escapeAttribute(detail.weight)}" placeholder="60">
              <span class="detail-input-unit" aria-hidden="true">kg</span>
            </label>
            <label class="detail-field detail-field-number">
              <span class="visually-hidden">${item.label}第${rowIndex + 1}项组数</span>
              <input type="number" inputmode="numeric" min="1" max="100" step="1" aria-label="${item.label}第${rowIndex + 1}项组数" data-training="${item.id}" data-row="${rowIndex}" data-field="sets" value="${escapeAttribute(detail.sets)}" placeholder="4">
              <span class="detail-input-unit" aria-hidden="true">组</span>
            </label>
            <label class="detail-field detail-field-number">
              <span class="visually-hidden">${item.label}第${rowIndex + 1}项次数</span>
              <input type="number" inputmode="numeric" min="1" max="1000" step="1" aria-label="${item.label}第${rowIndex + 1}项次数" data-training="${item.id}" data-row="${rowIndex}" data-field="reps" value="${escapeAttribute(detail.reps)}" placeholder="10">
              <span class="detail-input-unit" aria-hidden="true">次</span>
            </label>
          `;

        return `
          <div class="training-detail-row${draftLocked ? " locked" : ""}" data-training="${item.id}" data-row="${rowIndex}">
            ${draftLocked ? `<span class="detail-index" aria-hidden="true">${rowIndex + 1}</span>` : `<button type="button" class="detail-index drag-handle" data-training="${item.id}" data-row="${rowIndex}" aria-label="拖动排序第${rowIndex + 1}项，可用上下方向键调整">${rowIndex + 1}</button>`}
            <div class="detail-fields${item.id === "cardio" ? " detail-fields-cardio" : ""}${draftLocked ? " locked-fields" : ""}">${fields}${draftLocked ? "" : `<label class="detail-field detail-field-note"><input type="text" maxlength="500" placeholder="备注" aria-label="${item.label}第${rowIndex + 1}项备注" data-training="${item.id}" data-row="${rowIndex}" data-field="note" value="${escapeAttribute(detail.note || "")}"></label>`}</div>
            ${draftLocked ? "" : `<div class="detail-actions"><button class="remove-detail-button" type="button" data-detail-action="remove" data-training="${item.id}" data-row="${rowIndex}" aria-label="删除${item.label}第${rowIndex + 1}项训练">×</button><div class="note-control"><button type="button" class="note-button${detail.note ? " has-note" : ""}" data-note-training="${item.id}" data-row="${rowIndex}" aria-expanded="false" aria-label="编辑${item.label}第${rowIndex + 1}项备注">${NOTE_ICON}</button><div class="note-popover" hidden><textarea maxlength="500" rows="3" placeholder="备注" aria-label="训练备注">${escapeAttribute(detail.note || "")}</textarea></div></div></div>`}
          </div>
        `;
      }).join("");

      return `
        <section class="training-detail-card" style="${trainingStyle(item)}" aria-labelledby="detail-title-${item.id}">
          <div class="training-detail-heading">
            <div class="training-detail-title">
              <span class="option-symbol" aria-hidden="true">${item.symbol}</span>
              <h3 id="detail-title-${item.id}">${item.label}</h3>
            </div>
            ${draftLocked ? "" : `<button class="add-detail-button" type="button" data-detail-action="add" data-training="${item.id}" aria-label="添加${item.label}训练项目" title="添加项目"><span aria-hidden="true">＋</span></button>`}
          </div>
          <div class="training-detail-list">${rowMarkup}</div>
        </section>
      `;
    }).join("");
  }

  function renderCalendar() {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const monthStart = new Date(year, month, 1);
    const mondayOffset = (monthStart.getDay() + 6) % 7;
    const gridStart = new Date(year, month, 1 - mondayOffset);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cellCount = Math.ceil((mondayOffset + daysInMonth) / 7) * 7;
    const todayKey = dateKey(new Date());
    const prefix = `${monthKey(viewDate)}-`;
    const monthEntries = Object.entries(plan).filter(([key]) => key.startsWith(prefix));
    const plannedDays = monthEntries.length;
    const trainingDays = monthEntries.filter(([, entry]) => entry.ids.some((id) => id !== REST.id)).length;

    elements.monthTitle.textContent = `${year}年${month + 1}月`;
    elements.monthStats.textContent = `本月记录 ${plannedDays} 天 · ${trainingDays} 项训练`;
    elements.prev.setAttribute("aria-label", `查看${month === 0 ? year - 1 : year}年${month === 0 ? 12 : month}月`);
    elements.next.setAttribute("aria-label", `查看${month === 11 ? year + 1 : year}年${month === 11 ? 1 : month + 2}月`);

    const cells = [];
    for (let index = 0; index < cellCount; index += 1) {
      const day = new Date(gridStart);
      day.setDate(gridStart.getDate() + index);
      const key = dateKey(day);
      const entry = plan[key] || { ids: [], details: {} };
      const items = entry.ids.map(itemForId).filter(Boolean);
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
      const overflowChip = items.length > 3
        ? `<span class="training-overflow" aria-hidden="true">+${items.length - 3}</span>`
        : "";
      const content = `
        <span class="day-date">
          <span class="day-number">${day.getDate()}</span>
          <span class="day-weekday">${weekday}</span>
        </span>
        <span class="chips">${chips}${overflowChip}${items.length ? "" : '<span class="empty-hint" aria-hidden="true"></span>'}</span>
      `;

      if (isShareView) {
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
    if (isShareView || saveInProgress) return;
    const date = parseDateKey(key);
    if (!date) return;
    activeDateKey = key;
    const current = plan[key] || { ids: [], details: {} };
    draftLocked = current.locked === true;
    draftSelection = new Set(current.ids.filter((id) => id !== REST.id));
    draftDetails = Object.fromEntries(
      Object.entries(current.details || {}).map(([id, rows]) => [
        id,
        (Array.isArray(rows) ? rows : [rows]).map((row) => ({ ...row })),
      ]),
    );
    const weekday = new Intl.DateTimeFormat("zh-CN", { weekday: "long" }).format(date);
    elements.dialogTitle.textContent = `${date.getMonth() + 1}月${date.getDate()}日 · ${weekday}`;
    updateDraftControls();
    elements.dialog.showModal();
  }

  function updateDraftControls() {
    elements.form.dataset.locked = String(draftLocked);
    elements.options.hidden = draftLocked;
    elements.details.classList.toggle("locked", draftLocked);
    elements.dialogKicker.textContent = draftLocked ? "训练记录 · 已锁定" : "编辑训练";
    elements.lock.textContent = draftLocked ? "解锁" : "锁定";
    elements.lock.setAttribute("aria-pressed", String(draftLocked));
    elements.lock.disabled = saveInProgress;
    elements.options.querySelectorAll("[data-training]").forEach((button) => {
      button.setAttribute("aria-pressed", String(draftSelection.has(button.dataset.training)));
    });
    renderTrainingDetails();
    elements.save.textContent = draftSelection.size ? `保存 ${draftSelection.size} 项训练` : "保存休息日";
    elements.save.disabled = saveInProgress || draftLocked;
  }

  window.addEventListener("microsite:before-login", () => {
    if (activeDateKey && dataStore && elements.dialog.open && !isShareView) {
      const { entry } = buildDraftEntry();
      dataStore.saveDraft({...plan, [activeDateKey]: entry}, dataStore.revision ?? 0);
    }
  });

  function buildDraftEntry(locked = draftLocked) {
    const selected = TRAININGS.map((item) => item.id).filter((id) => draftSelection.has(id));
    const details = Object.fromEntries(
      selected
        .map((id) => [id, normalizeTrainingDetails(id, draftDetails[id])])
        .filter(([, rows]) => rows.length > 0),
    );
    const entry = selected.length
      ? { ids: selected, details }
      : { ids: [REST.id], details: {} };
    if (locked) entry.locked = true;
    return { entry, selected };
  }

  async function persistActiveEntry(entry, { closeDialog = false, successMessage = "已保存" } = {}) {
    if (!draftLocked && !elements.form.reportValidity()) return false;
    if (!activeDateKey || isShareView || saveInProgress) return false;
    if (!dataStore) {
      showToast("当前无法连接数据服务，修改未保存");
      return false;
    }
    const previousPlan = plan;
    const nextPlan = {
      ...plan,
      [activeDateKey]: entry,
    };
    dataStore.saveDraft(nextPlan, dataStore.revision ?? 0);
    plan = normalizePlan(nextPlan);
    saveInProgress = true;
    updateDraftControls();
    renderCalendar();

    try {
      const result = await dataStore.save(plan);
      plan = normalizePlan(result.value);
      if (closeDialog) elements.dialog.close();
      showToast(successMessage);
      return true;
    } catch (error) {
      plan = previousPlan;
      if (error?.status !== 401) dataStore.clearDraft();
      if (window.MicrositeData && error instanceof window.MicrositeData.ConflictError) {
        try {
          const latest = await dataStore.get();
          plan = normalizePlan(latest.value);
          showToast("发现其他设备的新版本，请重新编辑");
        } catch (_refreshError) {
          showToast("刷新服务器数据失败，请稍后重试");
        }
      } else if (error?.status === 401 || error?.status === 403) {
        showToast("训练数据未修改：请先登录站点后台");
      } else {
        showToast("保存失败，请检查网络后重试");
      }
      return false;
    } finally {
      saveInProgress = false;
      if (elements.dialog.open) updateDraftControls();
      renderCalendar();
    }
  }

  async function saveActiveDay() {
    if (draftLocked) return;
    const { entry, selected } = buildDraftEntry(false);
    await persistActiveEntry(entry, {
      closeDialog: true,
      successMessage: selected.length ? "训练日志已保存" : "已标记为休息日",
    });
  }

  async function toggleActiveDayLock() {
    if (!activeDateKey || isShareView || saveInProgress || !dataStore) {
      if (!dataStore) showToast("当前无法连接数据服务，锁定状态未修改");
      return;
    }
    if (!draftLocked && !elements.form.reportValidity()) return;
    const previousLocked = draftLocked;
    const nextLocked = !draftLocked;
    const { entry } = buildDraftEntry(nextLocked);
    if (nextLocked) {
      draftLocked = true;
      updateDraftControls();
    }
    const saved = await persistActiveEntry(entry, {
      successMessage: nextLocked ? "该日期已锁定" : "已解锁，可以继续编辑",
    });
    draftLocked = saved ? plan[activeDateKey]?.locked === true : previousLocked;
    updateDraftControls();
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
    if (!elements.sharePanel.hidden) {
      elements.sharePanel.hidden = true;
      elements.share.setAttribute("aria-expanded", "false");
      return;
    }
    let url;
    elements.share.disabled = true;
    try {
      const response = await fetch(`/api/sites/${encodeURIComponent(siteSlug)}/share-links`, {
        method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({document: DOCUMENT_KEY, month: monthKey(viewDate)}),
      });
      if (!response.ok) throw new Error("分享链接生成失败，请确认已登录且拥有此站点");
      url = new URL((await response.json()).url, location.origin).href;
    } catch (error) {
      showToast(error.message);
      return;
    } finally {
      elements.share.disabled = false;
    }
    elements.shareLink.value = url;
    elements.sharePanel.hidden = false;
    elements.share.setAttribute("aria-expanded", "true");
    elements.shareLink.focus();
    elements.shareLink.select();
    showToast("只读分享链接已生成");
  }

  async function loadSeedFallback() {
    const response = await fetch("training-plan.seed.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`seed request failed (${response.status})`);
    return normalizePlan(await response.json());
  }

  async function initializeData() {
    try {
      if (!window.MicrositeData) throw new Error("Runtime SDK unavailable");
      dataStore = window.MicrositeData.document(DOCUMENT_KEY, { site: siteSlug });
      const result = await dataStore.get();
      plan = normalizePlan(result.value);
      if (!isShareView) {
        const draft = dataStore.loadDraft();
        if (draft && draft.baseRevision === result.revision) {
          plan = normalizePlan(draft.value);
        }
      }
    } catch (error) {
      dataStore = null;
      try {
        const seedPlan = await loadSeedFallback();
        if (isLocalPreview() && !isShareView) {
          dataStore = createLocalDataStore(seedPlan);
          const result = await dataStore.get();
          plan = normalizePlan(result.value);
          const draft = dataStore.loadDraft();
          if (draft && draft.baseRevision === result.revision) {
            plan = normalizePlan(draft.value);
          }
          elements.privacyNote.innerHTML = '<span aria-hidden="true">●</span><span><strong>本地验收模式</strong>：修改仅保存在当前浏览器，不会写入线上 Runtime Data。</span>';
        } else {
          plan = seedPlan;
        }
      } catch (_seedError) {
        plan = {};
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
  document.addEventListener("click", (event) => {
    if (
      !elements.sharePanel.hidden
      && !elements.sharePanel.contains(event.target)
      && !elements.share.contains(event.target)
    ) {
      elements.sharePanel.hidden = true;
      elements.share.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.sharePanel.hidden) {
      elements.sharePanel.hidden = true;
      elements.share.setAttribute("aria-expanded", "false");
      elements.share.focus();
    }
  });
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
    if (draftLocked || saveInProgress) return;
    const button = event.target.closest("button[data-training]");
    if (!button) return;
    const id = button.dataset.training;
    if (draftSelection.has(id)) {
      draftSelection.delete(id);
    } else {
      draftSelection.add(id);
      draftDetails[id] ||= [{}];
    }
    updateDraftControls();
  });
  elements.details.addEventListener("click", (event) => {
    if (draftLocked || saveInProgress) return;
    const button = event.target.closest("button[data-detail-action][data-training]");
    if (!button) return;
    const { detailAction, training } = button.dataset;
    const rows = Array.isArray(draftDetails[training]) ? draftDetails[training] : [];
    if (detailAction === "add" && rows.length < 50) {
      rows.push({});
    } else if (detailAction === "remove") {
      rows.splice(Number(button.dataset.row), 1);
      if (!rows.length) rows.push({});
    }
    draftDetails[training] = rows;
    renderTrainingDetails();
  });
  elements.details.addEventListener("input", (event) => {
    if (draftLocked || saveInProgress) return;
    const input = event.target.closest("input[data-training][data-row][data-field]");
    if (!input) return;
    const { training, field, row } = input.dataset;
    draftDetails[training] ||= [{}];
    draftDetails[training][Number(row)] ||= {};
    draftDetails[training][Number(row)][field] = input.value;
    if (field === "exercise") {
      openExerciseCombobox(input, true);
    }
  });


  function closeNoteEditors(except = null) {
    elements.details.querySelectorAll(".note-control").forEach(control => {
      if (control === except) return;
      control.querySelector(".note-popover").hidden = true;
      control.querySelector(".note-button").setAttribute("aria-expanded", "false");
    });
  }
  elements.details.addEventListener("click", event => {
    const button = event.target.closest("[data-note-training]");
    if (!button || draftLocked || saveInProgress) return;
    const control = button.closest(".note-control");
    const panel = control.querySelector(".note-popover");
    const opening = panel.hidden;
    closeNoteEditors();
    if (!opening) return;
    closeExerciseComboboxes();
    panel.querySelector("textarea").value = draftDetails[button.dataset.noteTraining][Number(button.dataset.row)].note || "";
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    panel.querySelector("textarea").focus({preventScroll: true});
    panel.scrollIntoView({block: "nearest", inline: "nearest"});
  });
  elements.details.addEventListener("input", event => {
    if (!event.target.matches(".note-popover textarea") || draftLocked || saveInProgress) return;
    const control = event.target.closest(".note-control");
    const button = control.querySelector(".note-button");
    const {noteTraining: training, row} = button.dataset;
    const value = event.target.value;
    draftDetails[training][Number(row)].note = value;
    button.classList.toggle("has-note", Boolean(value.trim()));
    control.closest(".training-detail-row").querySelector('[data-field="note"]').value = value;
  });
  document.addEventListener("pointerdown", event => {
    if (!event.target.closest(".note-control")) closeNoteEditors();
  });
  document.addEventListener("keydown", event => {
    const control = event.target.closest(".note-control");
    if (event.key === "Escape" && control && !control.querySelector(".note-popover").hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeNoteEditors();
      control.querySelector(".note-button").focus();
    }
  }, true);
  window.addEventListener("resize", () => closeNoteEditors());

  function moveDetail(training, from, to) {
    const rows = draftDetails[training];
    if (draftLocked || saveInProgress || !rows || to < 0 || to >= rows.length || from === to) return;
    rows.splice(to, 0, rows.splice(from, 1)[0]);
    renderTrainingDetails();
    elements.details.querySelector(`.drag-handle[data-training="${training}"][data-row="${to}"]`)?.focus({preventScroll: true});
  }
  let drag = null;
  elements.details.addEventListener("pointerdown", event => {
    const handle = event.target.closest(".drag-handle");
    if (!handle || draftLocked || saveInProgress || event.button !== 0) return;
    closeExerciseComboboxes();
    drag = {training: handle.dataset.training, from: Number(handle.dataset.row), to: Number(handle.dataset.row), handle, pointer: event.pointerId};
    handle.setPointerCapture(event.pointerId);
    handle.closest(".training-detail-row").classList.add("dragging");
    event.preventDefault();
  });
  elements.details.addEventListener("pointermove", event => {
    if (!drag || drag.pointer !== event.pointerId) return;
    const rows = [...elements.details.querySelectorAll(`.training-detail-row[data-training="${drag.training}"]`)];
    const nearest = rows.reduce((best, row) => {
      const rect = row.getBoundingClientRect();
      const distance = Math.abs(event.clientY - (rect.top + rect.height / 2));
      return !best || distance < best.distance ? {row, distance} : best;
    }, null);
    rows.forEach(row => row.classList.remove("drop-target"));
    if (nearest) {
      drag.to = Number(nearest.row.dataset.row);
      if (drag.to !== drag.from) nearest.row.classList.add("drop-target");
    }
    const body = elements.details.closest(".dialog-body") || elements.details.parentElement;
    const rect = body.getBoundingClientRect();
    if (event.clientY < rect.top + 40) body.scrollTop -= 12;
    if (event.clientY > rect.bottom - 40) body.scrollTop += 12;
  });
  function finishDrag(event) {
    if (!drag || event.pointerId !== drag.pointer) return;
    const current = drag;
    drag = null;
    elements.details.querySelectorAll(".dragging, .drop-target").forEach(row => row.classList.remove("dragging", "drop-target"));
    if (event.type === "pointerup") moveDetail(current.training, current.from, current.to);
  }
  elements.details.addEventListener("pointerup", finishDrag);
  elements.details.addEventListener("pointercancel", finishDrag);
  elements.details.addEventListener("keydown", event => {
    const handle = event.target.closest(".drag-handle");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const from = Number(handle.dataset.row);
    moveDetail(handle.dataset.training, from, from + (event.key === "ArrowUp" ? -1 : 1));
  });

  function updateExerciseMenu(input) {
    const combo = input.closest(".exercise-combobox");
    if (!combo) return;
    const filtering = combo.dataset.filtering === "true";
    const value = input.value.trim();
    const query = filtering ? value.toLocaleLowerCase("zh-CN") : "";
    let visibleCount = 0;
    let exactMatch = false;

    combo.querySelectorAll(".exercise-option-row").forEach((row) => {
      const option = row.querySelector(".exercise-option");
      const optionValue = row.dataset.exerciseValue || "";
      const matches = !query || optionValue.toLocaleLowerCase("zh-CN").includes(query);
      const selected = optionValue === value;
      row.hidden = !matches;
      if (option) {
        option.hidden = !matches;
        option.classList.toggle("selected", selected);
        option.setAttribute("aria-selected", String(selected));
      }

      if (matches) visibleCount += 1;
      if (optionValue.toLocaleLowerCase("zh-CN") === value.toLocaleLowerCase("zh-CN")) exactMatch = true;
    });

    const createButton = combo.querySelector(".exercise-create");
    const canCreate = filtering && Boolean(value) && !exactMatch;
    createButton.hidden = !canCreate;
    createButton.textContent = canCreate ? `＋ 添加“${value}”` : "";
    combo.querySelector(".exercise-empty").hidden = visibleCount > 0 || canCreate;
  }

  function closeExerciseCombobox(combo) {
    if (!combo) return;
    combo.classList.remove("open", "drop-up");
    combo.querySelector(".exercise-menu").hidden = true;
    combo.querySelector(".exercise-combobox-input").setAttribute("aria-expanded", "false");
    combo.querySelectorAll(".exercise-option.active").forEach((option) => option.classList.remove("active"));
  }

  function closeExerciseComboboxes(except) {
    elements.details.querySelectorAll(".exercise-combobox.open").forEach((combo) => {
      if (combo !== except) closeExerciseCombobox(combo);
    });
  }

  function openExerciseCombobox(input, filtering = false) {
    const combo = input.closest(".exercise-combobox");
    if (!combo) return;
    const wasOpen = combo.classList.contains("open");
    closeExerciseComboboxes(combo);
    combo.classList.add("open");
    combo.dataset.filtering = filtering || (wasOpen && combo.dataset.filtering === "true") ? "true" : "false";
    combo.querySelector(".exercise-menu").hidden = false;
    input.setAttribute("aria-expanded", "true");
    updateExerciseMenu(input);

    const detailsRect = elements.details.getBoundingClientRect();
    const inputRect = input.getBoundingClientRect();
    const roomBelow = detailsRect.bottom - inputRect.bottom;
    const availableSpace = roomBelow;
    combo.classList.remove("drop-up");
    combo.style.setProperty("--exercise-menu-max-height", `${Math.max(96, Math.min(260, availableSpace - 10))}px`);
  }

  function syncExerciseOption(training, value) {
    // A historical or stale input must not reinsert a removed/renamed option.
    const edits = optionEdits[training] || {};
    if (Object.hasOwn(edits, value) && edits[value] !== value) return;
    elements.details.querySelectorAll(".exercise-combobox").forEach((combo) => {
      if (combo.dataset.training !== training) return;
      const list = combo.querySelector(".exercise-option-list");
      const exists = [...list.querySelectorAll(".exercise-option-row")]
        .some((row) => row.dataset.exerciseValue === value);
      if (!exists) {
        list.insertAdjacentHTML("beforeend", exerciseOptionMarkup(value));
      }
      updateExerciseMenu(combo.querySelector(".exercise-combobox-input"));
    });
  }

  function commitExerciseInput(input) {
    const value = input.value.trim().slice(0, 80);
    input.value = value;
    draftDetails[input.dataset.training][Number(input.dataset.row)].exercise = value;
    if (value) syncExerciseOption(input.dataset.training, value);
    updateExerciseMenu(input);
    return value;
  }

  function commitExerciseOption(event) {
    const input = event.target.closest('input[data-field="exercise"][data-training][data-row]');
    if (input) commitExerciseInput(input);
  }

  elements.details.addEventListener("change", commitExerciseOption);
  elements.details.addEventListener("focusin", (event) => {
    const input = event.target.closest('input[data-field="exercise"]');
    if (input) openExerciseCombobox(input);
  });
  elements.details.addEventListener("focusout", (event) => {
    const input = event.target.closest('input[data-field="exercise"]');
    if (input) commitExerciseInput(input);
    const combo = event.target.closest(".exercise-combobox");
    if (!combo) return;
    window.setTimeout(() => {
      if (!combo.contains(document.activeElement)) closeExerciseCombobox(combo);
    });
  });
  elements.details.addEventListener("click", (event) => {
    const triggerCombo = event.target.closest('.exercise-combobox');
    if (triggerCombo && !event.target.closest('.exercise-menu')) {
      const input = triggerCombo.querySelector('.exercise-combobox-input');
      const shouldClose = triggerPointerState.has(triggerCombo)
        ? triggerPointerState.get(triggerCombo)
        : document.activeElement === input && triggerCombo.classList.contains('open');
      triggerPointerState.delete(triggerCombo);
      input.focus({ preventScroll: true });
      if (shouldClose) closeExerciseCombobox(triggerCombo);
      else openExerciseCombobox(input);
      return;
    }

    const option = event.target.closest(".exercise-option");
    const create = event.target.closest('[data-combobox-action="create"]');
    if (!option && !create) return;
    const combo = event.target.closest(".exercise-combobox");
    const input = combo.querySelector(".exercise-combobox-input");
    input.value = option ? option.dataset.exerciseValue : input.value.trim();
    commitExerciseInput(input);
    input.focus({ preventScroll: true });
    closeExerciseCombobox(combo);
  });
  elements.details.addEventListener("keydown", (event) => {
    const input = event.target.closest('input[data-field="exercise"]');
    if (!input) return;
    const combo = input.closest(".exercise-combobox");
    if (event.key === "Escape") {
      closeExerciseCombobox(combo);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const active = combo.querySelector(".exercise-option.active:not([hidden])");
      if (active) input.value = active.dataset.exerciseValue;
      commitExerciseInput(input);
      closeExerciseCombobox(combo);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    openExerciseCombobox(input, combo.dataset.filtering === "true");
    const options = [...combo.querySelectorAll(".exercise-option:not([hidden])")];
    if (!options.length) return;
    const currentIndex = options.findIndex((option) => option.classList.contains("active"));
    const nextIndex = event.key === "ArrowDown"
      ? (currentIndex + 1) % options.length
      : (currentIndex <= 0 ? options.length : currentIndex) - 1;
    options.forEach((option, index) => option.classList.toggle("active", index === nextIndex));
    options[nextIndex].scrollIntoView({ block: "nearest" });
  });
  elements.close.addEventListener("click", () => elements.dialog.close());
  elements.cancel.addEventListener("click", () => elements.dialog.close());
  elements.lock.addEventListener("click", toggleActiveDayLock);
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
