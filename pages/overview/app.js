const bridge = window.AstrBotPluginPage;

const state = {
  overview: null,
  curve: [],
  config: null,
};

const formKeys = [
  "idle_compaction_enabled",
  "idle_compaction_after_minutes",
  "idle_compaction_max_idle_minutes",
  "active_session_min_messages_24h",
  "expected_context_tokens",
  "lite_compaction_ratio_threshold",
  "protected_tags",
  "auto_drop_tool_age",
];

const elements = {
  title: document.getElementById("page-title"),
  desc: document.getElementById("page-desc"),
  eyebrow: document.getElementById("eyebrow"),
  heroCount: document.getElementById("hero-count"),
  heroCountLabel: document.getElementById("hero-count-label"),
  compactedTokens: document.getElementById("compacted-tokens"),
  savedTokens: document.getElementById("saved-tokens"),
  modeSplit: document.getElementById("mode-split"),
  avgRatio: document.getElementById("avg-ratio"),
  activeSessions: document.getElementById("active-sessions"),
  latestEvent: document.getElementById("latest-event"),
  curveSummary: document.getElementById("curve-summary"),
  saveStatus: document.getElementById("save-status"),
  chartEmpty: document.getElementById("chart-empty"),
  chartGrid: document.getElementById("chart-grid"),
  chartThresholds: document.getElementById("chart-thresholds"),
  chartLine: document.getElementById("chart-line"),
  chartArea: document.getElementById("chart-area"),
  chartPoints: document.getElementById("chart-points"),
  refreshButton: document.getElementById("refresh-button"),
  form: document.getElementById("config-form"),
};

