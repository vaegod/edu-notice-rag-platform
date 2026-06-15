const state = {
  documents: [],
  documentsTotal: 0,
  documentGroups: [],
  activeDocumentInstitution: "",
  sources: [],
  sourcesTotal: 0,
  lastResult: null,
  lastQuery: "",
  lastHomepageUrl: "",
  activeTaskId: null,
  progressTimer: null,
  selectedSourceIds: new Set(),
  dashboardOverview: null,
};

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const contentType = response.headers.get("content-type") || "";
  let payload = text;
  if (text && contentType.includes("application/json")) {
    try {
      payload = JSON.parse(text);
    } catch {}
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || text || "请求失败");
  }
  return payload;
}

function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast is-visible is-${type}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.className = "toast";
  }, 3200);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function jsonText(value) {
  return JSON.stringify(value, null, 2);
}

function formatConfidence(value) {
  if (value == null || value === "") return "未提供";
  const numeric = Number(value);
  if (Number.isNaN(numeric)) return "未提供";
  const percent = numeric <= 1 ? numeric * 100 : numeric;
  return `${Math.round(percent)}%`;
}

function formatTrackLabel(value) {
  const labels = {
    undergraduate: "本科招生",
    graduate: "研究生招生",
    international: "留学生招生",
    continuing_education: "继续教育招生",
    mba: "MBA招生",
    second_bachelor: "第二学士学位",
  };
  return labels[value] || value || "未识别";
}

function taskTypeForDomain(collectionDomain) {
  if (collectionDomain === "school_profile") return "crawl_school_profile";
  if (collectionDomain === "news_center") return "crawl_news_center";
  return "crawl_admissions_notice";
}

function renderDocuments() {
  const list = document.getElementById("documents-list");
  const summary = document.getElementById("documents-summary");
  if (summary) {
    const filterText = state.activeDocumentInstitution ? `，当前高校：${escapeHtml(state.activeDocumentInstitution)}` : "";
    summary.innerHTML = `<span>共 ${state.documentsTotal || 0} 条，当前显示 ${state.documents.length} 条${filterText}</span>`;
  }
  list.innerHTML = state.documents.length ? state.documents.map((item) => `
    <article class="stack-item">
      <div class="panel-head">
        <div>
          <h4>${escapeHtml(item.title)}</h4>
          <div class="item-meta">
            <span class="pill">${escapeHtml(item.collection_domain || "未知主题")}</span>
            <span>${escapeHtml(item.source_name || item.institution_name || "未知来源")}</span>
            <span>${escapeHtml(item.publish_date || "日期未知")}</span>
          </div>
        </div>
        <div class="item-actions">
          <a class="ghost-btn" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">打开原文</a>
          <button class="ghost-btn danger-btn" data-delete-document="${item.id}">删除</button>
        </div>
      </div>
      <div class="detail-row"><strong>摘要</strong><span>${escapeHtml(item.summary || "暂无摘要")}</span></div>
    </article>
  `).join("") : '<div class="empty-state">暂无文档。</div>';
}

function renderDocumentGroups() {
  const container = document.getElementById("documents-groups");
  if (!container) return;
  const groups = state.documentGroups || [];
  if (!groups.length) {
    container.innerHTML = '<div class="empty-state">当前还没有可按大学分组的文档。</div>';
    return;
  }
  const allActive = !state.activeDocumentInstitution;
  container.innerHTML = `
    <div class="toolbar-inline">
      <button class="ghost-btn ${allActive ? "is-active" : ""}" type="button" data-documents-institution="">全部大学</button>
      ${groups.map((group) => `
        <button
          class="ghost-btn ${state.activeDocumentInstitution === group.institution_name ? "is-active" : ""}"
          type="button"
          data-documents-institution="${escapeHtml(group.institution_name)}"
        >${escapeHtml(group.institution_name)}（${group.total}）</button>
      `).join("")}
    </div>
  `;
}

