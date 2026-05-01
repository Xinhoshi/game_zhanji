const state = {
  data: null,
  selectedId: null,
  selectedWeek: null,
  memberSort: {
    key: "default",
    dir: "asc",
  },
};

const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const formatNumber = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("zh-CN");
};

const formatDamage = (valueK) => {
  if (valueK === null || valueK === undefined) return "基线";
  return `${formatNumber(valueK)}k`;
};

const compactNumber = (value) => {
  const number = Number(value || 0);
  if (number >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
  if (number >= 10000) return `${(number / 10000).toFixed(1)}万`;
  return formatNumber(number);
};

const formatDateTime = (value) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
};

const encodePath = (value) => String(value || "").split("/").map(encodeURIComponent).join("/");
const imageUrl = (folder, file) => {
  if (!file) return "";
  if (String(file).includes("/")) return `./${encodePath(file)}`;
  return `./${encodePath(folder)}/${encodeURIComponent(file)}`;
};

async function fetchState() {
  try {
    const apiResponse = await fetch("/api/state", { cache: "no-store" });
    if (apiResponse.ok) return apiResponse.json();
  } catch (_) {
    // Static preview fallback.
  }
  if (window.LOV_INITIAL_STATE) return window.LOV_INITIAL_STATE;
  const fileResponse = await fetch("./data/state.json", { cache: "no-store" });
  if (!fileResponse.ok) throw new Error("没有找到 data/state.json，请先运行 python server.py 或 tools/lov_parser.py");
  return fileResponse.json();
}

function selectedSnapshot() {
  return state.data.snapshots.at(-1);
}

function previousSnapshot(current) {
  const index = state.data.snapshots.findIndex((item) => item.id === current.id);
  return index > 0 ? state.data.snapshots[index - 1] : null;
}

function byKey(items) {
  return new Map(items.map((item) => [item.key, item]));
}

function normalizedName(value) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, "")
    .toLocaleLowerCase("zh-CN")
    .replace(/[01i|箫]/g, (char) => ({ 0: "o", 1: "l", i: "l", "|": "l", 箫: "萧" })[char]);
}

function memberIdentity(item) {
  const name = normalizedName(item?.name);
  return name ? `${item?.zone ?? ""}#${name}` : String(item?.key || "");
}

function uniqueMemberNames(items) {
  const counts = new Map();
  items.forEach((item) => {
    const name = normalizedName(item.name);
    if (name) counts.set(name, (counts.get(name) || 0) + 1);
  });
  return counts;
}

function memberMatches(left, right, leftNameCounts = new Map(), rightNameCounts = new Map()) {
  if (!left || !right) return false;
  if (left.key && right.key && left.key === right.key) return true;
  if (memberIdentity(left) && memberIdentity(left) === memberIdentity(right)) return true;

  const leftName = normalizedName(left.name);
  const rightName = normalizedName(right.name);
  if (!leftName || leftName !== rightName) return false;

  const sameZone = left.zone !== undefined && left.zone !== null && left.zone === right.zone;
  const uniqueName = (leftNameCounts.get(leftName) || 0) === 1 && (rightNameCounts.get(rightName) || 0) === 1;
  return sameZone || uniqueName;
}

function hasMatchingMember(item, members, itemNameCounts, memberNameCounts) {
  return members.some((member) => memberMatches(item, member, itemNameCounts, memberNameCounts));
}

function getBossDeltas(current) {
  const previous = state.data.snapshots
    .filter((snapshot) => snapshot.week_id === current.week_id && snapshot.captured_at < current.captured_at)
    .at(-1);
  const previousMap = previous ? byKey(previous.boss) : new Map();
  return current.boss.map((boss) => {
    const old = previousMap.get(boss.key);
    const delta = old && boss.damage_k !== null && old.damage_k !== null ? boss.damage_k - old.damage_k : null;
    return { ...boss, delta_k: delta !== null && delta >= 0 ? delta : null, is_baseline: !old };
  });
}

