const state = {
  data: null,
  selectedId: null,
  selectedWeek: null,
  selectedArchiveWeek: null,
  memberGroupFilter: "all",
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
  if (valueK === null || valueK === undefined) return "\u57fa\u7ebf";
  return `${formatNumber(valueK)}k`;
};

const formatDelta = (valueK) => {
  if (valueK === null || valueK === undefined) return "-";
  return `+${formatDamage(valueK)}`;
};

const compactNumber = (value) => {
  const number = Number(value || 0);
  if (number >= 100000000) return `${(number / 100000000).toFixed(2)}\u4ebf`;
  if (number >= 10000) return `${(number / 10000).toFixed(1)}\u4e07`;
  return formatNumber(number);
};

const formatDateTime = (value) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
};

const snapshotOptionLabel = (snapshot) => {
  const uploadLabel = `\u4e0a\u4f20 ${formatDateTime(snapshot.captured_at)}`;
  const identity = escapeHtml(snapshot.id);
  if (!snapshot?.boss_captured_at) return `${uploadLabel} \u00b7 ${identity}`;
  return `Boss\u5f52\u5c5e ${formatDateTime(snapshot.boss_captured_at)} \u00b7 ${uploadLabel} \u00b7 ${identity}`;
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
  if (!fileResponse.ok) throw new Error("\u6ca1\u6709\u627e\u5230 data/state.json\uff0c\u8bf7\u5148\u8fd0\u884c python server.py \u6216 tools/lov_parser.py");
  return fileResponse.json();
}

function selectedSnapshot() {
  return state.data.snapshots.at(-1);
}

function latestSnapshotWith(kind) {
  return state.data.snapshots.filter((snapshot) => snapshot.images?.[kind] || snapshot[kind]?.length).at(-1) || selectedSnapshot();
}

function latestSnapshotImageSource(kind) {
  if (kind === "members") {
    return state.data.snapshots.filter((snapshot) => snapshot.folder?.includes("records/members/") || snapshot.id?.endsWith("_\u8054\u76df")).at(-1) || latestSnapshotWith(kind);
  }
  return state.data.snapshots.filter((snapshot) => snapshot.images?.[kind]).at(-1) || latestSnapshotWith(kind);
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
    .replace(/[01i|]/g, (char) => ({ 0: "o", 1: "l", i: "l", "|": "l" })[char]);
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
  if (!leftName || !rightName) return false;

  const sameZone = left.zone !== undefined && left.zone !== null && left.zone === right.zone;
  const uniqueName = (leftNameCounts.get(leftName) || 0) === 1 && (rightNameCounts.get(rightName) || 0) === 1;
  const hasCjk = /[\u4e00-\u9fff]/.test(`${left.name || ""}${right.name || ""}`);
  const similarSameZone = sameZone && hasCjk && leftName.length === rightName.length && leftName.length >= 2 && (leftName[0] === rightName[0] || leftName.at(-1) === rightName.at(-1));
  if (leftName !== rightName) return similarSameZone;
  return sameZone || uniqueName;
}

function hasMatchingMember(item, members, itemNameCounts, memberNameCounts) {
  return members.some((member) => memberMatches(item, member, itemNameCounts, memberNameCounts));
}

function hasOwnBossData(snapshot) {
  return Boolean(snapshot?.boss?.length) && !snapshot.boss_carried_forward;
}

function getBossDeltas(current) {
  const currentWeek = current.boss_week_id || current.week_id;
  const previous = state.data.snapshots
    .filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === currentWeek && snapshot.captured_at < current.captured_at)
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
  const week = state.selectedWeek || current.boss_week_id || current.week_id;
  const snapshots = state.data.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === week);
  const fallback = snapshots.at(-1) || current;
  if (!snapshots.some((snapshot) => snapshot.id === state.selectedId)) {
    state.selectedId = fallback?.id;
  }
  select.innerHTML = snapshots
    .map((snapshot) => `<option value="${escapeHtml(snapshot.id)}">${snapshotOptionLabel(snapshot)}</option>`)
    .join("");
  select.value = state.selectedId;
}

function availableWeeks() {
  return [...new Set(state.data.snapshots.map((snapshot) => snapshot.boss_week_id || snapshot.week_id))].sort().reverse();
}

function isWeekArchived(week) {
  return (state.data.archived_boss_weeks || []).includes(week);
}

function renderWeekOptions() {
  const select = $("#weekSelect");
  const current = selectedSnapshot();
  state.selectedWeek = state.selectedWeek || current.boss_week_id || current.week_id;
  const weeks = availableWeeks();
  select.innerHTML = weeks
    .map((week) => `<option value="${escapeHtml(week)}">\u5468\u8d77\u59cb ${escapeHtml(week)}${isWeekArchived(week) ? " \u00b7 \u5df2\u5f52\u6863" : ""}</option>`)
    .join("");
  if (!weeks.includes(state.selectedWeek)) state.selectedWeek = weeks[0] || current.boss_week_id || current.week_id;
  select.value = state.selectedWeek;
}

function selectedBossSnapshot() {
  const current = selectedSnapshot();
  const week = state.selectedWeek || current.boss_week_id || current.week_id;
  const snapshots = state.data.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === week);
  return snapshots.find((snapshot) => snapshot.id === state.selectedId) || snapshots.at(-1) || current;
}