function renderSavedSources() {
  const list = document.getElementById("sources-list");
  const summary = document.getElementById("sources-summary");
  const selectAllBtn = document.getElementById("sources-select-all-btn");
  const batchDeleteBtn = document.getElementById("sources-batch-delete-btn");
  syncSelectedSourceIds();
  const selectedCount = state.selectedSourceIds.size;
  if (summary) {
    summary.innerHTML = `<span>共 ${state.sourcesTotal || 0} 个，当前显示 ${state.sources.length} 个，已选 ${selectedCount} 个</span>`;
  }
  if (selectAllBtn) {
    const allSelected = state.sources.length > 0 && state.sources.every((item) => state.selectedSourceIds.has(Number(item.id)));
    selectAllBtn.textContent = allSelected ? "取消全选" : "全选";
  }
  if (batchDeleteBtn) {
    batchDeleteBtn.disabled = selectedCount === 0;
    batchDeleteBtn.textContent = selectedCount ? `删除选中（${selectedCount}）` : "删除选中";
  }
  list.innerHTML = state.sources.length ? state.sources.map((item) => {
    const sourceUrl = (item.start_urls_json && item.start_urls_json[0]) || item.base_url || "#";
    const institution = item.organization_name || (item.scope_json && item.scope_json.university_name) || "未知高校";
    const selected = state.selectedSourceIds.has(Number(item.id));
    return `
      <article class="stack-item">
        <div class="panel-head">
          <div>
            <h4>${escapeHtml(item.name || "未命名数据源")}</h4>
            <div class="item-meta">
              <span class="pill">${escapeHtml(item.collection_domain || "未知主题")}</span>
              <span>${escapeHtml(institution)}</span>
              <span>${escapeHtml(item.onboarding_status || item.status || "unknown")}</span>
            </div>
          </div>
          <div class="item-actions">
            <label class="ghost-btn" style="display:inline-flex; gap:6px; align-items:center;">
              <input type="checkbox" data-select-source-id="${item.id}" ${selected ? "checked" : ""}>
              <span>选中</span>
            </label>
            <a class="ghost-btn" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">打开数据源</a>
            <button class="primary-btn" data-crawl-source-id="${item.id}" data-crawl-domain="${escapeHtml(item.collection_domain || "")}">采集这个数据源</button>
            <button class="ghost-btn danger-btn" data-delete-source-id="${item.id}" data-source-name="${escapeHtml(item.name || "未命名数据源")}">删除</button>
          </div>
        </div>
        <div class="detail-grid">
          <div class="detail-row"><strong>官网</strong><span><a href="${escapeHtml(item.base_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.base_url || "未提供")}</a></span></div>
          <div class="detail-row"><strong>起始页</strong><span><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(sourceUrl)}</a></span></div>
          <div class="detail-row"><strong>来源</strong><span>${escapeHtml(item.source_origin || "unknown")}</span></div>
          <div class="detail-row"><strong>置信度</strong><span>${escapeHtml(formatConfidence(item.confidence_score))}</span></div>
        </div>
      </article>
    `;
  }).join("") : '<div class="empty-state">暂无已保存数据源。</div>';
}

function syncSelectedSourceIds() {
  const validIds = new Set((state.sources || []).map((item) => Number(item.id)));
  state.selectedSourceIds = new Set(
    [...state.selectedSourceIds].filter((id) => validIds.has(Number(id)))
  );
}

function renderSourceCards(items) {
  if (!items.length) {
    return '<div class="empty-state">当前没有可展示的数据源。</div>';
  }
  return `
    <div class="stack-list">
      ${items.map((item, index) => `
        <article class="stack-item">
          <div class="panel-head">
            <div>
              <h4>${index + 1}. ${escapeHtml(item.university_name || "")} ${escapeHtml(item.source_title || "")}</h4>
              <div class="item-meta">
                <span class="pill">${escapeHtml(item.collection_domain || "未知主题")}</span>
                <span>${escapeHtml(item.validation_status || "unknown")}</span>
                <span>${escapeHtml(formatTrackLabel(item.track))}</span>
                <span>${escapeHtml(item.candidate_type || item.source_kind || "unknown")}</span>
                <span>${escapeHtml(formatConfidence(item.confidence_score))}</span>
              </div>
            </div>
            <div class="item-actions">
              ${item.source_id && (item.validation_status === "valid" || item.saved)
                ? `<button class="primary-btn" data-crawl-source-id="${item.source_id}" data-crawl-domain="${escapeHtml(item.collection_domain || "")}">采集这个数据源</button>
                   <button class="ghost-btn danger-btn" data-delete-source-id="${item.source_id}" data-source-name="${escapeHtml(item.source_title || item.university_name || "数据源")}">删除</button>`
                : `<button
                     class="ghost-btn"
                     type="button"
                     data-validate-source-url="${escapeHtml(item.source_url || "")}"
                     data-validate-homepage-url="${escapeHtml(item.homepage_url || "")}"
                   >先校验后采集</button>`}
            </div>
          </div>
          <div class="detail-grid">
            <div class="detail-row"><strong>高校</strong><span>${escapeHtml(item.university_name || "未知")}</span></div>
            <div class="detail-row"><strong>官网</strong><span><a href="${escapeHtml(item.homepage_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.homepage_url || "未提供")}</a></span></div>
            <div class="detail-row"><strong>数据源</strong><span><a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.source_url || "未提供")}</a></span></div>
            <div class="detail-row"><strong>轨道</strong><span>${escapeHtml(formatTrackLabel(item.track))}</span></div>
            <div class="detail-row"><strong>候选类型</strong><span>${escapeHtml(item.candidate_type || item.source_kind || "unknown")}</span></div>
            <div class="detail-row"><strong>适配分</strong><span>${escapeHtml(formatConfidence(item.source_suitability_score))}</span></div>
            <div class="detail-row"><strong>拒绝原因</strong><span>${escapeHtml(item.reject_reason_code || "无")}</span></div>
            <div class="detail-row"><strong>说明</strong><span>${escapeHtml(item.reason || item.validation_message || "")}</span></div>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