function renderSnapshotOptions() {
  const select = $("#snapshotSelect");
  const current = selectedSnapshot();
  const week = state.selectedWeek || current.week_id;
  const snapshots = state.data.snapshots.filter((snapshot) => snapshot.week_id === week);
  const fallback = snapshots.at(-1) || current;
  if (!snapshots.some((snapshot) => snapshot.id === state.selectedId)) {
    state.selectedId = fallback?.id;
  }
  select.innerHTML = snapshots
    .map((snapshot) => `<option value="${escapeHtml(snapshot.id)}">${formatDateTime(snapshot.captured_at)} · ${escapeHtml(snapshot.id)}</option>`)
    .join("");
  select.value = state.selectedId;
}

function availableWeeks() {
  return [...new Set(state.data.snapshots.map((snapshot) => snapshot.week_id))].sort().reverse();
}

function isWeekArchived(week) {
  return (state.data.archived_boss_weeks || []).includes(week);
}

function renderWeekOptions() {
  const select = $("#weekSelect");
  const current = selectedSnapshot();
  state.selectedWeek = state.selectedWeek || current.week_id;
  const weeks = availableWeeks();
  select.innerHTML = weeks
    .map((week) => `<option value="${escapeHtml(week)}">周起始 ${escapeHtml(week)}${isWeekArchived(week) ? " · 已归档" : ""}</option>`)
    .join("");
  if (!weeks.includes(state.selectedWeek)) {
    state.selectedWeek = weeks[0] || current.week_id;
  }
  select.value = state.selectedWeek;
}

function selectedBossSnapshot() {
  const current = selectedSnapshot();
  const week = state.selectedWeek || current.week_id;
  const snapshots = state.data.snapshots.filter((snapshot) => snapshot.week_id === week);
  return snapshots.find((snapshot) => snapshot.id === state.selectedId) || snapshots.at(-1) || current;
}

function renderStats(current, previous) {
  const members = current.members || [];
  const boss = current.boss || [];
  const powerTotal = members.reduce((sum, item) => sum + (item.power || 0), 0);
  const bossTotal = boss.reduce((sum, item) => sum + (item.damage_k || 0), 0);
  const previousList = previous?.members || [];
  const previousNameCounts = uniqueMemberNames(previousList);
  const currentNameCounts = uniqueMemberNames(members);
  const newCount = previous ? members.filter((item) => !hasMatchingMember(item, previousList, currentNameCounts, previousNameCounts)).length : 0;
  const missingCount = previous ? previousList.filter((item) => !hasMatchingMember(item, members, previousNameCounts, currentNameCounts)).length : 0;
  const reviewCount = members.filter((item) => item.needs_review?.length).length + boss.filter((item) => item.needs_review?.length).length;
  const correctedCount = members.filter((item) => item.corrected).length + boss.filter((item) => item.corrected).length;
  const archiveNote = current.boss_archived ? "Boss周数据已归档锁定" : `已人工修正 ${correctedCount} 条`;
  $("#captureMeta").textContent = `最新节点 ${formatDateTime(current.captured_at)}`;
  $("#archiveMeta").textContent = current.boss_archived ? `Boss周 ${current.week_id} 已归档锁定` : `Boss周 ${current.week_id} 可继续核对`;

  $("#statsGrid").innerHTML = [
    ["members", "成员数", members.length, `新增 ${newCount} / 缺失 ${missingCount}`],
    ["power", "总战斗力", formatNumber(powerTotal), `约 ${compactNumber(powerTotal)}`],
    ["boss", "Boss本周累计", `${formatNumber(bossTotal)}k`, `统计 ${boss.length} 人`],
    ["review", "需复核", reviewCount, archiveNote],
  ]
    .map(([type, label, value, note]) => `<article class="stat stat-${type}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`)
    .join("");
}