function bossSnapshotsForWeek(weekId) {
  return state.data.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === weekId);
}

function snapshotDayIndex(snapshot) {
  if (snapshot.boss_day_index !== undefined && snapshot.boss_day_index !== null) return snapshot.boss_day_index;
  const value = snapshot.boss_captured_at || snapshot.captured_at;
  const date = new Date(snapshot.captured_at);
  const effectiveDate = new Date(value);
  if (!Number.isNaN(effectiveDate.getTime())) return (effectiveDate.getDay() + 6) % 7;
  if (Number.isNaN(date.getTime())) return null;
  return (date.getDay() + 6) % 7;
}

function latestBossSnapshotsByDay(weekId) {
  const byDay = new Map();
  bossSnapshotsForWeek(weekId).forEach((snapshot) => {
    const day = snapshotDayIndex(snapshot);
    if (day === null) return;
    const previous = byDay.get(day);
    if (!previous || snapshot.captured_at > previous.captured_at) byDay.set(day, snapshot);
  });
  return byDay;
}

function bossMap(snapshot) {
  return new Map((snapshot?.boss || []).map((item) => [item.key, item]));
}

function previousBossSnapshotInWeek(current) {
  return bossSnapshotsForWeek(current.boss_week_id || current.week_id).filter((snapshot) => snapshot.captured_at < current.captured_at).at(-1);
}

function bossDailyDelta(item, daySnapshot, weekSnapshots) {
  if (!daySnapshot) return null;
  const dayBoss = bossMap(daySnapshot).get(item.key);
  if (!dayBoss || dayBoss.damage_k === null || dayBoss.damage_k === undefined) return null;
  const previous = weekSnapshots.filter((snapshot) => snapshot.captured_at < daySnapshot.captured_at).at(-1);
  const previousBoss = previous ? bossMap(previous).get(item.key) : null;
  if (!previousBoss || previousBoss.damage_k === null || previousBoss.damage_k === undefined) return dayBoss.damage_k;
  const delta = dayBoss.damage_k - previousBoss.damage_k;
  return delta >= 0 ? delta : null;
}

const bossDayLabels = new Map([
  [0, "\u5468\u4e00"],
  [1, "\u5468\u4e8c"],
  [2, "\u5468\u4e09"],
  [3, "\u5468\u56db"],
  [4, "\u5468\u4e94"],
  [5, "\u5468\u516d"],
  [6, "\u5468\u65e5"],
]);

function visibleBossDayOrder(current) {
  const currentDay = snapshotDayIndex(current);
  const allDays = [6, 5, 4, 3, 2, 1, 0];
  if (currentDay === null) return allDays;
  return allDays.filter((day) => day <= currentDay);
}

function renderRankingBars(target, rows, metricKey, valueFormatter, emptyText) {
  const maxValue = Math.max(...rows.map((item) => item[metricKey] || 0), 1);
  target.innerHTML = rows.length
    ? rows.map((item, index) => {
        const width = Math.max(2, ((item[metricKey] || 0) / maxValue) * 100);
        return `<div class="bar-row rank-top-${index + 1}" title="${escapeHtml(item.key)} ${valueFormatter(item[metricKey])}"><span class="rank-pill">No.${item.rank || index + 1}</span><span class="bar-name">${escapeHtml(item.key)}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${width}%"></span></span><strong>${valueFormatter(item[metricKey])}</strong></div>`;
      }).join("")
    : `<div class="empty-state">${emptyText}</div>`;
}

function archiveByWeek(weekId) {
  return (state.data.boss_archives || []).find((archive) => archive.week_id === weekId);
}

