const state = { projectId: localStorage.getItem("memoryBankProjectId"), data: null };

const $ = (selector) => document.querySelector(selector);
const esc = (value = "") => String(value).replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `请求失败（${response.status}）`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.className = "toast", 2600);
}

function activeInterrupt() {
  return state.data?.workflow?.interrupts?.[0]?.value || null;
}

function stageRank(stage) {
  const map = { interview: 0, evidence: 1, writing: 2, review: 3, creative: 4, delivered: 4 };
  return map[stage] ?? 0;
}

function render() {
  if (!state.data) return;
  $("#setupCard").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  const project = state.data.project;
  const rank = stageRank(project.stage);
  document.querySelectorAll(".stage-track [data-stage]").forEach((node, index) => {
    node.classList.toggle("done", index < rank || project.stage === "delivered");
    node.classList.toggle("active", index === rank && project.stage !== "delivered");
  });
  renderStage(project);
  renderEvidence();
  renderTrace();
}

function renderStage(project) {
  const host = $("#stageContent");
  const pending = activeInterrupt();
  if (project.stage === "revoked") {
    host.innerHTML = `<div class="empty-state"><span class="stage-label">授权已撤回</span><h2 class="stage-title">内容已经删除</h2><p>采访、Claim、草稿和交付内容均已失效，审计事件被保留。</p><button class="primary" onclick="startAnother()">创建新记忆</button></div>`;
    return;
  }
  if (project.stage === "rejected") {
    host.innerHTML = `<div class="empty-state"><span class="stage-label">流程已结束</span><h2 class="stage-title">本次草稿未获批准</h2><button class="primary" onclick="startAnother()">创建新记忆</button></div>`;
    return;
  }
  if (project.stage === "delivered") {
    const content = state.data.delivery?.content || "";
    host.innerHTML = `
      <span class="stage-label">✓ 已完成</span>
      <h2 class="stage-title">一段有来处的记忆</h2>
      <p class="stage-subtitle">内容已经通过证据审计和人工批准。</p>
      <div class="complete-card"><pre>${esc(content)}</pre></div>
      <div class="action-row"><button class="ghost" onclick="copyDelivery()">复制成品</button><button class="primary" onclick="startAnother()">再创建一段</button></div>`;
    return;
  }
  if (pending?.kind === "review") {
    const draft = pending.draft?.content || state.data.draft?.content || "";
    host.innerHTML = `
      <span class="stage-label">人工确认点</span>
      <h2 class="stage-title">确认这段文字是否准确</h2>
      <p class="stage-subtitle">你可以修改措辞，但请保留段尾证据标记；新增事实需要继续采访。</p>
      ${renderFindings()}
      <textarea id="draftText" class="draft-editor">${esc(draft)}</textarea>
      <div class="action-row">
        <button class="secondary" onclick="submitReview('approve')">批准并生成记忆卡</button>
        <button class="ghost" onclick="submitReview('edit')">保存修改并重审</button>
        <button class="ghost" onclick="submitReview('request_more')">继续采访补充</button>
        <button class="danger" onclick="submitReview('reject')">拒绝</button>
      </div>`;
    return;
  }
  if (pending?.kind === "interview") {
    host.innerHTML = `
      <span class="stage-label">INTERVIEW DIRECTOR</span>
      <h2 class="stage-title">正在采访 ${esc(project.subject_name)}</h2>
      <p class="stage-subtitle">一次只问一个问题。回答会成为可追溯的会话事件，而不是直接写入“既定事实”。</p>
      <div class="question-box"><small>${esc(pending.title || "采访")}</small><p>${esc(pending.question)}</p></div>
      <label>你的回答<textarea id="answerText" placeholder="可以像聊天一样自然地讲，也可以只记录一个具体片段。"></textarea></label>
      <div class="action-row">
        <button class="primary" onclick="submitAnswer(false)">提交并继续 <span>→</span></button>
        <button class="secondary" onclick="submitAnswer(true)">提交并生成章节</button>
        <button class="danger" onclick="revokeProject()">撤回授权并删除</button>
      </div>
      <p class="microcopy">提交具有幂等 decision_id；重复点击不会重复写入同一轮。</p>`;
    return;
  }
  host.innerHTML = `<div class="empty-state"><span class="stage-label">${esc(project.stage)}</span><h2 class="stage-title">工作流正在处理</h2><p>刷新即可获取最新状态。</p></div>`;
}