function renderWorkflowSteps(steps = []) {
  if (!steps.length) return "";
  return `
    <div class="detail-row">
      <strong>工作流</strong>
      <div class="note-list">
        ${steps.map((step) => `
          <span class="pill">${escapeHtml(step.name)} · ${escapeHtml(step.status)}${step.latency_ms != null ? ` · ${step.latency_ms}ms` : ""}</span>
        `).join("")}
      </div>
    </div>
  `;
}

function renderConfidenceNotes(notes = []) {
  if (!notes.length) return "";
  const labels = {
    answer_grounded_in_retrieved_documents: "答案基于召回文档",
    lightweight_keyword_retrieval: "轻量关键词召回",
    normalized_topic_filter: "已规范化主题词",
    relaxed_topic_filter: "已放宽主题词",
    relaxed_source_filter: "已放宽数据源限制",
    ask_intent_coerced_to_query: "问答入口已按查询处理",
    no_retrieved_documents: "未召回文档",
    invalid_query: "查询校验未通过",
    ask_endpoint_rejects_crawl_intents: "问答接口不发起采集",
    ask_endpoint_rejects_source_discovery: "问答接口不执行找源",
    source_selected_from_knowledge_base: "复用健康数据源",
  };
  return `
    <div class="detail-row">
      <strong>检索说明</strong>
      <div class="note-list">
        ${notes.map((note) => `<span class="pill">${escapeHtml(labels[note] || note)}</span>`).join("")}
      </div>
    </div>
  `;
}

function formatDateTime(value) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
}

function renderSystemStatus() {
  const container = document.getElementById("system-status");
  if (!container) return;
  const runtime = state.dashboardOverview && state.dashboardOverview.runtime;
  if (!runtime) {
    container.innerHTML = '<span class="pill">状态信息未加载</span>';
    return;
  }
  container.innerHTML = `
    <span class="pill">后端版本：${escapeHtml(runtime.app_version || "未知")}</span>
    <span class="pill">进程启动：${escapeHtml(formatDateTime(runtime.process_started_at))}</span>
    <span class="pill">代码构建：${escapeHtml(formatDateTime(runtime.backend_build_at))}</span>
    <span class="pill">知识库生成：${escapeHtml(formatDateTime(runtime.knowledge_generated_at))}</span>
    <span class="pill">知识库状态：healthy ${escapeHtml(runtime.healthy_knowledge_sources)} / stale ${escapeHtml(runtime.stale_knowledge_sources)} / invalid ${escapeHtml(runtime.invalid_knowledge_sources)}</span>
  `;
}