function renderBars(current) {
  const boss = current.boss.slice(0, 10);
  const maxDamage = Math.max(...boss.map((item) => item.damage_k || 0), 1);
  $("#bossWeekLabel").textContent = `周起始 ${current.week_id}`;
  $("#bossBars").innerHTML = boss.length
    ? boss
        .map((item, index) => {
          const width = Math.max(2, ((item.damage_k || 0) / maxDamage) * 100);
          return `<div class="bar-row rank-top-${index + 1}" title="${escapeHtml(item.key)} ${formatDamage(item.damage_k)}">
        <span class="rank-pill">No.${item.rank || index + 1}</span>
        <span class="bar-name">${escapeHtml(item.key)}</span>
        <span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${width}%"></span></span>
        <strong>${formatDamage(item.damage_k)}</strong>
      </div>`;
        })
        .join("")
    : `<div class="empty-state">还没有 Boss 识别数据</div>`;

  const members = current.members
    .filter((item) => item.power)
    .slice()
    .sort((a, b) => (b.power || 0) - (a.power || 0))
    .slice(0, 10);
  const maxPower = Math.max(...members.map((item) => item.power || 0), 1);
  $("#powerBars").innerHTML = members.length
    ? members
        .map((item, index) => {
          const width = Math.max(2, ((item.power || 0) / maxPower) * 100);
          return `<div class="bar-row power rank-top-${index + 1}" title="${escapeHtml(item.key)} ${formatNumber(item.power)}">
        <span class="rank-pill">${String(index + 1).padStart(2, "0")}</span>
        <span class="bar-name">${escapeHtml(item.key)}</span>
        <span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${width}%"></span></span>
        <strong>${formatNumber(item.power)}</strong>
      </div>`;
        })
        .join("")
    : `<div class="empty-state">还没有联盟成员战斗力数据</div>`;
}

function reviewTags(item) {
  return [
    item.corrected ? `<span class="tag corrected">已修正</span>` : "",
    item.manual ? `<span class="tag new">手动补录</span>` : "",
    item.needs_review?.length ? `<span class="tag review">复核 ${escapeHtml(item.needs_review.join(", "))}</span>` : "",
  ].join("");
}

function renderBossTable(current) {
  const rows = getBossDeltas(current);
  const locked = isWeekArchived(current.week_id);
  $("#bossTable").innerHTML = rows.length
    ? rows
        .map((item) => {
          const review = reviewTags(item) || `<span class="tag">正常</span>`;
          const delta = item.is_baseline ? `<span class="warn">本周累计基线</span>` : `<span class="delta">+${formatDamage(item.delta_k || 0)}</span>`;
          return `<tr class="${item.rank && item.rank <= 3 ? `rank-table-top-${item.rank}` : ""}">
        <td><span class="rank-pill table-rank">No.${item.rank || "-"}</span></td>
        <td><strong class="cell-name">${escapeHtml(item.key)}</strong></td>
        <td class="metric-cell">${formatDamage(item.damage_k)}</td>
        <td class="metric-cell">${delta}</td>
        <td>${locked ? `${review}<span class="tag corrected">已归档</span>` : review}</td>
        <td><button class="mini-button" data-edit-kind="boss" data-row-id="${escapeHtml(item.row_id)}" ${locked ? "disabled" : ""}>${locked ? "锁定" : "核对"}</button></td>
      </tr>`;
        })
        .join("")
    : `<tr><td colspan="6"><div class="empty-state table-empty">当前周还没有 Boss 数据</div></td></tr>`;
}

function renderArchiveStatus() {
  const status = $("#archiveStatus");
  const button = $("#archiveButton");
  if (!status || !button) return;
  const archived = state.data.archived_boss_weeks || [];
  status.textContent = archived.length ? `已归档：${archived.join("、")}` : "暂无已结束周可归档";
  button.disabled = availableWeeks().length <= 1;
}

