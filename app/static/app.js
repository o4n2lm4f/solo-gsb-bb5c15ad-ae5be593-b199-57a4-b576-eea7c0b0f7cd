"use strict";

const $ = (s) => document.querySelector(s);
const STORAGE_KEY = "decision-desk-session";

let state = null;
let busy = false;
let selectedPair = null; // 冲突环上待重新判定的组合 {a, b}
let focusPair = null; // 跳转到推荐组合后的当前评审组合 {a, b}，null 表示按原顺序
let basisSelection = []; // 「排名依据」面板中已选中的候选项（至多 2 个）
let basisReq = 0; // 排名依据请求序号，丢弃过期响应
let basisData = null; // 当前面板展示的排名依据数据

/* ---------- 工具 ---------- */

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 2800);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch (_) { /* 忽略非 JSON 响应 */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- 视图 ---------- */

function showSetup() {
  $("#setup-view").classList.remove("hidden");
  $("#review-view").classList.add("hidden");
}

function render() {
  $("#setup-view").classList.add("hidden");
  $("#review-view").classList.remove("hidden");

  // 停用/恢复后安全清空引用停用候选的跳转、复核选择与排名依据选择
  const enabled = new Set(state.candidates);
  if (focusPair && (!enabled.has(focusPair.a) || !enabled.has(focusPair.b))) {
    focusPair = null;
  }
  if (selectedPair && (!enabled.has(selectedPair.a) || !enabled.has(selectedPair.b))) {
    selectedPair = null;
  }
  if (basisSelection.some((n) => !enabled.has(n))) {
    basisSelection = basisSelection.filter((n) => enabled.has(n));
    basisData = null;
    basisReq += 1; // 丢弃在途的旧（含停用候选）排名依据响应
  }

  const { done, total } = state.progress;
  $("#progress-bar").style.width = total ? `${(done / total) * 100}%` : "0%";
  $("#progress-text").textContent = `${done} / ${total}`;

  // 跳转组合已不在待比较列表（已判定/撤销/重置）时自动回到原顺序
  if (focusPair && !state.pending.some((p) => p.a === focusPair.a && p.b === focusPair.b)) {
    focusPair = null;
  }
  const active = focusPair || state.current_pair;
  $("#current-pair").classList.toggle("hidden", !active);
  $("#done-banner").classList.toggle("hidden", !!active);
  if (active) {
    $("#btn-a").textContent = active.a;
    $("#btn-b").textContent = active.b;
  }
  $("#jump-banner").classList.toggle("hidden", !focusPair);
  if (focusPair && state.current_pair) {
    $("#jump-original").textContent = `${state.current_pair.a} ↔ ${state.current_pair.b}`;
  }
  $("#undo-btn").disabled = !state.can_undo;

  renderRanking();
  renderPending();
  renderMatrix();
  renderConflict();
  renderNextPair();
  renderBasis();
  renderCandidates();
}

/* ---------- 候选管理：停用 / 恢复 ---------- */

function renderCandidates() {
  const box = $("#candidate-manage");
  box.innerHTML = "";

  const makeGroup = (title, names, disabled) => {
    const head = document.createElement("p");
    head.className = "cand-group-title";
    head.textContent = title;
    const list = document.createElement("div");
    list.className = "cand-list";
    if (names.length === 0) {
      const none = document.createElement("span");
      none.className = "empty muted";
      none.textContent = "（无）";
      list.appendChild(none);
    }
    for (const name of names) {
      const item = document.createElement("span");
      item.className = "cand-item" + (disabled ? " disabled-cand" : "");
      const label = document.createElement("span");
      label.className = "cand-item-name";
      label.textContent = name;
      const btn = document.createElement("button");
      btn.type = "button";
      if (disabled) {
        btn.className = "cand-on";
        btn.textContent = "恢复";
        btn.title = `恢复「${name}」：按最初录入顺序回到评审，已有判断沿用，缺失组合重新待评审`;
        btn.addEventListener("click", () => reactivateCandidate(name));
      } else {
        btn.className = "cand-off";
        // 至少保留 3 个启用候选：只剩 3 个时禁用按钮
        const canDisable = state.candidates.length > 3;
        btn.disabled = !canDisable;
        btn.textContent = "停用";
        btn.title = canDisable
          ? `停用「${name}」：保留其全部判断记录，只是不参与进度、排名、冲突与推荐`
          : "至少保留 3 个启用候选";
        btn.addEventListener("click", () => deactivateCandidate(name));
      }
      item.append(label, btn);
      list.appendChild(item);
    }
    box.append(head, list);
  };

  // 启用组按最初录入顺序（state.candidates 已过滤保持原序）；停用组同序
  makeGroup(`启用中的候选（${state.candidates.length}）`, state.candidates, false);
  makeGroup(`已停用的候选（${state.disabled.length}；判断记录已保留）`, state.disabled, true);
}

async function deactivateCandidate(name) {
  if (busy || !state) return;
  if (!confirm(
    `确定停用「${name}」吗？\n` +
    "其全部判断记录都会保留（不删除），但进度、待比较组合、Schulze 排名、矩阵、\n" +
    "循环冲突、最少改判与下一对推荐将只按其余启用候选重算。\n" +
    "恢复后已有组合沿用原判断，仅未比较的组合重新进入待评审。"
  )) {
    return;
  }
  busy = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/candidates/deactivate`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    stateChanged();
    toast(`已停用「${name}」，判断记录已保留`);
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
  }
}

async function reactivateCandidate(name) {
  if (busy || !state) return;
  busy = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/candidates/reactivate`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    stateChanged();
    toast(`已恢复「${name}」：已有组合沿用原判断，缺失组合重新待评审`);
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
  }
}