function t(key, fallback) {
  return bridge.t(key, fallback);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function formatTime(timestamp) {
  if (!timestamp) {
    return "暂无";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function renderStaticText() {
  document.title = t("pages.overview.title", "Magic Context 面板");
  elements.title.textContent = t("pages.overview.heading", "上下文压缩面板");
  elements.desc.textContent = t(
    "pages.overview.description",
    "用漫画分镜看今天的压缩动作、上下文压力曲线和后台压缩策略。",
  );
  elements.eyebrow.textContent = t("pages.overview.eyebrow", "Magic Context");
  elements.heroCountLabel.textContent = t("pages.overview.hero_count", "今日压缩次数");
}

function renderOverview() {
  const data = state.overview;
  if (!data) {
    return;
  }

  elements.heroCount.textContent = formatNumber(data.compaction_count);
  elements.compactedTokens.textContent = formatNumber(data.compacted_tokens);
  elements.savedTokens.textContent = formatNumber(data.saved_tokens);
  elements.modeSplit.textContent = `${formatNumber(data.lite_count)} / ${formatNumber(data.hard_count)}`;
  elements.avgRatio.textContent = formatPercent(data.avg_ratio);
  elements.activeSessions.textContent = `活跃会话 ${formatNumber(data.active_sessions)}`;
  elements.latestEvent.textContent = `最近压缩：${formatTime(data.latest_event_at)}`;

  const pressureText =
    data.avg_ratio >= 1 ? "今天多次顶到目标上下文线，适合让 LLM 自己决定是否清理" :
    data.avg_ratio >= 0.4 ? "今天多次进入 Lite 观察区，后台空闲压缩可能会介入" :
    "今天大多停留在低压区，缓存稳定性较好";
  elements.curveSummary.textContent = `${data.date_label} · ${pressureText}`;
}

function buildGridLines() {
  const width = 760;
  const height = 280;
  const horizontal = [0, 0.25, 0.5, 0.75, 1];
  const vertical = [0, 0.25, 0.5, 0.75, 1];

  elements.chartGrid.innerHTML = horizontal
    .map((ratio) => {
      const y = 20 + (height - 40) * ratio;
      return `<line x1="24" y1="${y}" x2="${width - 24}" y2="${y}"></line>`;
    })
    .join("") +
    vertical
      .map((ratio) => {
        const x = 24 + (width - 48) * ratio;
        return `<line x1="${x}" y1="20" x2="${x}" y2="${height - 20}"></line>`;
      })
      .join("");

  const liteThreshold = Number(state.config?.lite_compaction_ratio_threshold ?? 0.4);
  const liteY = 20 + (height - 40) * (1 - Math.max(0, Math.min(1, liteThreshold)));
  const hardY = 20;
  elements.chartThresholds.innerHTML = [
    `<line class="lite-line" x1="24" y1="${liteY}" x2="${width - 24}" y2="${liteY}"></line>`,
    `<line class="hard-line" x1="24" y1="${hardY}" x2="${width - 24}" y2="${hardY}"></line>`,
  ].join("");
}

function renderCurve() {
  const points = Array.isArray(state.curve) ? state.curve : [];
  buildGridLines();

  if (points.length === 0) {
    elements.chartLine.setAttribute("d", "");
    elements.chartArea.setAttribute("d", "");
    elements.chartPoints.innerHTML = "";
    elements.chartEmpty.style.display = "grid";
    return;
  }

  elements.chartEmpty.style.display = "none";

  const width = 760;
  const height = 280;
  const left = 24;
  const right = width - 24;
  const top = 20;
  const bottom = height - 20;
  const safeMax = Math.max(points.length - 1, 1);

  const mapped = points.map((point, index) => {
    const x = left + ((right - left) * index) / safeMax;
    const clampedRatio = Math.max(0, Math.min(1, Number(point.ratio || 0)));
    const y = bottom - (bottom - top) * clampedRatio;
    return { ...point, x, y, clampedRatio };
  });

  const lineD = mapped
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");
  const areaD = `${lineD} L ${mapped[mapped.length - 1].x.toFixed(2)} ${bottom} L ${mapped[0].x.toFixed(2)} ${bottom} Z`;

  elements.chartLine.setAttribute("d", lineD);
  elements.chartArea.setAttribute("d", areaD);
  elements.chartPoints.innerHTML = mapped
    .map(
      (point) =>
        `<circle cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="6">
          <title>${formatTime(point.created_at)} · ${formatPercent(point.clampedRatio)} · ${formatNumber(point.input_tokens)} tokens</title>
        </circle>`,
    )
    .join("");
}

function renderConfig() {
  if (!state.config) {
    return;
  }
  for (const key of formKeys) {
    const input = elements.form.elements.namedItem(key);
    if (!input) {
      continue;
    }
    if (input.type === "checkbox") {
      input.checked = Boolean(state.config[key]);
    } else {
      input.value = state.config[key] ?? "";
    }
  }
}

async function loadOverview() {
  state.overview = await bridge.apiGet("dashboard/overview");
  renderOverview();
}

async function loadCurve() {
  const result = await bridge.apiGet("dashboard/curve");
  state.curve = Array.isArray(result?.points) ? result.points : [];
  renderCurve();
}

async function loadConfig() {
  const result = await bridge.apiGet("dashboard/config");
  state.config = result?.config || {};
  renderConfig();
}

async function refreshAll() {
  elements.refreshButton.disabled = true;
  elements.refreshButton.textContent = "刷新中…";
  try {
    await Promise.all([loadOverview(), loadCurve(), loadConfig()]);
  } finally {
    elements.refreshButton.disabled = false;
    elements.refreshButton.textContent = "刷新数据";
  }
}

async function saveConfig(event) {
  event.preventDefault();
  const payload = {};
  for (const key of formKeys) {
    const input = elements.form.elements.namedItem(key);
    if (!input) {
      continue;
    }
    if (input.type === "checkbox") {
      payload[key] = input.checked;
    } else if (key === "lite_compaction_ratio_threshold") {
      payload[key] = Number(input.value || 0);
    } else {
      payload[key] = Number(input.value || 0);
    }
  }

  elements.saveStatus.textContent = "保存中…";
  try {
    const result = await bridge.apiPost("dashboard/config", payload);
    state.config = result?.config || payload;
    renderConfig();
    elements.saveStatus.textContent = result?.message || "配置已保存";
  } catch (error) {
    elements.saveStatus.textContent = `保存失败：${error.message}`;
  }
}

async function bootstrap() {
  await bridge.ready();
  renderStaticText();
  bridge.onContext(renderStaticText);
  buildGridLines();

  elements.refreshButton.addEventListener("click", refreshAll);
  elements.form.addEventListener("submit", saveConfig);

  await refreshAll();
  window.setInterval(loadOverview, 60000);
  window.setInterval(loadCurve, 60000);
}

bootstrap().catch((error) => {
  elements.saveStatus.textContent = `加载失败：${error.message}`;
});