function renderCitations(citations = []) {
  if (!citations.length) return "";
  return `
    <div class="detail-row">
      <strong>证据引用</strong>
      <div class="stack-list">
        ${citations.map((item) => `
          <article class="stack-item">
            <h4>[${escapeHtml(item.index)}] ${escapeHtml(item.title)}</h4>
            <div class="item-meta">
              <span>${escapeHtml(item.publish_date || "日期未知")}</span>
              <span class="pill">${escapeHtml(item.collection_domain || "unknown")}</span>
            </div>
            <p>${escapeHtml(item.snippet || "")}</p>
            <a class="ghost-btn" href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">打开证据</a>
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSourceKnowledge(debug = {}) {
  const hits = debug.source_knowledge_hits || [];
  if (!hits.length) return "";
  return `
    <div class="detail-row">
      <strong>健康 Source 复用</strong>
      <div class="stack-list">
        ${hits.map((hit) => `
          <article class="stack-item knowledge-hit">
            <div class="panel-head">
              <div>
                <h4>${escapeHtml(hit.university_name || "未知高校")} ${escapeHtml(hit.source_title || "研招源")}</h4>
                <div class="item-meta">
                  <span class="pill">${escapeHtml(hit.source_origin || "source_knowledge")}</span>
                  <span>${escapeHtml(hit.health_status || "healthy")}</span>
                  <span>${escapeHtml(formatConfidence(hit.confidence_score))}</span>
                  <span>${escapeHtml(Math.round(Number(hit.selection_score || 0)))}分</span>
                </div>
              </div>
              <a class="ghost-btn" href="${escapeHtml(hit.source_url || "#")}" target="_blank" rel="noreferrer">打开数据源</a>
            </div>
            <div class="detail-grid">
              <div class="detail-row"><strong>入口</strong><span><a href="${escapeHtml(hit.entrypoint_url || hit.homepage_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(hit.entrypoint_url || hit.homepage_url || "未提供")}</a></span></div>
              <div class="detail-row"><strong>URL</strong><span><a href="${escapeHtml(hit.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(hit.source_url || "未提供")}</a></span></div>
              <div class="detail-row"><strong>复用理由</strong><span>${escapeHtml(hit.source_reuse_reason || hit.reason || "")}</span></div>
              <div class="detail-row"><strong>证据片段</strong><span>${escapeHtml(hit.evidence_snippet || "")}</span></div>
            </div>
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSourceSections(items) {
  const savedItems = items.filter((item) => item.saved);
  const reviewItems = items.filter((item) => item.validation_status === "valid" && !item.saved);
  const invalidItems = items.filter((item) => item.validation_status !== "valid" && !item.saved);
  return `
    ${savedItems.length ? `
      <div class="detail-row">
        <strong>已自动保存的数据源</strong>
        <div>${renderSourceCards(savedItems)}</div>
      </div>
    ` : `
      <div class="detail-row">
        <strong>已自动保存的数据源</strong>
        <span>当前没有高置信自动保存的数据源。</span>
      </div>
    `}
    ${reviewItems.length ? `
      <div class="detail-row">
        <strong>通过校验但建议人工确认</strong>
        <div class="note-list">
          <span>这些候选看起来可信，但系统暂未自动落库。</span>
        </div>
        <div>${renderSourceCards(reviewItems)}</div>
      </div>
    ` : ""}
    ${invalidItems.length ? `
      <div class="detail-row">
        <strong>未通过校验的模型候选</strong>
        <div class="note-list">
          <span>下面这些只是模型返回的候选，不建议直接采集。</span>
        </div>
        <div>${renderSourceCards(invalidItems)}</div>
      </div>
    ` : ""}
  `;
}

function renderSourceGroup(title, items, description = "") {
  if (!items || !items.length) return "";
  return `
    <div class="detail-row">
      <strong>${escapeHtml(title)}</strong>
      ${description ? `<div class="note-list"><span>${escapeHtml(description)}</span></div>` : ""}
      <div>${renderSourceSections(items)}</div>
    </div>
  `;
}

function renderAgentDiagnostics(result) {
  const blocks = [];
  if (result.failure_reason) {
    blocks.push(`<div class="detail-row"><strong>失败原因</strong><span>${escapeHtml(result.failure_reason)}</span></div>`);
  }
  if (result.used_browser_explorer) {
    blocks.push(`<div class="detail-row"><strong>浏览器兜底</strong><span>已启用</span></div>`);
  }
  if (result.candidate_rankings && result.candidate_rankings.length) {
    blocks.push(`<div class="detail-row"><strong>候选排序</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.candidate_rankings))}</pre></details></div>`);
  }
  if (result.agent_trace && result.agent_trace.length) {
    blocks.push(`<div class="detail-row"><strong>智能体轨迹</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.agent_trace))}</pre></details></div>`);
  }
  return blocks.join("");
}

function renderResult(result) {
  const container = document.getElementById("nl-result");
  state.lastResult = result;

  if (result.resolved_sources && result.resolved_sources.length) {
    container.className = "detail-panel";
    container.innerHTML = `
      <div class="detail-grid">
        <div class="detail-row"><strong>结论</strong><span>${escapeHtml(result.answer || "")}</span></div>
        <div class="detail-row"><strong>数量</strong><span>${result.resolved_sources.length}</span></div>
        ${renderWorkflowSteps(result.workflow_steps || [])}
        ${renderConfidenceNotes(result.confidence_notes || [])}
        ${renderSourceKnowledge(result.debug || {})}
        ${renderAgentDiagnostics(result)}
        ${result.debug && Object.keys(result.debug).length ? `
          <div class="detail-row"><strong>选源策略</strong><span>${escapeHtml(result.debug.source_resolution_strategy || "未知")}</span></div>
          <div class="detail-row"><strong>启动方式</strong><span>${escapeHtml(result.debug.bootstrap_strategy || "未知")}</span></div>
          <div class="detail-row"><strong>站内入口</strong><span>${escapeHtml(result.debug.entrypoint_url || result.homepage_url || "未提供")}</span></div>
          <div class="detail-row"><strong>复用说明</strong><span>${escapeHtml(result.debug.source_reuse_reason || "本次未复用已保存 source")}</span></div>
          <div class="detail-row"><strong>Source 健康状态</strong><span>${escapeHtml(result.debug.health_status || "未知")}</span></div>
          <div class="detail-row"><strong>是否触发重发现</strong><span>${result.debug.rediscovery_triggered ? "是" : "否"}</span></div>
          <div class="detail-row"><strong>候选发现来源</strong><span>${escapeHtml(result.debug.candidate_provider || "未知")}</span></div>
          <div class="detail-row"><strong>首页候选数</strong><span>${escapeHtml(result.debug.raw_candidate_count ?? 0)}</span></div>
          <div class="detail-row"><strong>轨道分布</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.debug.candidate_track_counts || {}))}</pre></details></div>
          <div class="detail-row"><strong>类型分布</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.debug.candidate_type_counts || {}))}</pre></details></div>
          <div class="detail-row"><strong>拒绝统计</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.debug.reject_reason_counts || {}))}</pre></details></div>
          <div class="detail-row"><strong>首页候选链接</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.debug.raw_candidates || []))}</pre></details></div>
          <div class="detail-row"><strong>送给 DeepSeek 的候选</strong><details><summary>展开查看</summary><pre>${escapeHtml(jsonText(result.debug.llm_selected_candidates || []))}</pre></details></div>
        ` : ""}
        ${renderSourceGroup("候选结果", result.resolved_sources)}
        <div class="detail-row"><strong>原始返回</strong><details><summary>展开 JSON</summary><pre>${escapeHtml(jsonText(result))}</pre></details></div>
      </div>
    `;
    return;
  }

  if (result.documents && result.documents.length) {
    container.className = "detail-panel";
    container.innerHTML = `
      <div class="detail-grid">
        <div class="detail-row"><strong>结论</strong><span>${escapeHtml(result.answer || "")}</span></div>
        ${renderWorkflowSteps(result.workflow_steps || [])}
        ${renderConfidenceNotes(result.confidence_notes || [])}
        ${renderSourceKnowledge(result.debug || {})}
        ${renderCitations(result.citations || [])}
        <div class="detail-row"><strong>文档结果</strong><pre>${escapeHtml(jsonText(result.documents))}</pre></div>
      </div>
    `;
    return;
  }

  container.className = "detail-panel";
  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-row"><strong>结果</strong><span>${escapeHtml(result.answer || result.validation_message || "已完成")}</span></div>
      ${renderWorkflowSteps(result.workflow_steps || [])}
      ${renderConfidenceNotes(result.confidence_notes || [])}
      ${renderSourceKnowledge(result.debug || {})}
      ${renderAgentDiagnostics(result)}
      ${renderCitations(result.citations || [])}
      <div class="detail-row"><strong>原始返回</strong><pre>${escapeHtml(jsonText(result))}</pre></div>
    </div>
  `;
}

function renderTaskProgress(task) {
  const container = document.getElementById("nl-result");
  const percent = task.progress_percent ?? 0;
  const stage = task.progress_stage || task.status || "pending";
  const message = task.progress_message || "任务执行中...";
  container.className = "detail-panel";
  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-row"><strong>任务 ID</strong><span>#${task.id}</span></div>
      <div class="detail-row"><strong>状态</strong><span>${escapeHtml(task.status)}</span></div>
      <div class="detail-row"><strong>阶段</strong><span>${escapeHtml(stage)}</span></div>
      <div class="detail-row"><strong>进度</strong><span>${percent}%</span></div>
      <div class="detail-row"><strong>说明</strong><span>${escapeHtml(message)}</span></div>
      <div class="detail-row"><strong>任务详情</strong><pre>${escapeHtml(jsonText(task))}</pre></div>
    </div>
  `;
}