function renderRanking() {
  renderRankingInto($("#ranking"), state.ranking, true);
}

function renderPending() {
  const box = $("#pending-list");
  box.innerHTML = "";
  if (state.pending.length === 0) {
    const span = document.createElement("span");
    span.className = "empty";
    span.textContent = "✓ 全部组合均已比较";
    box.appendChild(span);
    return;
  }
  for (const p of state.pending) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${p.a} ↔ ${p.b}`;
    box.appendChild(chip);
  }
}

const SYMBOLS = { win: "✓", lose: "✗", tie: "＝", pending: "·", self: "—" };

function conflictCells() {
  // 冲突环上每个组合对应的两个矩阵格（i:j 与 j:i）
  const cells = new Set();
  if (state.conflict) {
    const pos = new Map(state.candidates.map((name, i) => [name, i]));
    for (const e of state.conflict.edges) {
      const i = pos.get(e.a);
      const j = pos.get(e.b);
      cells.add(`${i}:${j}`).add(`${j}:${i}`);
    }
  }
  return cells;
}

function suggestionCells() {
  // 最少改判方案中各组合对应的两个矩阵格
  const cells = new Set();
  if (state.suggestion) {
    const pos = new Map(state.candidates.map((name, i) => [name, i]));
    for (const p of state.suggestion.pairs) {
      const i = pos.get(p.a);
      const j = pos.get(p.b);
      cells.add(`${i}:${j}`).add(`${j}:${i}`);
    }
  }
  return cells;
}

function renderMatrix() {
  const wrap = $("#matrix-wrap");
  wrap.innerHTML = "";
  const conflicts = conflictCells();
  const suggested = suggestionCells();
  const table = document.createElement("table");
  table.className = "matrix";

  const headRow = document.createElement("tr");
  const corner = document.createElement("th");
  corner.className = "matrix-corner";
  corner.textContent = "选两项";
  corner.title = "点击行/列标题选择两个候选项，查看双向最强路径与排名依据";
  headRow.appendChild(corner);
  for (let j = 0; j < state.candidates.length; j++) {
    const name = state.candidates[j];
    const th = document.createElement("th");
    th.textContent = name;
    th.title = basisSelection.includes(name)
      ? `${name}（已选择，再次点击取消）`
      : `选择 ${name} 作为排名依据的第 ${basisSelection.length + 1} 个候选项`;
    th.classList.add("matrix-pick");
    if (basisSelection.includes(name)) th.classList.add("matrix-pick-selected");
    th.addEventListener("click", () => toggleBasisCandidate(name));
    headRow.appendChild(th);
  }
  const thead = document.createElement("thead");
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  state.matrix.forEach((cells, i) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = state.candidates[i];
    th.title = basisSelection.includes(state.candidates[i])
      ? `${state.candidates[i]}（已选择，再次点击取消）`
      : `选择 ${state.candidates[i]} 作为排名依据的第 ${basisSelection.length + 1} 个候选项`;
    th.classList.add("matrix-pick");
    if (basisSelection.includes(state.candidates[i])) th.classList.add("matrix-pick-selected");
    th.addEventListener("click", () => toggleBasisCandidate(state.candidates[i]));
    tr.appendChild(th);
    cells.forEach((cell, j) => {
      const td = document.createElement("td");
      td.className = `cell-${cell}`;
      td.textContent = SYMBOLS[cell];
      const key = `${i}:${j}`;
      const isBasisPair = i !== j && basisSelection.length === 2 &&
        basisSelection.includes(state.candidates[i]) &&
        basisSelection.includes(state.candidates[j]);
      if (conflicts.has(key)) {
        td.classList.add("cell-conflict");
        td.title = "循环冲突组合，点击重新判定";
        td.addEventListener("click", () => {
          selectConflictPair({
            a: state.candidates[Math.min(i, j)],
            b: state.candidates[Math.max(i, j)],
          });
        });
      } else if (suggested.has(key)) {
        td.classList.add("cell-suggestion");
        td.title = "最少改判方案：点击可手动重新判定";
        td.addEventListener("click", () => {
          selectConflictPair({
            a: state.candidates[Math.min(i, j)],
            b: state.candidates[Math.max(i, j)],
          });
        });
      } else if (i !== j) {
        // 普通格：直接选中该组合查看排名依据（冲突/建议格仍优先复核操作）
        td.classList.add("cell-basis-pick");
        if (isBasisPair) td.classList.add("cell-basis");
        td.title = "点击查看这两个候选项的排名依据";
        td.addEventListener("click", () => {
          pickBasisPair(state.candidates[i], state.candidates[j]);
        });
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
}

/* ---------- 循环冲突复核 ---------- */

function renderConflict() {
  const card = $("#conflict-card");
  const conflict = state.conflict;
  if (!conflict) {
    selectedPair = null;
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  $("#conflict-path").textContent = [...conflict.cycle, conflict.cycle[0]].join(" → ");

  // 选中组合已不在当前环上、也不在改判建议中（如经撤销/重判）时收起面板
  if (
    selectedPair &&
    !conflict.edges.some((e) => e.a === selectedPair.a && e.b === selectedPair.b) &&
    !(state.suggestion &&
      state.suggestion.pairs.some((p) => p.a === selectedPair.a && p.b === selectedPair.b))
  ) {
    selectedPair = null;
  }

  const box = $("#conflict-pairs");
  box.innerHTML = "";
  for (const e of conflict.edges) {
    const chip = document.createElement("button");
    const winner = e.result === "a" ? e.a : e.b;
    const loser = e.result === "a" ? e.b : e.a;
    chip.className =
      "chip conflict-chip" +
      (selectedPair && selectedPair.a === e.a && selectedPair.b === e.b ? " selected" : "");
    chip.textContent = `${winner} 胜 ${loser}`;
    chip.title = "点击重新判定该组合";
    chip.addEventListener("click", () => selectConflictPair(e));
    box.appendChild(chip);
  }

  const panel = $("#revise-panel");
  panel.classList.toggle("hidden", !selectedPair);
  if (selectedPair) {
    $("#revise-title").textContent = `重新判定：${selectedPair.a} ↔ ${selectedPair.b}`;
    $("#revise-a").textContent = `「${selectedPair.a}」胜`;
    $("#revise-b").textContent = `「${selectedPair.b}」胜`;
  }

  renderSuggestion();
}

function renderRankingInto(box, groups, selectable = false) {
  box.innerHTML = "";
  let rank = 1; // 竞争排名：并列后跳名次
  for (const group of groups) {
    const row = document.createElement("div");
    row.className = "rank-row" + (group.length > 1 ? " tied" : "");
    const badge = document.createElement("span");
    badge.className = "rank-badge";
    badge.textContent = `第 ${rank} 名`;
    const names = document.createElement("span");
    names.className = "rank-names";
    if (selectable) {
      // 每个候选项单独可点（并列组内亦可任选两个比较）
      group.forEach((name, i) => {
        if (i > 0) names.appendChild(document.createTextNode(" ＝ "));
        const item = document.createElement("span");
        item.className =
          "rank-name-item" + (basisSelection.includes(name) ? " selected" : "");
        item.textContent = name;
        item.title = "点击加入/移出「排名依据」选择（需两个候选项）";
        item.addEventListener("click", () => toggleBasisCandidate(name));
        names.appendChild(item);
      });
    } else {
      names.textContent = group.join(" ＝ ");
    }
    row.append(badge, names);
    box.appendChild(row);
    rank += group.length;
  }
}

function renderSuggestion() {
  const panel = $("#suggestion-panel");
  const s = state.suggestion;
  if (!s) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#suggestion-count").textContent = `共 ${s.change_count} 个组合`;
  $("#suggestion-num").textContent = s.change_count;

  const box = $("#suggestion-pairs");
  box.innerHTML = "";
  for (const p of s.pairs) {
    const chip = document.createElement("button");
    chip.className =
      "chip suggestion-chip" +
      (selectedPair && selectedPair.a === p.a && selectedPair.b === p.b ? " selected" : "");
    chip.textContent = `${p.a} ＝ ${p.b}`;
    chip.title = "点击可手动重新判定该组合";
    chip.addEventListener("click", () => selectConflictPair(p));
    box.appendChild(chip);
  }

  const affected = $("#suggestion-affected");
  affected.innerHTML = "";
  for (const item of s.affected) {
    const row = document.createElement("div");
    row.className = "affected-row";
    const winner = item.from === "a" ? item.a : item.b;
    const loser = item.from === "a" ? item.b : item.a;
    row.innerHTML =
      `<span class="affected-from">${winner} 胜 ${loser}</span>` +
      `<span class="affected-arrow">→</span>` +
      `<span class="affected-to">${item.a} ＝ ${item.b}（平局）</span>`;
    affected.appendChild(row);
  }

  renderRankingInto($("#suggestion-ranking"), s.preview_ranking);
}

function selectConflictPair(pair) {
  selectedPair = { a: pair.a, b: pair.b };
  renderConflict();
  $("#conflict-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- 下一对推荐 ---------- */

function renderNextPair() {
  const card = $("#nextpair-card");
  const np = state.next_pair;
  if (!np) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  $("#nextpair-name").textContent = `${np.a} ↔ ${np.b}`;
  if (np.impact > 0) {
    $("#nextpair-reason").textContent =
      `三种结果下共有 ${np.impact} / ${np.candidate_pairs} 个候选对的相对名次` +
      `（前 / 后 / 并列）会发生变化，为当前所有待比较组合中影响最大；` +
      `影响分相同的组合按原待比较顺序推荐。`;
  } else {
    $("#nextpair-reason").textContent =
      "当前所有待比较组合的结果都不会再改变任何候选对的相对名次，可按原顺序完成剩余比较。";
  }
  $("#nextpair-title-a").textContent = `若「${np.a}」胜`;
  $("#nextpair-title-b").textContent = `若「${np.b}」胜`;
  renderRankingInto($("#nextpair-rank-a"), np.previews.a);
  renderRankingInto($("#nextpair-rank-tie"), np.previews.tie);
  renderRankingInto($("#nextpair-rank-b"), np.previews.b);

  const cur = state.current_pair;
  const isCurrent = cur && cur.a === np.a && cur.b === np.b;
  $("#nextpair-jump").classList.toggle("hidden", !!isCurrent);
  $("#nextpair-is-current").classList.toggle("hidden", !isCurrent);
}

function jumpToRecommended() {
  if (busy || !state || !state.next_pair) return;
  focusPair = { a: state.next_pair.a, b: state.next_pair.b };
  render();
  $("#current-pair").scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ---------- 排名依据 ---------- */

function toggleBasisCandidate(name) {
  const pos = basisSelection.indexOf(name);
  if (pos >= 0) {
    basisSelection.splice(pos, 1);
  } else {
    if (basisSelection.length >= 2) basisSelection.shift(); // 保留最新两个
    basisSelection.push(name);
  }
  basisData = null;
  basisReq += 1; // 使任何在途请求失效
  renderRanking();
  renderMatrix();
  renderBasis();
  if (basisSelection.length === 2) {
    $("#basis-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function pickBasisPair(a, b) {
  basisSelection = [a, b];
  basisData = null;
  basisReq += 1;
  renderRanking();
  renderMatrix();
  renderBasis();
  $("#basis-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function clearBasis() {
  basisSelection = [];
  basisData = null;
  basisReq += 1;
  renderRanking();
  renderMatrix();
  renderBasis();
}

function renderPathBlock(title, path, lead) {
  const block = document.createElement("div");
  block.className = "basis-path" + (lead ? " lead" : "");
  const head = document.createElement("p");
  head.className = "basis-path-title";
  head.textContent = title;
  block.appendChild(head);

  if (!path.exists) {
    const none = document.createElement("div");
    none.className = "basis-none";
    none.textContent = "无有效路径（不存在可到达的比较链）";
    block.appendChild(none);
    return block;
  }

  const meta = document.createElement("div");
  meta.className = "basis-meta";
  const strength = document.createElement("span");
  strength.className = "basis-strength";
  strength.textContent = `瓶颈强度 ${path.strength} · ${path.hops} 条边`;
  meta.appendChild(strength);
  block.appendChild(meta);

  const chain = document.createElement("div");
  chain.className = "basis-chain";
  path.nodes.forEach((node, k) => {
    if (k > 0) {
      const e = path.edges[k - 1];
      const edge = document.createElement("span");
      edge.className = "basis-edge " + (e.kind === "win" ? "edge-win" : "edge-tie");
      edge.textContent = e.kind === "win" ? " 胜 →" : " ＝平局＝";
      edge.title = `边强度 ${e.strength}（${e.kind === "win" ? "直接胜负" : "直接平局"}）`;
      chain.appendChild(edge);
    }
    const nodeEl = document.createElement("span");
    nodeEl.className = "basis-node";
    nodeEl.textContent = node;
    chain.appendChild(nodeEl);
  });
  block.appendChild(chain);
  return block;
}

function renderBasis() {
  const card = $("#basis-card");

  // 新一轮候选项变化后，剔除失效选择
  if (state && basisSelection.some((n) => !state.candidates.includes(n))) {
    basisSelection = basisSelection.filter((n) => state.candidates.includes(n));
    basisData = null;
  }

  if (!state || basisSelection.length < 2) {
    card.classList.add("hidden");
    return;
  }
  const [a, b] = basisSelection;
  card.classList.remove("hidden");
  $("#basis-pair").textContent = `${a} ↔ ${b}`;

  const box = $("#basis-directions");

  // 以服务端数据为准；无数据或选择已变化时重新拉取（正反序共用同一响应）
  const samePair = basisData &&
    ((basisData.a === a && basisData.b === b) || (basisData.a === b && basisData.b === a));
  if (!samePair) {
    box.innerHTML = "";
    const loadHint = document.createElement("p");
    loadHint.className = "muted";
    loadHint.textContent = "正在重建最强路径…";
    box.appendChild(loadHint);
    loadBasis(a, b);
    return;
  }

  // 服务端按请求顺序返回；用户按相反顺序选择时整体交换两个方向展示
  let data = basisData;
  if (data.a !== a) {
    data = {
      a,
      b,
      verdict: data.verdict === "a" ? "b" : data.verdict === "b" ? "a" : "tie",
      strength_ab: data.strength_ba,
      strength_ba: data.strength_ab,
      path_ab: data.path_ba,
      path_ba: data.path_ab,
    };
  }

  const verdict = $("#basis-verdict");
  if (data.verdict === "a") {
    verdict.textContent = `判定：「${a}」领先于「${b}」（${a}→${b} 最强 ${data.strength_ab} ＞ 反向 ${data.strength_ba}）`;
    verdict.className = "basis-verdict verdict-a";
  } else if (data.verdict === "b") {
    verdict.textContent = `判定：「${b}」领先于「${a}」（${b}→${a} 最强 ${data.strength_ba} ＞ 反向 ${data.strength_ab}）`;
    verdict.className = "basis-verdict verdict-b";
  } else {
    verdict.textContent = `判定：两者并列（双向最强强度均为 ${data.strength_ab}${data.strength_ab === 0 ? "，且双向均无有效路径" : ""}）`;
    verdict.className = "basis-verdict verdict-tie";
  }

  box.innerHTML = "";
  box.appendChild(
    renderPathBlock(`${a} → ${b}（${a} 强于 ${b} 的最强路径）`, data.path_ab,
      data.verdict === "a")
  );
  box.appendChild(
    renderPathBlock(`${b} → ${a}（${b} 强于 ${a} 的最强路径）`, data.path_ba,
      data.verdict === "b")
  );
}

async function loadBasis(a, b) {
  const req = ++basisReq;
  try {
    const data = await api(
      `/api/sessions/${state.session_id}/ranking-basis?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`
    );
    if (req !== basisReq) return; // 已有更新的选择/状态，丢弃过期响应
    basisData = data;
    renderBasis();
  } catch (e) {
    if (req !== basisReq) return;
    const box = $("#basis-directions");
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "basis-error";
    err.textContent = `排名依据加载失败：${e.message}`;
    box.appendChild(err);
  }
}

/* ---------- 数据操作 ---------- */

function stateChanged() {
  // 判断新增、撤销、复核、批量改判、重置后：排名依据随状态刷新
  basisData = null;
  basisReq += 1; // 丢弃所有在途旧请求
  render();
}

async function refresh() {
  const sid = localStorage.getItem(STORAGE_KEY);
  if (!sid) {
    showSetup();
    return;
  }
  try {
    state = await api(`/api/sessions/${sid}`);
    render();
  } catch (_) {
    localStorage.removeItem(STORAGE_KEY);
    showSetup();
  }
}

async function judge(result) {
  if (busy || !state) return;
  const active = focusPair || state.current_pair;
  if (!active) return;
  busy = true;
  try {
    const { a, b } = active;
    state = await api(`/api/sessions/${state.session_id}/judgments`, {
      method: "POST",
      body: JSON.stringify({ a, b, result }),
    });
    focusPair = null; // 判定完成后回到原顺序流程
    stateChanged();
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
  }
}

async function undo() {
  if (busy || !state) return;
  busy = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/undo`, { method: "POST" });
    stateChanged();
  } catch (e) {
    toast(e.message);
  } finally {
    busy = false;
  }
}