function renderRankingBarsHtml(rows, metricKey, valueFormatter, emptyText) {
  const maxValue = Math.max(...rows.map((item) => item[metricKey] || 0), 1);
  return rows.length
    ? rows.map((item, index) => {
        const width = Math.max(2, ((item[metricKey] || 0) / maxValue) * 100);
        return `<div class="bar-row rank-top-${index + 1}" title="${escapeHtml(item.key)} ${valueFormatter(item[metricKey])}"><span class="rank-pill">No.${item.rank || index + 1}</span><span class="bar-name">${escapeHtml(item.key)}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${width}%"></span></span><strong>${valueFormatter(item[metricKey])}</strong></div>`;
      }).join("")
    : `<div class="empty-state">${emptyText}</div>`;
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
  const archiveNote = current.boss_archived ? "Boss\u5468\u6570\u636e\u5df2\u5f52\u6863\u9501\u5b9a" : `\u5df2\u4eba\u5de5\u4fee\u6b63 ${correctedCount} \u6761`;
  $("#captureMeta").textContent = `\u6700\u65b0\u8282\u70b9 ${formatDateTime(current.captured_at)}`;
  $("#archiveMeta").textContent = current.boss_archived ? `Boss\u5468 ${current.boss_week_id || current.week_id} \u5df2\u5f52\u6863\u9501\u5b9a` : `Boss\u5468 ${current.boss_week_id || current.week_id} \u53ef\u7ee7\u7eed\u6838\u5bf9`;
  $("#statsGrid").innerHTML = [["members", "\u6210\u5458\u6570", members.length, `\u65b0\u589e ${newCount} / \u7f3a\u5931 ${missingCount}`], ["power", "\u603b\u6218\u6597\u529b", formatNumber(powerTotal), `\u7ea6 ${compactNumber(powerTotal)}`], ["boss", "Boss\u672c\u5468\u7d2f\u8ba1", `${formatNumber(bossTotal)}k`, `\u7edf\u8ba1 ${boss.length} \u4eba`], ["review", "\u9700\u590d\u6838", reviewCount, archiveNote]].map(([type, label, value, note]) => `<article class="stat stat-${type}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
}

function renderBars(current) {
  const boss = current.boss.slice(0, 10);
  $("#bossWeekLabel").textContent = `\u5468\u8d77\u59cb ${current.boss_week_id || current.week_id}`;
  renderRankingBars($("#bossBars"), boss, "damage_k", formatDamage, "\u8fd8\u6ca1\u6709 Boss \u8bc6\u522b\u6570\u636e");

  const members = current.members
    .filter((item) => item.power)
    .slice()
    .sort((a, b) => (b.power || 0) - (a.power || 0))
    .slice(0, 10)
    .map((item, index) => ({ ...item, rank: index + 1 }));
  renderRankingBars($("#powerBars"), members, "power", formatNumber, "\u8fd8\u6ca1\u6709\u8054\u76df\u6210\u5458\u6218\u6597\u529b\u6570\u636e");
}

function reviewTags(item) {
  return [item.corrected ? `<span class="tag corrected">\u5df2\u4fee\u6b63</span>` : "", item.manual ? `<span class="tag new">\u624b\u52a8\u8865\u5f55</span>` : "", item.needs_review?.length ? `<span class="tag review">\u590d\u6838 ${escapeHtml(item.needs_review.join(", "))}</span>` : ""].join("");
}

function renderBossTable(current) {
  const currentWeek = current.boss_week_id || current.week_id;
  const weekSnapshots = bossSnapshotsForWeek(currentWeek);
  const previousSnapshot = previousBossSnapshotInWeek(current);
  const previousMap = bossMap(previousSnapshot);
  const dailySnapshots = latestBossSnapshotsByDay(currentWeek);
  const dayOrder = visibleBossDayOrder(current);
  const headerRow = $("#bossHeaderRow");
  if (headerRow) {
    headerRow.innerHTML = ["\u6392\u884c", "\u6210\u5458", "\u672c\u5468\u7d2f\u8ba1", ...dayOrder.map((day) => bossDayLabels.get(day)), "\u672c\u6b21\u589e\u91cf", "\u8bc6\u522b\u72b6\u6001", "\u64cd\u4f5c"].map((label) => `<th>${label}</th>`).join("");
  }
  const rows = current.boss.map((boss) => {
    const old = previousMap.get(boss.key);
    const delta = old && boss.damage_k !== null && old.damage_k !== null ? boss.damage_k - old.damage_k : null;
    return { ...boss, delta_k: delta !== null && delta >= 0 ? delta : null, is_baseline: !old, daily_deltas: Object.fromEntries(dayOrder.map((day) => [day, bossDailyDelta(boss, dailySnapshots.get(day), weekSnapshots)])) };
  });
  const locked = isWeekArchived(currentWeek);
  const columnCount = dayOrder.length + 6;
  $("#bossTable").innerHTML = rows.length ? rows.map((item) => {
    const review = reviewTags(item) || `<span class="tag">\u6b63\u5e38</span>`;
    const dailyCells = dayOrder.map((day) => `<td class="metric-cell day-cell">${formatDelta(item.daily_deltas[day])}</td>`).join("");
    const delta = item.is_baseline ? `<span class="warn">\u672c\u5468\u7d2f\u8ba1\u57fa\u7ebf</span>` : `<span class="delta">${formatDelta(item.delta_k || 0)}</span>`;
    return `<tr class="${item.rank && item.rank <= 3 ? `rank-table-top-${item.rank}` : ""}"><td><span class="rank-pill table-rank">No.${item.rank || "-"}</span></td><td><strong class="cell-name">${escapeHtml(item.key)}</strong></td><td class="metric-cell">${formatDamage(item.damage_k)}</td>${dailyCells}<td class="metric-cell">${delta}</td><td>${locked ? `${review}<span class="tag corrected">\u5df2\u5f52\u6863</span>` : review}</td><td><button class="mini-button" data-edit-kind="boss" data-row-id="${escapeHtml(item.row_id)}" ${locked ? "disabled" : ""}>${locked ? "\u9501\u5b9a" : "\u6838\u5bf9"}</button></td></tr>`;
  }).join("") : `<tr><td colspan="${columnCount}"><div class="empty-state table-empty">\u5f53\u524d\u5468\u8fd8\u6ca1\u6709 Boss \u6570\u636e</div></td></tr>`;
}

function renderArchiveStatus() {
  const status = $("#archiveStatus"); const button = $("#archiveButton"); if (!status || !button) return;
  const archived = state.data.archived_boss_weeks || [];
  status.textContent = archived.length ? `\u5df2\u5f52\u6863\uff1a${archived.join("\u3001")}` : "\u6682\u65e0\u5df2\u7ed3\u675f\u5468\u53ef\u5f52\u6863";
  button.disabled = availableWeeks().length <= 1;
}

function renderArchiveHistory() {
  const stats = state.data.archive_stats || { rows: [], week_count: 0, total_damage_k: 0, member_count: 0 };
  const summary = $("#archiveSummary");
  const table = $("#archiveTable");
  if (!summary || !table) return;
  summary.innerHTML = [["\u5f52\u6863\u5468\u6570", stats.week_count || 0], ["\u5386\u53f2\u603b\u4f24\u5bb3", `${formatNumber(stats.total_damage_k || 0)}k`], ["\u53c2\u4e0e\u6210\u5458", stats.member_count || 0]].map(([label, value]) => `<article class="archive-stat"><span>${label}</span><strong>${value}</strong></article>`).join("");
  if (!stats.rows?.length) {
    table.innerHTML = `<tr><td colspan="7"><div class="empty-state table-empty">\u6682\u65e0\u5386\u53f2\u5f52\u6863</div></td></tr>`;
    return;
  }
  table.innerHTML = stats.rows.map((row) => {
    const selected = state.selectedArchiveWeek === row.week_id;
    const baseRow = `<tr class="archive-row ${selected ? "is-selected" : ""}" data-archive-week="${escapeHtml(row.week_id || "")}" tabindex="0"><td>${escapeHtml(row.week_id || "-")}</td><td>${formatDateTime(row.archived_at)}</td><td>${escapeHtml(row.source_snapshot_id || "-")}</td><td class="metric-cell">${formatNumber(row.member_count)}</td><td class="metric-cell">${formatDamage(row.total_damage_k)}</td><td>${escapeHtml(row.leader_key || "-")}</td><td class="metric-cell">${formatDamage(row.leader_damage_k)}</td></tr>`;
    return selected ? baseRow + renderArchiveExpandedRow(row.week_id) : baseRow;
  }).join("");
}

function renderArchiveExpandedRow(weekId) {
  const archive = archiveByWeek(weekId);
  if (!archive) return "";
  const bossRows = (archive.boss || []).slice().sort((a, b) => (a.rank || 9999) - (b.rank || 9999));
  const totalDamage = archive.total_damage_k ?? bossRows.reduce((sum, item) => sum + (item.damage_k || 0), 0);
  const leader = bossRows[0] || {};
  const statCards = [["boss", "Boss\u603b\u4f24\u5bb3", `${formatNumber(totalDamage)}k`, `\u7edf\u8ba1 ${bossRows.length} \u4eba`], ["members", "\u53c2\u4e0e\u6210\u5458", bossRows.length, archive.week_id], ["review", "\u7b2c\u4e00\u540d", escapeHtml(leader.key || "-"), formatDamage(leader.damage_k)], ["power", "\u5f52\u6863\u72b6\u6001", archive.locked ? "\u5df2\u9501\u5b9a" : "-", "\u5386\u53f2\u6570\u636e"]].map(([type, label, value, note]) => `<article class="stat stat-${type}"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  const bars = renderRankingBarsHtml(bossRows.slice(0, 10), "damage_k", formatDamage, "\u6682\u65e0 Boss \u5f52\u6863\u6570\u636e");
  const memberBars = renderRankingBarsHtml(bossRows.slice(0, 10), "damage_k", formatDamage, "\u6682\u65e0\u5f52\u6863\u6210\u5458\u6570\u636e");
  return `<tr class="archive-expanded-row"><td colspan="7"><section class="archive-inline-detail"><div class="panel-head archive-detail-head"><div><p class="eyebrow">Archive Detail</p><h3>\u5386\u53f2\u5f52\u6863 ${escapeHtml(archive.week_id)}</h3><p class="hint">\u6765\u6e90\u8282\u70b9 ${escapeHtml(archive.source_snapshot_id || "-")} \u00b7 \u5f52\u6863\u65f6\u95f4 ${formatDateTime(archive.archived_at)}</p></div></div><section class="stats-grid">${statCards}</section><section class="leaderboard-grid"><article class="panel ranking-panel"><div class="panel-head compact"><div><p class="eyebrow">Boss</p><h2>Boss \u5386\u53f2\u4f24\u5bb3\u6392\u884c</h2></div><span class="badge">\u5468\u8d77\u59cb ${escapeHtml(archive.week_id)}</span></div><div class="bars">${bars}</div></article><article class="panel ranking-panel"><div class="panel-head compact"><div><p class="eyebrow">Members</p><h2>\u5f52\u6863\u6210\u5458 Top 10</h2></div></div><div class="bars">${memberBars}</div></article></section></section></td></tr>`;
}

function renderArchiveDetail() {}

function renderMemberTable(current, previous) {
  const search = $("#searchInput").value.trim().toLowerCase();
  const groupFilter = state.memberGroupFilter || "all";
  const previousList = previous?.members || [];
  const currentList = current.members || [];
  const previousNameCounts = uniqueMemberNames(previousList);
  const currentNameCounts = uniqueMemberNames(currentList);
  const missing = previous
    ? previousList
        .filter((item) => !hasMatchingMember(item, currentList, previousNameCounts, currentNameCounts))
        .map((item) => ({ ...item, missing: true, source_snapshot_id: previous.member_source_id || previous.id }))
    : [];
  const rows = sortMembers(
    [...current.members, ...missing].filter((item) => {
      const matchesText = !search || item.key.toLowerCase().includes(search);
      const matchesGroup = groupFilter === "all" || (groupFilter === "in" ? !!item.in_group : !item.in_group);
      return matchesText && matchesGroup;
    }),
  );
  renderMemberSortIcons();

  $("#memberTable").innerHTML = rows.length
    ? rows
        .map((item) => {
          const isNew = !item.missing && previous && !hasMatchingMember(item, previousList, currentNameCounts, previousNameCounts);
          const tags = [
            isNew ? `<span class="tag new">\u65b0\u589e</span>` : "",
            item.missing ? `<span class="tag missing">\u7f3a\u5931</span>` : "",
            reviewTags(item),
          ].join("");
          const rowSnapshotId = item.source_snapshot_id || current.id;
          const saveSnapshotId = item.source_snapshot_id || current.member_source_id || current.id;
          const raw = item.raw ? `<details><summary>\u67e5\u770b</summary><code>${escapeHtml([item.raw.identity, item.raw.power, item.raw.last_online].filter(Boolean).join("\n"))}</code></details>` : "";
          const nameClass = item.in_group ? "cell-name in-group-name" : "cell-name";
          const deleteButton = item.manual && !item.missing ? `<button class="mini-button danger-button" data-delete-kind="members" data-row-id="${escapeHtml(item.row_id)}" data-save-snapshot-id="${escapeHtml(saveSnapshotId)}">\u5220\u9664</button>` : "";
          return `<tr class="${item.in_group ? "member-in-group" : ""}"><td><strong class="${nameClass}">${escapeHtml(item.key)}</strong></td><td class="metric-cell">${formatNumber(item.power)}</td><td>${formatDateTime(item.last_online)}</td><td>${tags || `<span class="tag">\u5728\u76df</span>`}</td><td>${raw}</td><td><div class="row-actions"><button class="mini-button" data-edit-kind="members" data-row-id="${escapeHtml(item.row_id)}" data-snapshot-id="${escapeHtml(rowSnapshotId)}" data-save-snapshot-id="${escapeHtml(saveSnapshotId)}">\u6838\u5bf9</button>${deleteButton}</div></td></tr>`;
        })
        .join("")
    : `<tr><td colspan="6"><div class="empty-state table-empty">\u6ca1\u6709\u5339\u914d\u7684\u6210\u5458</div></td></tr>`;
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
  const zoneIcon = $("#memberSortIcon"); const powerIcon = $("#powerSortIcon"); const resetButton = $("#resetMemberSortButton");
  if (!zoneIcon || !powerIcon) return;
  zoneIcon.textContent = state.memberSort.key === "zone" ? (state.memberSort.dir === "asc" ? "\u2191" : "\u2193") : "\u2195";
  powerIcon.textContent = state.memberSort.key === "power" ? (state.memberSort.dir === "asc" ? "\u2191" : "\u2193") : "\u2195";
  if (resetButton) { resetButton.disabled = state.memberSort.key === "default"; resetButton.classList.toggle("is-active", state.memberSort.key === "default"); }
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
  renderArchiveHistory();
  renderArchiveDetail();
}

async function reloadState() {
  state.data = await fetchState();
  const latestBoss = state.data.snapshots.filter(hasOwnBossData).at(-1) || state.data.snapshots.at(-1);
  state.selectedWeek = latestBoss?.boss_week_id || latestBoss?.week_id;
  state.selectedId = state.data.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === state.selectedWeek).at(-1)?.id;
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
      <form method="dialog" id="reviewForm" class="review-shell">
        <header class="review-head">
          <div>
            <p class="eyebrow">OCR Review</p>
            <h2 id="reviewTitle">\u8bc6\u522b\u6838\u5bf9</h2>
            <p id="reviewSubtitle" class="review-subtitle"></p>
          </div>
          <button type="button" class="icon-button close-review-button" data-close-review aria-label="\u5173\u95ed"><span aria-hidden="true">\u00d7</span></button>
        </header>
        <div class="review-body">
          <section class="review-main" id="reviewFields"></section>
          <aside class="review-side" id="reviewRaw"></aside>
        </div>
        <footer class="review-actions">
          <p id="reviewStatus" class="hint"></p>
          <div class="review-action-buttons">
            <button type="button" class="secondary-button" data-close-review>\u53d6\u6d88</button>
            <button type="submit">\u4fdd\u5b58</button>
          </div>
        </footer>
      </form>
    </dialog>`,
  );
  modal = $("#reviewModal");
  modal.querySelectorAll("[data-close-review]").forEach((button) => button.addEventListener("click", () => modal.close()));
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.close();
  });
  modal.addEventListener("click", (event) => {
    const switchButton = event.target.closest("[data-switch-field]");
    if (!switchButton) return;
    const input = modal.querySelector(`#${CSS.escape(switchButton.dataset.switchField)}`);
    if (!input) return;
    input.checked = !input.checked;
    switchButton.setAttribute("aria-checked", input.checked ? "true" : "false");
    switchButton.classList.toggle("is-checked", input.checked);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  modal.addEventListener("pointerdown", (event) => {
    const rawBlock = event.target.closest(".review-raw pre");
    if (!rawBlock) return;
    rawBlock.dataset.dragging = "true";
    rawBlock.dataset.dragStartX = String(event.clientX);
    rawBlock.dataset.dragScrollLeft = String(rawBlock.scrollLeft);
    rawBlock.setPointerCapture?.(event.pointerId);
  });
  modal.addEventListener("pointermove", (event) => {
    const rawBlock = event.target.closest(".review-raw pre");
    if (!rawBlock || rawBlock.dataset.dragging !== "true") return;
    const startX = Number(rawBlock.dataset.dragStartX || event.clientX);
    const startScrollLeft = Number(rawBlock.dataset.dragScrollLeft || rawBlock.scrollLeft);
    rawBlock.scrollLeft = startScrollLeft - (event.clientX - startX);
  });
  modal.addEventListener("pointerup", (event) => {
    const rawBlock = event.target.closest(".review-raw pre");
    if (!rawBlock) return;
    rawBlock.dataset.dragging = "false";
    rawBlock.releasePointerCapture?.(event.pointerId);
  });
  $("#reviewForm").addEventListener("submit", saveCorrection);
  return modal;
}