function renderActionError(message, context = "操作失败") {
  const container = document.getElementById("nl-result");
  if (!container) return;
  container.className = "detail-panel";
  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-row"><strong>状态</strong><span>${escapeHtml(context)}</span></div>
      <div class="detail-row"><strong>原因</strong><span>${escapeHtml(message || "请求未成功完成")}</span></div>
      <div class="detail-row"><strong>建议</strong><span>可以重试一次，或先检查数据源发现结果是否已经返回。</span></div>
    </div>
  `;
}

async function pollTaskProgress(taskId) {
  state.activeTaskId = taskId;
  if (state.progressTimer) {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
  const load = async () => {
    const task = await apiFetch(`/api/v1/tasks/${taskId}`);
    renderTaskProgress(task);
    if (["success", "partial_success", "failed"].includes(task.status)) {
      clearInterval(state.progressTimer);
      state.progressTimer = null;
      state.activeTaskId = null;
      await loadSources();
      await loadDocuments();
      showToast(task.status === "failed" ? "任务执行失败" : "任务执行完成", task.status === "failed" ? "error" : "success");
    }
  };
  await load();
  state.progressTimer = setInterval(() => {
    load().catch((error) => {
      clearInterval(state.progressTimer);
      state.progressTimer = null;
      showToast(error.message, "error");
    });
  }, 1500);
}

async function loadAllPages(basePath) {
  const pageSize = 100;
  const first = await apiFetch(`${basePath}${basePath.includes("?") ? "&" : "?"}page=1&page_size=${pageSize}`);
  const items = [...(first.items || [])];
  const total = first.total || items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  for (let page = 2; page <= totalPages; page += 1) {
    const payload = await apiFetch(`${basePath}${basePath.includes("?") ? "&" : "?"}page=${page}&page_size=${pageSize}`);
    items.push(...(payload.items || []));
  }
  return { items, total };
}

async function loadSources() {
  const payload = await apiFetch("/api/v1/sources");
  state.sources = payload || [];
  state.sourcesTotal = state.sources.length;
  syncSelectedSourceIds();
  renderSavedSources();
}

function handleSourceSelectionChange(event) {
  const sourceId = event.target.dataset.selectSourceId;
  if (!sourceId) return;
  const numericId = Number(sourceId);
  if (event.target.checked) {
    state.selectedSourceIds.add(numericId);
  } else {
    state.selectedSourceIds.delete(numericId);
  }
  renderSavedSources();
}

async function toggleSelectAllSources() {
  const sourceIds = (state.sources || []).map((item) => Number(item.id));
  if (!sourceIds.length) {
    throw new Error("当前没有可选择的数据源");
  }
  const allSelected = sourceIds.every((id) => state.selectedSourceIds.has(id));
  if (allSelected) {
    state.selectedSourceIds.clear();
  } else {
    sourceIds.forEach((id) => state.selectedSourceIds.add(id));
  }
  renderSavedSources();
}

async function deleteSelectedSources() {
  const sourceIds = [...state.selectedSourceIds];
  if (!sourceIds.length) {
    throw new Error("请先选择要删除的数据源");
  }
  const sourceNames = (state.sources || [])
    .filter((item) => state.selectedSourceIds.has(Number(item.id)))
    .map((item) => item.name || `#${item.id}`);
  if (!window.confirm(`确认删除选中的 ${sourceIds.length} 个数据源吗？\n${sourceNames.join("\n")}`)) {
    return;
  }
  for (const sourceId of sourceIds) {
    await apiFetch(`/api/v1/sources/${sourceId}`, {
      method: "DELETE",
      headers: {},
    });
    syncDeletedSourceFromLastResult(sourceId);
  }
  state.selectedSourceIds.clear();
  await loadSources();
  showToast(`已删除 ${sourceIds.length} 个数据源`);
}