function renderFindings() {
  const findings = state.data?.workflow?.state?.audit_findings || [];
  if (!findings.length) return `<div class="inline-note">证据审计已通过：当前正文中的叙述段均绑定有效 Claim。</div>`;
  return findings.map(item => `<div class="finding">${esc(item.message)} ${item.excerpt ? `“${esc(item.excerpt)}”` : ""}</div>`).join("");
}

function renderEvidence() {
  const host = $("#evidencePanel");
  const claims = state.data.claims || [];
  const turns = state.data.turns || [];
  host.innerHTML = `<div class="evidence-title">EVIDENCE LEDGER · ${claims.length}</div>` + (
    claims.length ? claims.map(claim => `
      <article class="evidence-item">
        <span class="claim-status">口述来源</span>
        <p>${esc(claim.text)}</p>
        <small>第 ${claim.round_index} 轮 · ${esc(claim.id.slice(-8))}</small>
      </article>`).join("") : `<p class="microcopy">完成采访后，这里会出现可追溯的 Claim。</p>`
  );
  if (turns.length) host.innerHTML += `<div class="evidence-title">INTERVIEW TURNS · ${turns.length}</div>` + turns.map(turn => `
    <article class="evidence-item"><p>${esc(turn.answer)}</p><small>第 ${turn.round_index} 轮</small></article>`).join("");
}

function renderTrace() {
  const host = $("#tracePanel");
  const events = state.data.events || [];
  host.innerHTML = `<div class="evidence-title">AUDIT TRACE</div>` + events.slice(0, 12).map(event => `
    <div class="trace-item"><i></i><div><b>${esc(event.event_type)}</b><small>${esc(event.created_at.replace("T", " "))}</small></div></div>`).join("");
}

async function createProject(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  payload.max_rounds = Number(payload.max_rounds);
  try {
    state.data = await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
    state.projectId = state.data.project.id;
    localStorage.setItem("memoryBankProjectId", state.projectId);
    render();
    toast("采访已经开始");
  } catch (error) { toast(error.message, true); }
}

async function submitAnswer(finish) {
  const answer = $("#answerText")?.value.trim();
  if (!answer) return toast("请先写下这段回答", true);
  await mutate(`/api/projects/${state.projectId}/respond`, { answer, finish });
}

async function submitReview(action) {
  const edited_text = action === "edit" ? $("#draftText")?.value : null;
  await mutate(`/api/projects/${state.projectId}/review`, { action, edited_text });
}

async function mutate(path, payload) {
  try {
    state.data = await api(path, { method: "POST", body: JSON.stringify(payload) });
    render();
    toast("状态已安全保存");
  } catch (error) { toast(error.message, true); }
}

async function revokeProject() {
  if (!window.confirm("确认撤回授权并删除采访、证据、草稿和交付内容吗？审计记录会保留。")) return;
  await mutate(`/api/projects/${state.projectId}/revoke`, { reason: "用户在演示界面主动撤回" });
}

async function refresh() {
  if (!state.projectId) return;
  try { state.data = await api(`/api/projects/${state.projectId}`); render(); }
  catch (error) { localStorage.removeItem("memoryBankProjectId"); state.projectId = null; }
}

function startAnother() {
  localStorage.removeItem("memoryBankProjectId");
  state.projectId = null; state.data = null;
  $("#workspace").classList.add("hidden");
  $("#setupCard").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function copyDelivery() {
  await navigator.clipboard.writeText(state.data?.delivery?.content || "");
  toast("成品已复制");
}

async function boot() {
  try {
    const health = await api("/api/health");
    $("#healthDot").classList.add("online");
    $("#healthText").textContent = `${health.workflow} · ${health.mode}`;
  } catch (_) { $("#healthText").textContent = "服务未连接"; }
  if (state.projectId) await refresh();
}

$("#projectForm").addEventListener("submit", createProject);
$("#refreshButton").addEventListener("click", refresh);
window.submitAnswer = submitAnswer;
window.submitReview = submitReview;
window.revokeProject = revokeProject;
window.startAnother = startAnother;
window.copyDelivery = copyDelivery;
boot();