function field(name, label, value, type = "text", hint = "") {
  return `<label class="review-field"><span>${label}</span><input name="${name}" type="${type}" value="${escapeHtml(value ?? "")}" />${hint ? `<small>${hint}</small>` : ""}</label>`;
}

function checkboxField(name, label, checked = false, hint = "") {
  const fieldId = `review-${name}`;
  return `<div class="switch-field"><input id="${fieldId}" class="switch-input" name="${name}" type="checkbox" ${checked ? "checked" : ""} tabindex="-1" aria-hidden="true" /><button type="button" class="switch-control ${checked ? "is-checked" : ""}" data-switch-field="${fieldId}" role="switch" aria-checked="${checked ? "true" : "false"}"><span class="switch-track" aria-hidden="true"></span><span><strong>${label}</strong>${hint ? `<small>${hint}</small>` : ""}</span></button></div>`;
}

function reviewSection(title, fields) {
  return `<fieldset class="review-section"><legend>${title}</legend>${fields.join("")}</fieldset>`;
}

function openReview(kind, rowId = null, snapshotId = null, saveSnapshotId = null) {
  const snapshot = snapshotId ? reviewSnapshot(kind, snapshotId) : kind === "members" ? selectedSnapshot() : selectedBossSnapshot();
  const row = rowId ? findRow(kind, rowId, snapshot.id) : null;
  const isMember = kind === "members";
  const generatedRowId = rowId || `manual-${isMember ? "m" : "b"}-${Date.now()}`;
  const modal = ensureReviewModal();
  modal.dataset.kind = kind;
  modal.dataset.rowId = generatedRowId;
  modal.dataset.snapshotId = snapshot.id;
  modal.dataset.saveSnapshotId = saveSnapshotId || snapshot.id;
  modal.dataset.groupOnly = "false";
  $("#reviewTitle").textContent = row ? `\u6838\u5bf9 ${row.key}` : `\u624b\u52a8\u8865\u5f55${isMember ? "\u6210\u5458" : "Boss\u8bb0\u5f55"}`;
  $("#reviewSubtitle").textContent = `${isMember ? "\u8054\u76df\u6210\u5458" : "Boss\u4f24\u5bb3"} \u00b7 ${snapshot.id}`;

  $("#reviewFields").innerHTML = isMember
    ? [
        reviewSection("\u57fa\u672c\u8eab\u4efd", [
          field("zone", "\u533a\u670d", row?.zone ?? "", "number"),
          field("name", "\u4eba\u540d", row?.name ?? "", "text", "\u652f\u6301\u65e5\u6587\u548c\u7279\u6b8a\u7b26\u53f7"),
        ]),
        reviewSection("\u6210\u5458\u6570\u636e", [
          field("power", "\u6218\u6597\u529b", row?.power ?? "", "number"),
          field("last_online", "\u4e0a\u7ebf\u65f6\u95f4", row?.last_online ?? "", "text", "YYYY-MM-DDTHH:mm:ss"),
        ]),
        reviewSection("\u7fa4\u72b6\u6001", [checkboxField("in_group", "\u662f\u5426\u5728\u7fa4", row?.in_group ?? false, "\u53ea\u4fee\u6539\u6b64\u9879\u4e0d\u4f1a\u8bb0\u4e3a\u5df2\u4fee\u6b63")]),
        reviewSection("\u5907\u6ce8", [field("note", "\u4fee\u6b63\u5907\u6ce8", row?.raw?.correction_note ?? "")]),
      ].join("")
    : [
        reviewSection("\u57fa\u672c\u8eab\u4efd", [field("zone", "\u533a\u670d", row?.zone ?? "", "number"), field("name", "\u4eba\u540d", row?.name ?? "")]),
        reviewSection("Boss \u6570\u636e", [field("rank", "\u6392\u884c", row?.rank ?? "", "number"), field("damage_k", "\u4f24\u5bb3\uff08k\uff09", row?.damage_k ?? "", "number", "\u53ea\u586b\u6570\u5b57")]),
        reviewSection("\u5907\u6ce8", [field("note", "\u4fee\u6b63\u5907\u6ce8", row?.raw?.correction_note ?? "")]),
      ].join("");

  if (isMember && row) {
    const initialValues = {
      zone: String(row.zone ?? ""),
      name: String(row.name ?? ""),
      power: String(row.power ?? ""),
      last_online: String(row.last_online ?? ""),
      note: String(row.raw?.correction_note ?? ""),
    };
    $("#reviewFields").addEventListener("input", () => {
      const form = $("#reviewForm");
      modal.dataset.groupOnly = ["zone", "name", "power", "last_online", "note"].every((key) => String(form.elements[key]?.value ?? "") === initialValues[key]) ? "true" : "false";
    }, { once: false });
  }
  const rawText = row?.raw ? Object.entries(row.raw).map(([key, value]) => `${key}: ${value}`).join("\n") : "\u624b\u52a8\u8865\u5f55";
  $("#reviewRaw").innerHTML = `<h3>\u539f\u59cb\u8bc6\u522b</h3><pre>${escapeHtml(rawText)}</pre><dl><dt>\u4fdd\u5b58\u5230</dt><dd>${escapeHtml(modal.dataset.saveSnapshotId)}</dd><dt>\u5f53\u524d\u5c55\u793a</dt><dd>${escapeHtml(snapshot.id)}</dd></dl>`;
  $("#reviewStatus").textContent = "";
  modal.showModal();
}