function renderMemberTable(current, previous) {
  const search = $("#searchInput").value.trim().toLowerCase();
  const previousList = previous?.members || [];
  const currentList = current.members || [];
  const previousNameCounts = uniqueMemberNames(previousList);
  const currentNameCounts = uniqueMemberNames(currentList);
  const missing = previous
    ? previousList
        .filter((item) => !hasMatchingMember(item, currentList, previousNameCounts, currentNameCounts))
        .map((item) => ({ ...item, missing: true, source_snapshot_id: previous.id }))
    : [];
  const rows = sortMembers([...current.members, ...missing].filter((item) => !search || item.key.toLowerCase().includes(search)));
  renderMemberSortIcons();

  $("#memberTable").innerHTML = rows.length
    ? rows
        .map((item) => {
          const isNew = !item.missing && previous && !hasMatchingMember(item, previousList, currentNameCounts, previousNameCounts);
          const tags = [
            isNew ? `<span class="tag new">新增</span>` : "",
            item.missing ? `<span class="tag missing">缺失</span>` : "",
            reviewTags(item),
          ].join("");
          const rowSnapshotId = item.source_snapshot_id || current.id;
          const raw = item.raw
            ? `<details><summary>查看</summary><code>${escapeHtml([item.raw.identity, item.raw.power, item.raw.last_online].filter(Boolean).join("\n"))}</code></details>`
            : "";
          return `<tr>
        <td><strong class="cell-name">${escapeHtml(item.key)}</strong></td>
        <td class="metric-cell">${formatNumber(item.power)}</td>
        <td>${formatDateTime(item.last_online)}</td>
        <td>${tags || `<span class="tag">在盟</span>`}</td>
        <td>${raw}</td>
        <td><button class="mini-button" data-edit-kind="members" data-row-id="${escapeHtml(item.row_id)}" data-snapshot-id="${escapeHtml(rowSnapshotId)}">核对</button></td>
      </tr>`;
        })
        .join("")
    : `<tr><td colspan="6"><div class="empty-state table-empty">没有匹配的成员</div></td></tr>`;
}

function sortMembers(rows) {
  if (state.memberSort.key === "default") return rows;
  const direction = state.memberSort.dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    if (a.missing !== b.missing) return a.missing ? 1 : -1;
    if (state.memberSort.key === "power") {
      return (((a.power ?? -1) - (b.power ?? -1)) * direction) || String(a.key).localeCompare(String(b.key), "zh-Hans-CN");
    }
    return (((a.zone ?? 999999) - (b.zone ?? 999999)) * direction) || String(a.name).localeCompare(String(b.name), "zh-Hans-CN");
  });
}

function renderMemberSortIcons() {
  const zoneIcon = $("#memberSortIcon");
  const powerIcon = $("#powerSortIcon");
  const resetButton = $("#resetMemberSortButton");
  if (!zoneIcon || !powerIcon) return;
  zoneIcon.textContent = state.memberSort.key === "zone" ? (state.memberSort.dir === "asc" ? "↑" : "↓") : "↕";
  powerIcon.textContent = state.memberSort.key === "power" ? (state.memberSort.dir === "asc" ? "↑" : "↓") : "↕";
  if (resetButton) {
    resetButton.disabled = state.memberSort.key === "default";
    resetButton.classList.toggle("is-active", state.memberSort.key === "default");
  }
}

function renderImages(current) {
  const memberImage = $("#memberImage");
  const bossImage = $("#bossImage");
  const memberFile = current.images?.members;
  const bossFile = current.images?.boss;
  memberImage.closest("figure").hidden = !memberFile;
  bossImage.closest("figure").hidden = !bossFile;
  if (memberFile) memberImage.src = imageUrl(current.folder, memberFile);
  if (bossFile) bossImage.src = imageUrl(current.folder, bossFile);
}

function render() {
  if (!state.data?.snapshots?.length) return;
  const current = selectedSnapshot();
  const previous = previousSnapshot(current);
  renderWeekOptions();
  renderSnapshotOptions();
  renderStats(current, previous);
  renderBars(current);
  renderBossTable(selectedBossSnapshot());
  renderMemberTable(current, previous);
  renderImages(current);
  renderArchiveStatus();
}

async function reloadState() {
  state.data = await fetchState();
  state.selectedWeek = state.data.snapshots.at(-1)?.week_id;
  state.selectedId = state.data.snapshots.filter((snapshot) => snapshot.week_id === state.selectedWeek).at(-1)?.id;
  render();
}

function findSnapshotById(snapshotId) {
  return state.data.snapshots.find((snapshot) => snapshot.id === snapshotId);
}