async function revise(result) {
  if (busy || !state || !selectedPair) return;
  busy = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/judgments`, {
      method: "PUT",
      body: JSON.stringify({ a: selectedPair.a, b: selectedPair.b, result }),
    });
    selectedPair = null;
    stateChanged();
    toast("判定已更新，排名与冲突已重算");
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
  }
}

async function applySuggestion() {
  if (busy || !state || !state.suggestion) return;
  const n = state.suggestion.change_count;
  if (!confirm(`确认把这 ${n} 个组合批量改为平局吗？\n进度与撤销顺序保持不变，排名与冲突将立即重算。`)) {
    return;
  }
  busy = true;
  $("#suggestion-apply").disabled = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/resolve-conflicts`, {
      method: "POST",
      body: JSON.stringify({
        pairs: state.suggestion.pairs.map((p) => ({ a: p.a, b: p.b })),
      }),
    });
    selectedPair = null;
    stateChanged();
    toast(state.conflict ? "已应用方案，仍存在其他循环冲突" : "已批量改判，全部循环冲突已消除");
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
  }
}

async function resetRound() {  if (busy || !state) return;
  if (!confirm("确定要清空本轮全部判断记录吗？")) return;
  busy = true;
  try {
    state = await api(`/api/sessions/${state.session_id}/reset`, { method: "POST" });
    focusPair = null;
    stateChanged();
    toast("本轮已重置");
  } catch (e) {
    toast(e.message);
  } finally {
    busy = false;
  }
}