async function saveCorrection(event) {
  event.preventDefault(); const modal = $("#reviewModal"); const form = event.currentTarget; const formData = new FormData(form); const kind = modal.dataset.kind; const rowId = modal.dataset.rowId; const values = Object.fromEntries(formData.entries()); if (kind === "members") { values.in_group = formData.has("in_group"); values.group_only = modal.dataset.groupOnly === "true"; } values.reviewed = true;
  $("#reviewStatus").textContent = "\u6b63\u5728\u4fdd\u5b58...";
  try { const response = await fetch("/api/corrections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ snapshot_id: modal.dataset.saveSnapshotId || modal.dataset.snapshotId || (kind === "boss" ? selectedBossSnapshot() : selectedSnapshot()).id, kind, row_id: rowId, values }) }); const payload = await response.json(); if (!response.ok) throw new Error(payload.error || "\u4fdd\u5b58\u5931\u8d25"); state.data = payload.state; modal.close(); render(); } catch (error) { $("#reviewStatus").textContent = `\u4fdd\u5b58\u5931\u8d25\uff1a${error.message}\u3002\u8bf7\u786e\u8ba4\u662f\u901a\u8fc7 python server.py \u6253\u5f00\u7684\u9875\u9762\u3002`; }
}


async function deleteCorrection(kind, rowId, snapshotId) {
  if (!rowId || !snapshotId) return;
  const confirmed = window.confirm("\u786e\u5b9a\u5220\u9664\u8fd9\u6761\u624b\u52a8\u8865\u5f55\u6210\u5458\u5417\uff1f");
  if (!confirmed) return;
  try {
    const response = await fetch("/api/corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot_id: snapshotId, kind, row_id: rowId, delete: true }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "\u5220\u9664\u5931\u8d25");
    state.data = payload.state;
    render();
  } catch (error) {
    window.alert(`\u5220\u9664\u5931\u8d25\uff1a${error.message}`);
  }
}

document.addEventListener("click", (event) => {
  const editButton = event.target.closest("[data-edit-kind]");
  if (editButton) {
    openReview(editButton.dataset.editKind, editButton.dataset.rowId, editButton.dataset.snapshotId, editButton.dataset.saveSnapshotId);
    return;
  }
  if (event.target.closest("#addMemberButton")) {
    const current = selectedSnapshot();
    openReview("members", null, current.id, current.member_source_id || current.id);
  }
  const deleteButton = event.target.closest("[data-delete-kind]");
  if (deleteButton) {
    deleteCorrection(deleteButton.dataset.deleteKind, deleteButton.dataset.rowId, deleteButton.dataset.saveSnapshotId);
    return;
  }
  if (event.target.closest("#resetMemberSortButton")) {
    state.memberSort = { key: "default", dir: "asc" };
    render();
  }
});

document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-archive-week]");
  if (!row) return;
  state.selectedArchiveWeek = state.selectedArchiveWeek === row.dataset.archiveWeek ? null : row.dataset.archiveWeek;
  renderArchiveHistory();
});