async function loadDocuments() {
  const basePath = state.activeDocumentInstitution
    ? `/api/v1/documents?institution_name=${encodeURIComponent(state.activeDocumentInstitution)}`
    : "/api/v1/documents";
  const payload = await loadAllPages(basePath);
  state.documentsTotal = payload.total || 0;
  state.documents = payload.items || [];
  renderDocuments();
}

async function loadDocumentGroups() {
  const payload = await apiFetch("/api/v1/documents/by-university");
  state.documentGroups = payload || [];
  renderDocumentGroups();
}

async function loadDashboardOverview() {
  const payload = await apiFetch("/api/v1/dashboard/overview");
  state.dashboardOverview = payload;
  renderSystemStatus();
}

async function classifyDocumentsByUniversity() {
  renderResult({
    answer: "正在调用 DeepSeek 对文档按大学分类，请稍候...",
  });
  const result = await apiFetch("/api/v1/documents/postprocess/university-classify", {
    method: "POST",
    body: JSON.stringify({
      only_missing: true,
      limit: 100,
      dry_run: false,
    }),
  });
  renderResult({
    answer: `大学分类完成：匹配 ${result.matched} 条，更新 ${result.updated} 条，未变化 ${result.unchanged} 条，失败 ${result.failed} 条。`,
    documents: result.items || [],
  });
  await loadDocumentGroups();
  await loadDocuments();
  showToast("文档大学分类完成");
}

