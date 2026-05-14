const $ = (selector) => document.querySelector(selector);

const state = {
  inspection: null,
  selectedId: null,
};

const formatBytes = (value = 0) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
};

const identity = (row) => `${row.zone ? `${row.zone}#` : ""}${row.name || row.key || "-"}`;

const setStatus = (text) => {
  $("#packetStatus").textContent = text;
  $("#packetHeaderStatus").textContent = text;
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const renderMetrics = () => {
  const metrics = $("#packetMetrics");
  metrics.replaceChildren();
  const inspection = state.inspection;
  const items = inspection
    ? [
        ["文件大小", formatBytes(inspection.file?.bytes || 0)],
        ["帧数量", inspection.frame_count ?? 0],
        ["TCP 流", inspection.tcp_flow_count ?? 0],
        ["gzip 内容", inspection.summary?.gzip ?? 0],
        ["原始流", inspection.summary?.raw ?? 0],
        ["联盟行", inspection.summary?.members ?? 0],
        ["Boss 行", inspection.summary?.boss ?? 0],
      ]
    : [
        ["文件大小", "-"],
        ["帧数量", "-"],
        ["TCP 流", "-"],
        ["gzip 内容", "-"],
        ["原始流", "-"],
        ["联盟行", "-"],
        ["Boss 行", "-"],
      ];
  for (const [label, value] of items) {
    const item = el("div", "packet-metric");
    item.append(el("span", "", label), el("strong", "", String(value)));
    metrics.append(item);
  }
};

const renderPayloadList = () => {
  const list = $("#payloadList");
  const badge = $("#payloadCountBadge");
  list.replaceChildren();
  const payloads = state.inspection?.payloads || [];
  badge.textContent = String(payloads.length);
  if (!payloads.length) {
    list.append(el("div", "empty-state", state.inspection ? "没有发现可解压的 gzip payload。" : "解析后会显示抓包内的可读内容。"));
    return;
  }
  for (const payload of payloads) {
    const button = el("button", `payload-item${payload.id === state.selectedId ? " active" : ""}`);
    button.type = "button";
    button.dataset.payloadId = payload.id;

    const title = el("div", "payload-item-title");
    title.append(el("span", "badge", `#${payload.id.replace("payload-", "")}`), el("strong", "", payload.packet || "gzip"));
    const meta = [
      payload.complete ? "完整" : "可能不完整",
      payload.encoding === "game-binary" ? "游戏二进制" : payload.encoding === "raw" ? "原始 TCP" : "gzip",
      formatBytes(payload.bytes),
      `${payload.strings?.length || 0} 个字符串`,
      payload.rows?.length ? `${payload.rows.length} 行 ${payload.row_kind === "boss" ? "Boss" : "联盟"}` : "",
    ].filter(Boolean);
    button.append(title, el("small", "", meta.join(" / ")), el("small", "", payload.flow || ""));
    button.addEventListener("click", () => selectPayload(payload.id));
    list.append(button);
  }
};

const renderFlows = () => {
  const list = $("#flowList");
  list.replaceChildren();
  const flows = state.inspection?.flows || [];
  if (!flows.length) {
    list.append(el("div", "empty-state", state.inspection ? "没有发现 TCP 流。" : "解析后会显示 TCP 流。"));
    return;
  }
  for (const flow of flows) {
    const row = el("div", "flow-row");
    row.append(
      el("strong", "", flow.flow),
      el("span", "", `${flow.segment_count} 段 / ${formatBytes(flow.payload_bytes)} payload / gzip ${flow.gzip_count}`)
    );
    list.append(row);
  }
};

const renderStrings = (payload) => {
  const list = $("#stringList");
  list.replaceChildren();
  const strings = payload?.strings || [];
  if (!strings.length) {
    list.append(el("div", "empty-state", "没有提取到长度前缀字符串。"));
    return;
  }
  for (const item of strings) {
    const row = el("div", "string-row");
    row.append(el("span", "", String(item.offset)), el("span", "", item.value));
    list.append(row);
  }
};

const renderInsights = (payload) => {
  const list = $("#insightList");
  list.replaceChildren();
  const insights = payload?.insights || [];
  if (!insights.length) {
    list.append(el("div", "empty-state", "没有针对该 payload 的结构提示。"));
    return;
  }
  for (const insight of insights) {
    const row = el("div", "insight-row");
    row.append(el("span", "", insight.label), el("span", "", insight.value));
    list.append(row);
  }
};

const renderRows = (payload) => {
  const list = $("#rowPreview");
  list.replaceChildren();
  const rows = payload?.rows || [];
  if (!rows.length) {
    list.append(el("div", "empty-state", "没有匹配到已知结构化行。"));
    return;
  }
  for (const row of rows) {
    const line = el("div", "row-preview-row");
    const key = payload.row_kind === "boss" ? `#${row.rank}` : row.row_id || "";
    const value = payload.row_kind === "boss"
      ? `${identity(row)}，${row.damage_k?.toLocaleString?.() || row.damage_k || 0}k`
      : `${identity(row)}，战力 ${(row.power || 0).toLocaleString()}`;
    line.append(el("span", "", key), el("span", "", value));
    list.append(line);
  }
};

const renderWords = (payload) => {
  const list = $("#wordPreview");
  list.replaceChildren();
  const words = payload?.words || [];
  if (!words.length) {
    list.append(el("div", "empty-state", "没有可按 4 字节展示的字段。"));
    return;
  }
  const heading = el("div", "word-row heading");
  heading.append(el("span", "", "offset"), el("span", "", "u32"), el("span", "", "i32"), el("span", "", "float"), el("span", "", "hex"));
  list.append(heading);
  for (const word of words) {
    const row = el("div", "word-row");
    row.append(
      el("span", "", String(word.offset)),
      el("span", "", String(word.u32)),
      el("span", "", String(word.i32)),
      el("span", "", word.float === undefined ? "" : String(word.float)),
      el("span", "", word.hex)
    );
    list.append(row);
  }
};

const selectPayload = (id) => {
  state.selectedId = id;
  const payload = state.inspection?.payloads?.find((item) => item.id === id);
  $("#copyPayloadButton").disabled = !payload;
  $("#openPayloadButton").disabled = !payload;
  $("#payloadDetailTitle").textContent = payload ? payload.packet || "gzip" : "选择一个 payload";
  const header = payload?.game_header
    ? ` / 包长 ${payload.game_header.total} / body ${payload.game_header.body_offset}`
    : "";
  $("#payloadMeta").textContent = payload
    ? `${payload.flow} / offset ${payload.stream_offset}${header} / ${payload.complete ? "完整" : "可能不完整"} / ${formatBytes(payload.bytes)}${payload.text_truncated ? " / 文本已截断" : ""}`
    : "";
  $("#payloadText").textContent = payload?.text || "尚未解析抓包。";
  renderInsights(payload);
  renderStrings(payload);
  renderRows(payload);
  renderWords(payload);
  renderPayloadList();
};

const clearView = () => {
  state.inspection = null;
  state.selectedId = null;
  $("#packetForm").reset();
  renderMetrics();
  renderPayloadList();
  renderFlows();
  selectPayload(null);
  setStatus("等待文件");
};

$("#packetForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("#packetFile").files?.[0];
  if (!file) {
    setStatus("请选择 .pcapng 抓包文件。");
    return;
  }
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = "解析中...";
  setStatus("正在上传抓包文件...");
  try {
    const body = new FormData();
    body.append("pcap", file);
    await new Promise((resolve) => setTimeout(resolve, 30));
    setStatus("正在重建 TCP 流并解压内容...");
    const response = await fetch("/api/inspect-pcap", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "抓包解析失败");
    state.inspection = payload;
    state.selectedId = payload.payloads?.[0]?.id || null;
    renderMetrics();
    renderPayloadList();
    renderFlows();
    selectPayload(state.selectedId);
    setStatus(`解析完成：${payload.summary?.gzip || 0} 个 gzip，${payload.summary?.raw || 0} 个原始流，${payload.tcp_flow_count || 0} 个 TCP 流`);
  } catch (error) {
    setStatus(`抓包解析失败：${error.message}`);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "解析文件";
  }
});

$("#clearPacketButton").addEventListener("click", clearView);

$("#copyPayloadButton").addEventListener("click", async () => {
  const payload = state.inspection?.payloads?.find((item) => item.id === state.selectedId);
  if (!payload) return;
  await navigator.clipboard.writeText(payload.text || "");
  setStatus("已复制当前 payload 文本。");
});

$("#openPayloadButton").addEventListener("click", () => {
  const payload = state.inspection?.payloads?.find((item) => item.id === state.selectedId);
  if (!payload) return;
  $("#dialogTitle").textContent = payload.packet || "Payload 内容";
  $("#dialogText").textContent = payload.text || "";
  $("#payloadDialog").showModal();
});

renderMetrics();
renderPayloadList();
renderFlows();