document.addEventListener("keydown", (event) => {
  const row = event.target.closest?.("[data-archive-week]");
  if (!row || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  row.click();
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
$("#groupFilter").addEventListener("change", (event) => {
  state.memberGroupFilter = event.target.value;
  render();
});

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
    status.textContent = "\u8bf7\u81f3\u5c11\u9009\u62e9\u8054\u76df\u622a\u56fe\u6216 Boss \u622a\u56fe\u4e2d\u7684\u4e00\u5f20\u3002";
    return;
  }
  status.textContent = "\u6b63\u5728\u4e0a\u4f20\u5e76\u8bc6\u522b...";
  try {
    const response = await fetch("/api/upload", { method: "POST", body: new FormData(form) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "\u4e0a\u4f20\u5931\u8d25");
    state.data = payload.state;
    state.selectedWeek = payload.snapshot.week_id;
    state.selectedId = payload.state.snapshots.filter((snapshot) => snapshot.week_id === state.selectedWeek).at(-1)?.id;
    form.reset();
    status.textContent = `\u5df2\u4fdd\u5b58\u5e76\u8bc6\u522b\uff1a${payload.snapshot.id}`;
    render();
  } catch (error) {
    status.textContent = `\u4e0a\u4f20\u5931\u8d25\uff1a${error.message}`;
  }
});

$("#reparseButton").addEventListener("click", async () => {
  const status = $("#uploadStatus");
  status.textContent = "\u6b63\u5728\u91cd\u626b\u672a\u5f52\u6863\u5468\u7684\u672c\u5730\u622a\u56fe\u548c\u6293\u5305\u6570\u636e...";
  try {
    const response = await fetch("/api/reparse", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "\u91cd\u65b0\u8bc6\u522b\u5931\u8d25");
    state.data = payload;
    const latestBoss = payload.snapshots.filter(hasOwnBossData).at(-1) || payload.snapshots.at(-1);
    state.selectedWeek = latestBoss?.boss_week_id || latestBoss?.week_id;
    state.selectedId = payload.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === state.selectedWeek).at(-1)?.id;
    const summary = payload.reparse_summary || {};
    status.textContent = `\u5df2\u91cd\u626b\u672a\u5f52\u6863\u8282\u70b9 ${summary.reparsed_count ?? payload.snapshot_count} \u4e2a\uff0c\u6293\u5305 ${summary.packet_count ?? 0} \u4e2a\uff0c\u5df2\u8df3\u8fc7\u5f52\u6863\u8282\u70b9 ${summary.loaded_archived_count ?? 0} \u4e2a`;
    render();
  } catch (error) {
    status.textContent = `\u91cd\u65b0\u8bc6\u522b\u5931\u8d25\uff1a${error.message}`;
  }
});