async function handleNlAction(action) {
  const query = document.querySelector("#nl-form textarea[name='query']").value.trim();
  const homepageUrl = document.querySelector("#nl-form input[name='homepage_url']").value.trim();
  if (!query) throw new Error("请先输入自然语言请求");
  state.lastQuery = query;
  state.lastHomepageUrl = homepageUrl || "";
  const container = document.getElementById("nl-result");
  container.className = "detail-panel";
  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-row"><strong>当前状态</strong><span>${action === "execute" ? "系统正在解析官方入口、复用健康 Source 或触发站内找源..." : action === "ask" ? "正在查询已采集数据..." : "正在解析自然语言..."}</span></div>
      <div class="detail-row"><strong>输入</strong><span>${escapeHtml(query)}</span></div>
      <div class="detail-row"><strong>站内入口</strong><span>${escapeHtml(homepageUrl || "未提供，将回退到官方入口目录")}</span></div>
      <div class="detail-row"><strong>请稍候</strong><span>请求已发出，正在等待返回。</span></div>
    </div>
  `;
  const urlMap = {
    parse: "/api/v1/nl/parse",
    execute: "/api/v1/nl/execute",
    ask: "/api/v1/ask",
  };
  let result;
  try {
    result = await apiFetch(urlMap[action], {
      method: "POST",
      body: JSON.stringify({ query, homepage_url: homepageUrl || null }),
    });
  } catch (error) {
    renderActionError(error.message, "请求失败");
    throw error;
  }
  renderResult(result);
  if (action === "execute" && result.task_ids && result.task_ids.length) {
    await pollTaskProgress(result.task_ids[0]);
    return;
  }
  await loadSources();
  await loadDocuments();
  showToast(action === "execute" ? "入口解析与找源处理完成" : "操作完成");
}

async function handleResultClick(event) {
  const validateSourceUrl = event.target.dataset.validateSourceUrl;
  if (validateSourceUrl) {
    const homepageUrl = event.target.dataset.validateHomepageUrl || "";
    const candidate = findResolvedSourceCandidate(validateSourceUrl, homepageUrl);
    if (!candidate) {
      throw new Error("没有找到待校验的候选数据源");
    }
    const response = await apiFetch("/api/v1/sources/validate-candidate", {
      method: "POST",
      body: JSON.stringify({
        university_name: candidate.university_name,
        collection_domain: candidate.collection_domain || "admissions_notice",
        homepage_url: candidate.homepage_url,
        source_url: candidate.source_url,
        source_title: candidate.source_title,
        source_kind: candidate.source_kind || "list_page",
        admissions_levels: candidate.admissions_levels || [],
        confidence_score: candidate.confidence_score || 0,
        reason: candidate.reason || "",
      }),
    });
    updateResolvedSourceCandidate(validateSourceUrl, homepageUrl, {
      source_id: response.source_id,
      saved: response.saved,
      validation_status: response.validation_status,
      validation_message: response.validation_message,
      confidence_score: response.confidence_score || candidate.confidence_score || 0,
      reason: response.validation_message || candidate.reason,
    });
    await loadSources();
    showToast(response.saved ? "校验通过，已保存数据源" : (response.validation_message || "校验未通过"), response.saved ? "success" : "error");
    return;
  }
  const deleteSourceId = event.target.dataset.deleteSourceId;
  if (deleteSourceId) {
    const sourceName = event.target.dataset.sourceName || `#${deleteSourceId}`;
    if (!window.confirm(`确认删除数据源“${sourceName}”吗？`)) return;
    await apiFetch(`/api/v1/sources/${deleteSourceId}`, {
      method: "DELETE",
      headers: {},
    });
    syncDeletedSourceFromLastResult(Number(deleteSourceId));
    await loadSources();
    showToast(`已删除数据源 #${deleteSourceId}`);
    return;
  }
  const sourceId = event.target.dataset.crawlSourceId;
  if (!sourceId) return;
  const collectionDomain = event.target.dataset.crawlDomain || "admissions_notice";
  renderResult({
    answer: `正在为数据源 #${sourceId} 创建采集任务，请稍候...`,
  });
  const payload = {
    source_id: Number(sourceId),
    task_type: taskTypeForDomain(collectionDomain),
    trigger_mode: "manual",
    execute_immediately: true,
    task_payload: { page_limit: 1 },
  };
  const result = await apiFetch("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showToast(`已开始采集数据源 #${sourceId}`);
  await pollTaskProgress(result.id);
}