function reviewSnapshot(kind, snapshotId = null) {
  if (snapshotId) return findSnapshotById(snapshotId) || (kind === "boss" ? selectedBossSnapshot() : selectedSnapshot());
  return kind === "boss" ? selectedBossSnapshot() : selectedSnapshot();
}

function findRow(kind, rowId, snapshotId = null) {
  const snapshot = reviewSnapshot(kind, snapshotId);
  return (snapshot[kind] || []).find((item) => item.row_id === rowId);
}

function ensureReviewModal() {
  let modal = $("#reviewModal");
  if (modal) return modal;
  document.body.insertAdjacentHTML(
    "beforeend",
    `<dialog id="reviewModal" class="review-modal">
      <form method="dialog" id="reviewForm">
        <div class="modal-head">
          <div>
            <p class="eyebrow">Review</p>
            <h2 id="reviewTitle">识别核对</h2>
          </div>
          <button type="button" class="icon-button" data-close-review>×</button>
        </div>
        <div id="reviewFields" class="review-fields"></div>
        <div id="reviewRaw" class="review-raw"></div>
        <div class="modal-actions">
          <button type="button" class="secondary-button" data-close-review>取消</button>
          <button type="submit">保存修正</button>
        </div>
        <p id="reviewStatus" class="hint"></p>
      </form>
    </dialog>`,
  );
  modal = $("#reviewModal");
  modal.querySelectorAll("[data-close-review]").forEach((button) => button.addEventListener("click", () => modal.close()));
  $("#reviewForm").addEventListener("submit", saveCorrection);
  return modal;
}

function field(name, label, value, type = "text") {
  return `<label><span>${label}</span><input name="${name}" type="${type}" value="${escapeHtml(value ?? "")}" /></label>`;
}

function openReview(kind, rowId = null, snapshotId = null) {
  const snapshot = reviewSnapshot(kind, snapshotId);
  const row = rowId ? findRow(kind, rowId, snapshot.id) : null;
  const isMember = kind === "members";
  const generatedRowId = rowId || `manual-${isMember ? "m" : "b"}-${Date.now()}`;
  const modal = ensureReviewModal();
  modal.dataset.kind = kind;
  modal.dataset.rowId = generatedRowId;
  modal.dataset.snapshotId = snapshot.id;
  $("#reviewTitle").textContent = row ? `核对 ${row.key}` : `手动补录${isMember ? "成员" : "Boss记录"}`;

  $("#reviewFields").innerHTML = isMember
    ? [
        field("zone", "区服", row?.zone ?? "", "number"),
        field("name", "人名（支持日文、特殊符号）", row?.name ?? ""),
        field("power", "战斗力", row?.power ?? "", "number"),
        field("last_online", "上线时间（YYYY-MM-DDTHH:mm:ss，可留空）", row?.last_online ?? ""),
        field("note", "修正备注", row?.raw?.correction_note ?? ""),
      ].join("")
    : [
        field("zone", "区服", row?.zone ?? "", "number"),
        field("name", "人名（支持日文、特殊符号）", row?.name ?? ""),
        field("rank", "排行", row?.rank ?? "", "number"),
        field("damage_k", "伤害（单位 k，只填数字）", row?.damage_k ?? "", "number"),
        field("note", "修正备注", row?.raw?.correction_note ?? ""),
      ].join("");

  const rawText = row?.raw ? Object.entries(row.raw).map(([key, value]) => `${key}: ${value}`).join("\n") : "手动补录";
  $("#reviewRaw").innerHTML = `<h3>原始识别</h3><pre>${escapeHtml(rawText)}</pre><p>当前时间节点：${escapeHtml(snapshot.id)}</p>`;
  $("#reviewStatus").textContent = "";
  modal.showModal();
}