async function startSession() {
  const names = $("#candidates-input").value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  if (names.length < 3 || names.length > 12) {
    toast("请输入 3～12 个候选项（每行一个）");
    return;
  }
  if (new Set(names).size !== names.length) {
    toast("候选项名称不可重复");
    return;
  }
  try {
    state = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ candidates: names }),
    });
    localStorage.setItem(STORAGE_KEY, state.session_id);
    selectedPair = null;
    focusPair = null;
    basisSelection = [];
    basisData = null;
    render();
  } catch (e) {
    toast(e.message);
  }
}

function newRound() {
  if (!confirm("开始新一轮评审？当前进度将从本浏览器中移除（服务器记录仍保留）。")) return;
  localStorage.removeItem(STORAGE_KEY);
  state = null;
  selectedPair = null;
  focusPair = null;
  basisSelection = [];
  basisData = null;
  showSetup();
}

/* ---------- 事件绑定 ---------- */

function updateCount() {
  const n = $("#candidates-input").value.split("\n").map((s) => s.trim()).filter(Boolean).length;
  $("#setup-count").textContent = n ? `已录入 ${n} 项` : "";
}

$("#candidates-input").addEventListener("input", updateCount);
$("#start-btn").addEventListener("click", startSession);
$("#btn-a").addEventListener("click", () => judge("a"));
$("#btn-b").addEventListener("click", () => judge("b"));
$("#btn-tie").addEventListener("click", () => judge("tie"));
$("#undo-btn").addEventListener("click", undo);
$("#reset-btn").addEventListener("click", resetRound);
$("#new-btn").addEventListener("click", newRound);
$("#revise-a").addEventListener("click", () => revise("a"));
$("#revise-tie").addEventListener("click", () => revise("tie"));
$("#revise-b").addEventListener("click", () => revise("b"));
$("#revise-cancel").addEventListener("click", () => {
  selectedPair = null;
  renderConflict();
});
$("#suggestion-apply").addEventListener("click", applySuggestion);
$("#nextpair-jump").addEventListener("click", jumpToRecommended);
$("#basis-close").addEventListener("click", clearBasis);
$("#jump-back").addEventListener("click", () => {
  focusPair = null;
  render();
});

document.addEventListener("keydown", (e) => {
  if ($("#review-view").classList.contains("hidden")) return;
  if (e.ctrlKey && e.key.toLowerCase() === "z") {
    e.preventDefault();
    undo();
    return;
  }
  if (!state || (!state.current_pair && !focusPair)) return;
  if (e.key === "ArrowLeft") judge("a");
  else if (e.key === "ArrowRight") judge("b");
  else if (e.key === "ArrowDown") judge("tie");
});

/* ---------- 启动 ---------- */

refresh();