function findResolvedSourceCandidate(sourceUrl, homepageUrl = "") {
  if (!state.lastResult || !Array.isArray(state.lastResult.resolved_sources)) return null;
  return state.lastResult.resolved_sources.find((item) =>
    String(item.source_url || "") === String(sourceUrl || "")
    && String(item.homepage_url || "") === String(homepageUrl || "")
  ) || null;
}

function updateResolvedSourceCandidate(sourceUrl, homepageUrl, updates) {
  if (!state.lastResult || !Array.isArray(state.lastResult.resolved_sources)) return;
  let changed = false;
  state.lastResult.resolved_sources = state.lastResult.resolved_sources.map((item) => {
    if (
      String(item.source_url || "") !== String(sourceUrl || "")
      || String(item.homepage_url || "") !== String(homepageUrl || "")
    ) {
      return item;
    }
    changed = true;
    return { ...item, ...updates };
  });
  if (changed) {
    renderResult(state.lastResult);
  }
}

function syncDeletedSourceFromLastResult(sourceId) {
  if (!state.lastResult || !Array.isArray(state.lastResult.resolved_sources)) return;
  let changed = false;
  state.lastResult.resolved_sources = state.lastResult.resolved_sources.map((item) => {
    if (Number(item.source_id) !== Number(sourceId)) return item;
    changed = true;
    return {
      ...item,
      source_id: null,
      saved: false,
      validation_status: item.validation_status === "valid" ? "unchecked" : item.validation_status,
      validation_message: item.validation_message || "数据源已删除，可重新发现或重新保存。",
    };
  });
  if (changed) {
    renderResult(state.lastResult);
  }
}

async function handleDocumentListClick(event) {
  const institutionName = event.target.dataset.documentsInstitution;
  if (institutionName !== undefined) {
    state.activeDocumentInstitution = institutionName;
    await loadDocuments();
    renderDocumentGroups();
    return;
  }
  const documentId = event.target.dataset.deleteDocument;
  if (!documentId) return;
  await apiFetch(`/api/v1/documents/${documentId}`, {
    method: "DELETE",
    headers: {},
  });
  showToast(`已删除文档 #${documentId}`);
  await loadDocuments();
}

async function runUiAction(action) {
  try {
    await action();
  } catch (error) {
    showToast(error.message || "操作失败", "error");
  }
}

function bindEvents() {
  document.getElementById("global-refresh-btn").addEventListener("click", () => runUiAction(async () => {
    await loadDashboardOverview();
    await loadSources();
    await loadDocuments();
    showToast("已刷新");
  }));
  document.querySelectorAll("[data-nl-action]").forEach((button) => {
    button.addEventListener("click", () => runUiAction(() => handleNlAction(button.dataset.nlAction)));
  });
  document.getElementById("nl-result").addEventListener("click", (event) => runUiAction(() => handleResultClick(event)));
  document.getElementById("sources-refresh-btn").addEventListener("click", () => runUiAction(loadSources));
  document.getElementById("sources-select-all-btn").addEventListener("click", () => runUiAction(toggleSelectAllSources));
  document.getElementById("sources-batch-delete-btn").addEventListener("click", () => runUiAction(deleteSelectedSources));
  document.getElementById("documents-classify-btn").addEventListener("click", () => runUiAction(classifyDocumentsByUniversity));
  document.getElementById("documents-refresh-btn").addEventListener("click", () => runUiAction(loadDocuments));
  document.getElementById("documents-list").addEventListener("click", (event) => runUiAction(() => handleDocumentListClick(event)));
  document.getElementById("documents-groups").addEventListener("click", (event) => runUiAction(() => handleDocumentListClick(event)));
  document.getElementById("sources-list").addEventListener("click", (event) => runUiAction(() => handleResultClick(event)));
  document.getElementById("sources-list").addEventListener("change", (event) => runUiAction(() => handleSourceSelectionChange(event)));
}

async function init() {
  bindEvents();
  await loadDashboardOverview();
  await loadSources();
  await loadDocumentGroups();
  await loadDocuments();
}

init().catch((error) => showToast(error.message, "error"));