async function saveCorrection(event) {
  event.preventDefault();
  const modal = $("#reviewModal");
  const form = event.currentTarget;
  const formData = new FormData(form);
  const kind = modal.dataset.kind;
  const rowId = modal.dataset.rowId;
  const values = Object.fromEntries(formData.entries());
  values.reviewed = true;

  $("#reviewStatus").textContent = "正在保存...";
  try {
    const response = await fetch("/api/corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot_id: modal.dataset.snapshotId || (kind === "boss" ? selectedBossSnapshot() : selectedSnapshot()).id, kind, row_id: rowId, values }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "保存失败");
    state.data = payload.state;
    modal.close();
    render();
  } catch (error) {
    $("#reviewStatus").textContent = `保存失败：${error.message}。请确认是通过 python server.py 打开的页面。`;
  }
}

document.addEventListener("click", (event) => {
  const editButton = event.target.closest("[data-edit-kind]");
  if (editButton) {
    openReview(editButton.dataset.editKind, editButton.dataset.rowId, editButton.dataset.snapshotId);
    return;
  }
  if (event.target.closest("#addMemberButton")) {
    openReview("members");
  }
  if (event.target.closest("#resetMemberSortButton")) {
    state.memberSort = { key: "default", dir: "asc" };
    render();
  }
});

$("#snapshotSelect").addEventListener("change", (event) => {
  state.selectedId = event.target.value;
  render();
});

$("#weekSelect").addEventListener("change", (event) => {
  state.selectedWeek = event.target.value;
  state.selectedId = null;
  render();
});

$("#searchInput").addEventListener("input", render);

document.querySelectorAll("[data-member-sort]").forEach((button) => {
  button.addEventListener("click", () => {
    const key = button.dataset.memberSort;
    if (state.memberSort.key === key) {
      if (state.memberSort.dir === "asc") {
        state.memberSort.dir = "desc";
      } else {
        state.memberSort = { key: "default", dir: "asc" };
      }
    } else {
      state.memberSort.key = key;
      state.memberSort.dir = key === "power" ? "desc" : "asc";
    }
    render();
  });
});

$("#uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("#uploadStatus");
  const form = event.currentTarget;
  const memberFile = form.elements.member.files[0];
  const bossFile = form.elements.boss.files[0];
  if (!memberFile && !bossFile) {
    status.textContent = "请至少选择联盟截图或 Boss 截图中的一张。";
    return;
  }
  status.textContent = "正在上传并识别...";
  try {
    const response = await fetch("/api/upload", { method: "POST", body: new FormData(form) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "上传失败");
    state.data = payload.state;
    state.selectedWeek = payload.snapshot.week_id;
    state.selectedId = payload.state.snapshots.filter((snapshot) => snapshot.week_id === state.selectedWeek).at(-1)?.id;
    form.reset();
    status.textContent = `已分开保存并识别：${payload.snapshot.id}`;
    render();
  } catch (error) {
    status.textContent = `上传失败：${error.message}`;
  }
});

$("#reparseButton").addEventListener("click", async () => {
  const status = $("#uploadStatus");
  status.textContent = "正在重新识别所有本地时间节点...";
  try {
    const response = await fetch("/api/reparse", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "重新识别失败");
    state.data = payload;
    state.selectedWeek = payload.snapshots.at(-1)?.week_id;
    state.selectedId = payload.snapshots.filter((snapshot) => snapshot.week_id === state.selectedWeek).at(-1)?.id;
    status.textContent = `已重新识别 ${payload.snapshot_count} 个时间节点，人工修正会继续生效`;
    render();
  } catch (error) {
    status.textContent = `重新识别失败：${error.message}`;
  }
});

$("#archiveButton")?.addEventListener("click", async () => {
  const status = $("#archiveStatus");
  status.textContent = "正在归档已结束周...";
  try {
    const response = await fetch("/api/archive-boss-weeks", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "归档失败");
    state.data = payload.state;
    status.textContent = payload.archived_boss_weeks?.length ? `已归档：${payload.archived_boss_weeks.join("、")}` : "暂无已结束周可归档";
    render();
  } catch (error) {
    status.textContent = `归档失败：${error.message}`;
  }
});

reloadState().catch((error) => {
  document.body.innerHTML = `<main class="shell"><section class="panel"><div class="panel-head"><div><p class="eyebrow">Error</p><h2>无法加载战报数据</h2></div></div><div style="padding:18px;color:#8fa1bb;line-height:1.7">${escapeHtml(error.message)}</div></section></main>`;
});