$("#importPcapButton")?.addEventListener("click", async () => {
  const form = $("#uploadForm");
  const status = $("#uploadStatus");
  const importButton = $("#importPcapButton");
  const submitButton = form.querySelector('button[type="submit"]');
  const reparseButton = $("#reparseButton");
  const pcapFile = form.elements.pcap?.files?.[0];
  if (!pcapFile) {
    status.textContent = "\u8bf7\u9009\u62e9 .pcapng \u6293\u5305\u6587\u4ef6\u3002";
    return;
  }
  const body = new FormData();
  body.append("pcap", pcapFile);
  const originalLabel = importButton?.textContent || "\u5bfc\u5165\u6293\u5305";
  if (importButton) {
    importButton.disabled = true;
    importButton.textContent = "\u5bfc\u5165\u4e2d...";
  }
  if (submitButton) submitButton.disabled = true;
  if (reparseButton) reparseButton.disabled = true;
  status.textContent = "\u6b63\u5728\u4e0a\u4f20\u6293\u5305\u6587\u4ef6...";
  try {
    await new Promise((resolve) => setTimeout(resolve, 30));
    status.textContent = "\u6b63\u5728\u89e3\u6790\u6293\u5305\uff0c\u8bc6\u522b\u8054\u76df\u548c Boss \u6570\u636e...";
    const response = await fetch("/api/import-pcap", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "\u6293\u5305\u5bfc\u5165\u5931\u8d25");
    state.data = payload.state;
    const latestBoss = payload.state.snapshots.filter(hasOwnBossData).at(-1) || payload.snapshot;
    state.selectedWeek = latestBoss?.boss_week_id || latestBoss?.week_id;
    state.selectedId = payload.state.snapshots.filter((snapshot) => hasOwnBossData(snapshot) && (snapshot.boss_week_id || snapshot.week_id) === state.selectedWeek).at(-1)?.id;
    form.elements.pcap.value = "";
    const labels = new Map([
      ["SCLogic_RankInfoBack", "Boss"],
      ["SCLogic_GetUnionMebInfoBack", "\u8054\u76df"],
    ]);
    const summaryParts = [
      payload.summary?.members ? `\u8054\u76df ${payload.summary.members} \u6761` : "",
      payload.summary?.boss ? `Boss ${payload.summary.boss} \u6761` : "",
    ].filter(Boolean);
    const counts = summaryParts.length ? summaryParts.join(" / ") : payload.diagnostics
      ?.filter((item) => labels.has(item.packet))
      .map((item) => `${labels.get(item.packet)} ${item.rows} \u6761`)
      .join(" / ");
    status.textContent = `\u6293\u5305\u5bfc\u5165\u5b8c\u6210\uff1a${counts || payload.imported.join("\u3001")}`;
    render();
  } catch (error) {
    status.textContent = `\u6293\u5305\u5bfc\u5165\u5931\u8d25\uff1a${error.message}`;
  } finally {
    if (importButton) {
      importButton.disabled = false;
      importButton.textContent = originalLabel;
    }
    if (submitButton) submitButton.disabled = false;
    if (reparseButton) reparseButton.disabled = false;
  }
});

$("#archiveButton")?.addEventListener("click", async () => {
  const status = $("#archiveStatus");
  status.textContent = "\u6b63\u5728\u5f52\u6863\u5df2\u7ed3\u675f\u5468...";
  try {
    const response = await fetch("/api/archive-boss-weeks", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "\u5f52\u6863\u5931\u8d25");
    state.data = payload.state;
    status.textContent = payload.archived_boss_weeks?.length ? `\u5df2\u5f52\u6863\uff1a${payload.archived_boss_weeks.join("\u3001")}` : "\u6682\u65e0\u5df2\u7ed3\u675f\u5468\u53ef\u5f52\u6863";
    render();
  } catch (error) {
    status.textContent = `\u5f52\u6863\u5931\u8d25\uff1a${error.message}`;
  }
});

reloadState().catch((error) => {
  document.body.innerHTML = `<main class="shell"><section class="panel"><div class="panel-head"><div><p class="eyebrow">Error</p><h2>无法加载战报数据</h2></div></div><div style="padding:18px;color:#8fa1bb;line-height:1.7">${escapeHtml(error.message)}</div></section></main>`;
});
