/* Free expression stays in memory until an explicit download or account save.

   两个模式可以先让 AI 代劳：
   - 帮我理清：读正文，替用户拆出事实与需要，用户只需确认要不要填进整理框；
   - 想想下一步：先给本地选项（转轮盘 / 短练习），想写点什么的再让 AI 给建议。
   每次调用前都会确认一次：这段字会发给模型服务，服务端不保存、不进记录。 */
const EXPRESSION_MODES = ["space", "clarify", "next"];
const EXPRESSION_FIELDS = { text: 500, facts: 160, need: 160, step: 160, support: 160 };
const INTENT_LIMIT = 200;

export function expressionNote(draft, t) {
  const lines = [t("express.saved_mode") + t("express.mode_" + draft.mode)];
  for (const field of ["facts", "need", "step", "support"]) {
    if (draft[field]?.trim()) lines.push(t("express." + field) + "\n" + draft[field].trim());
  }
  return lines.join("\n\n");
}

export function createExpressionController({
  api, t, esc, getAccount, getEmotions, tzOffset, overlay, open, close,
  confirm, toast, render, changed, download, getAI, openWheel, goPractice,
}) {
  const empty = () => ({ text: "", mode: "space", emotion: "", facts: "", need: "", step: "", support: "" });
  let draft = empty();
  let active = false;
  let busy = false;
  let pending = null;
  let error = "";
  let epoch = 0;
  /* AI 侧状态：结果只活在内存里，不写进记录，除非用户点「填进整理框」 */
  const ai = { busy: false, error: "", clarify: null, steps: null, intent: "", quota: null };
  const aiReady = () => Boolean(getAI && getAI().enabled);
  const clearAI = () => Object.assign(ai, { error: "", clarify: null, steps: null, quota: null });
  const dirty = () => Object.keys(EXPRESSION_FIELDS).some(key => draft[key].trim());
  const field = (key, id) => `<label class="stack gap-8" for="${id}">
    <span class="field-label">${t("express." + (key === "text" ? "label" : key))}</span>
    <textarea id="${id}" data-expression-field="${key}" maxlength="${EXPRESSION_FIELDS[key]}" ${id === "expression-dialog-text" ? "data-autofocus" : ""} ${pending ? "readonly" : ""}
      aria-describedby="${id}-hint" placeholder="${t(key === "text" ? "express.placeholder" : "express.optional")}">${esc(draft[key])}</textarea>
    <span id="${id}-hint" class="tiny">${t("express.optional")} · <span data-expression-count="${key}">${draft[key].length}</span>/${EXPRESSION_FIELDS[key]}</span>
  </label>`;
  const modes = () => `<div class="expression-modes" role="group" aria-label="${t("express.choose")}">
    ${EXPRESSION_MODES.map(mode => `<button class="expression-mode ${draft.mode === mode ? "on" : ""}" data-act="e-mode" data-mode="${mode}"
      aria-pressed="${draft.mode === mode}" ${pending ? "disabled" : ""}>${t("express.mode_" + mode)}</button>`).join("")}
  </div>`;

  function card() {
    return `<section class="card expression-card stack gap-16" aria-labelledby="expression-home-title">
      <div class="stack gap-6"><h2 class="section-title" id="expression-home-title">${t("express.title")}</h2></div>
      ${field("text", "expression-home-text")}
      <p class="tiny expression-privacy">${t("express.memory")}</p>
      <button class="btn block" data-act="e-open">${t("express.open")} <span aria-hidden="true">→</span></button>
    </section>`;
  }

  function quotaLine() {
    if (!ai.quota) return "";
    if (ai.quota.limit === null) return `<span class="tiny">${t("express.ai_quota_pro")}</span>`;
    const left = ai.quota.remaining;
    return `<span class="tiny">${left > 0 ? t("express.ai_quota_left", { n: left }) : t("express.ai_quota_none")}</span>`;
  }

  function clarifyResult() {
    const result = ai.clarify;
    const emotion = result.emotion ? (getEmotions().find(e => e.key === result.emotion) || {}) : null;
    return `<div class="stack gap-10 expression-ai-result" role="status">
      <div class="row-between"><span class="eyebrow">${t("express.ai_clarify")}</span>${quotaLine()}</div>
      ${result.facts ? `<div class="stack gap-4"><span class="field-label">${t("express.ai_facts")}</span><p class="small">${esc(result.facts)}</p></div>` : ""}
      ${result.needs?.length ? `<div class="stack gap-4"><span class="field-label">${t("express.ai_needs")}</span>
        <ul class="expression-ai-list">${result.needs.map(item => `<li class="small">${esc(item)}</li>`).join("")}</ul>
        <span class="tiny">${t("express.ai_guess_note")}</span></div>` : ""}
      ${emotion ? `<div class="stack gap-4"><span class="field-label">${t("express.ai_emotion")}</span><p class="small">${esc(emotion.label || "")}</p></div>` : ""}
      ${result.question ? `<div class="stack gap-4"><span class="field-label">${t("express.ai_question")}</span><p class="small">${esc(result.question)}</p></div>` : ""}
      <div class="row gap-8" style="flex-wrap:wrap">
        <button class="btn soft sm" data-act="e-ai-fill">${t("express.ai_fill")}</button>
        <button class="btn ghost sm" data-act="e-ai-discard">${t("express.ai_discard")}</button>
      </div>
    </div>`;
  }

  function stepsResult() {
    return `<div class="stack gap-10 expression-ai-result" role="status">
      <div class="row-between"><span class="eyebrow">${t("express.ai_suggest")}</span>${quotaLine()}</div>
      <ul class="expression-ai-list">${ai.steps.steps.map(step => `<li class="small">${esc(step.text)}</li>`).join("")}</ul>
      ${ai.steps.note ? `<p class="tiny">${esc(ai.steps.note)}</p>` : ""}
      <span class="tiny">${t("express.ai_step_note")}</span>
      <button class="btn ghost sm" data-act="e-ai-discard">${t("express.ai_discard")}</button>
    </div>`;
  }

  function clarifyBlock() {
    if (ai.clarify) return clarifyResult();
    if (!aiReady()) return `<p class="tiny">${t("express.ai_unavailable")}</p>`;
    return `<div class="stack gap-6">
      <button class="btn block" data-act="e-clarify" ${ai.busy ? "disabled" : ""}>${ai.busy ? t("express.ai_busy") : t("express.ai_clarify")}</button>
      <p class="tiny">${t("express.ai_note")}</p>
      ${quotaLine()}
    </div>`;
  }

  function nextBlock() {
    const steps = ai.steps ? stepsResult() : "";
    const pickers = `<div class="row gap-8" style="flex-wrap:wrap">
      <button class="btn soft sm" data-act="e-wheel">${t("express.next_wheel")}</button>
      <button class="btn soft sm" data-act="e-practice">${t("express.next_practice")}</button>
    </div>`;
    const intent = aiReady() ? `<label class="stack gap-6">
        <span class="field-label">${t("express.next_intent_label")}</span>
        <input type="text" data-expression-intent maxlength="${INTENT_LIMIT}" value="${esc(ai.intent)}"
          placeholder="${t("express.next_intent_placeholder")}" ${ai.busy ? "readonly" : ""} />
        <span class="tiny"><span data-intent-count>${ai.intent.length}</span>/${INTENT_LIMIT}</span>
      </label>
      <button class="btn block" data-act="e-steps" ${ai.busy || !ai.intent.trim() ? "disabled" : ""}>${ai.busy ? t("express.ai_busy") : t("express.ai_suggest")}</button>
      <p class="tiny">${t("express.ai_note")}</p>` : `<p class="tiny">${t("express.ai_unavailable")}</p>`;
    return `<div class="stack gap-12">${pickers}${steps || intent}</div>`;
  }

  function manualFields(keys) {
    return `<details class="expression-manual stack gap-12">
      <summary class="field-label">${t("express.ai_manual")}</summary>
      ${keys.map(key => field(key, "expression-" + key)).join("")}
    </details>`;
  }

  function panel() {
    const prompts = draft.mode === "clarify" ? ["facts", "need"] : draft.mode === "next" ? ["step", "support"] : [];
    const emotions = getEmotions();
    const aiError = ai.error ? `<p class="expression-error small" role="alert">${esc(ai.error)}</p>` : "";
    return `<div class="sheet center expression-sheet stack gap-20">
      <div class="row-between"><div class="stack gap-6"><span class="eyebrow">${t("express.eyebrow")}</span><h2 class="section-title">${t("express.title")}</h2></div>
        <button class="icon-btn" data-act="close" aria-label="${t("common.close")}" ${busy ? "disabled" : ""}>×</button></div>
      <p class="tiny expression-privacy">${t("express.memory")}</p>
      ${field("text", "expression-dialog-text")}
      <div class="stack gap-8"><h3 class="field-label">${t("express.choose")}</h3>${modes()}</div>
      <div class="expression-prompts stack gap-16" aria-live="polite">
        <p class="small">${t("express.hint_" + draft.mode)}</p>
        ${draft.mode === "clarify" ? clarifyBlock() : ""}
        ${draft.mode === "next" ? nextBlock() : ""}
        ${aiError}
        ${prompts.length ? manualFields(prompts) : ""}
      </div>
      <details class="expression-save stack gap-12" ${pending || error ? "open" : ""}>
        <summary class="field-label">${t("express.keep")}</summary>
        <div class="stack gap-12">
          <button class="btn soft block" data-act="e-download" ${busy ? "disabled" : ""}>${t("express.download")}</button>
          <p class="tiny">${t("express.download_note")}</p>
          ${getAccount() ? `<label class="stack gap-8" for="expression-emotion"><span class="field-label">${t("express.emotion")}</span>
            <select id="expression-emotion" data-expression-emotion ${pending ? "disabled" : ""}><option value="">${t("express.emotion_placeholder")}</option>
              ${emotions.map(e => `<option value="${esc(e.key)}" ${draft.emotion === e.key ? "selected" : ""}>${esc(e.label)}</option>`).join("")}
            </select></label><p class="tiny">${t("express.server_note")}</p>
            <button class="btn block" data-act="e-save" ${busy ? "disabled" : ""}>${t(busy ? "express.saving" : pending ? "express.retry" : "express.save")}</button>`
            : `<p class="small">${t("express.guest")}</p>`}
          ${error ? `<p class="expression-error small" role="alert">${esc(error)} ${t("express.save_failed")}</p>` : ""}
        </div>
      </details>
      <div class="stack gap-8"><button class="btn ghost block" data-act="e-clear" ${busy ? "disabled" : ""}>${t("express.clear")}</button>
        <p class="tiny">${t("express.close_note")}</p></div>
    </div>`;
  }

  function paint(focus = false) {
    const keepOpen = active && overlay.querySelector(".expression-save")?.open;
    const keepManual = active && overlay.querySelector(".expression-manual")?.open;
    active = true;
    open(panel(), { focus, label: t("express.title") });
    if (keepOpen) {
      const details = overlay.querySelector(".expression-save");
      if (details) details.open = true;
    }
    if (keepManual) {
      const details = overlay.querySelector(".expression-manual");
      if (details) details.open = true;
    }
  }

  function input(node) {
    if (node.dataset?.expressionIntent !== undefined) {
      ai.intent = node.value.slice(0, INTENT_LIMIT);
      const count = overlay.querySelector("[data-intent-count]");
      if (count) count.textContent = String(ai.intent.length);
      const button = overlay.querySelector('[data-act="e-steps"]');
      if (button) button.disabled = ai.busy || !ai.intent.trim();
      return;
    }
    const key = node.dataset?.expressionField;
    if (!Object.hasOwn(EXPRESSION_FIELDS, key) || pending) return;
    draft[key] = node.value.slice(0, EXPRESSION_FIELDS[key]);
    const root = node.closest(".expression-card, .expression-sheet");
    const count = root?.querySelector(`[data-expression-count="${key}"]`);
    if (count) count.textContent = String(draft[key].length);
  }

  function change(node) {
    if (node.matches("[data-expression-emotion]") && !pending) {
      draft.emotion = getEmotions().some(e => e.key === node.value) ? node.value : "";
    }
  }

  /* 每次调用前确认一次：这是唯一会把正文发出去的动作。 */
  async function consent() {
    return confirm({ title: t("express.ai_confirm_title"), body: t("express.ai_confirm_body"), ok: t("express.ai_confirm_ok") });
  }

  async function runClarify() {
    const text = draft.text.trim();
    if (!text) { toast(t("express.text_required")); return; }
    if (!await consent()) return;
    const owner = epoch;
    ai.busy = true;
    ai.error = "";
    paint();
    try {
      const result = await api.clarifyExpression(text);
      if (owner !== epoch) return;
      ai.clarify = result;
      ai.quota = result.quota || null;
    } catch (err) {
      if (owner !== epoch) return;
      ai.error = err.message || t("express.ai_failed");
    } finally {
      if (owner === epoch) {
        ai.busy = false;
        if (active) paint();
      }
    }
  }

  async function runSteps() {
    /* 只发用户为「想做的事」单独写的那句，不回退正文：少一段字离开设备。 */
    const text = ai.intent.trim();
    if (!text) { toast(t("express.ai_intent_required")); return; }
    if (!await consent()) return;
    const owner = epoch;
    ai.busy = true;
    ai.error = "";
    paint();
    try {
      const result = await api.nextSteps(text);
      if (owner !== epoch) return;
      ai.steps = result;
      ai.quota = result.quota || null;
    } catch (err) {
      if (owner !== epoch) return;
      ai.error = err.message || t("express.ai_failed");
    } finally {
      if (owner === epoch) {
        ai.busy = false;
        if (active) paint();
      }
    }
  }

  function fillFromClarify() {
    if (!ai.clarify) return;
    draft.facts = (ai.clarify.facts || "").slice(0, EXPRESSION_FIELDS.facts);
    draft.need = (ai.clarify.needs || []).join("；").slice(0, EXPRESSION_FIELDS.need);
    ai.clarify = null;
    paint();
    toast(t("express.ai_filled"));
  }

  async function save() {
    if (busy) return;
    if (!getAccount()) { toast(t("express.guest")); return; }
    if (!dirty()) { toast(t("express.text_required")); return; }
    if (!getEmotions().some(e => e.key === draft.emotion)) { toast(t("express.emotion_required")); return; }
    busy = true;
    const owner = epoch;
    try {
      if (!pending) {
        if (!await confirm({ title: t("express.save_confirm"), body: t("express.server_note"), ok: t("express.save") })) return;
        if (owner !== epoch) return;
        // Freeze the complete request, not just its ID: retries must be identical.
        pending = { emotion: draft.emotion, cause: draft.text.trim(), note: expressionNote(draft, t),
          tz_offset: tzOffset(), occurred_at: new Date().toISOString(),
          client_id: crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join(""),
          intervention_completed: false };
      }
      error = "";
      paint();
      await api.createEntry({ ...pending });
    } catch (err) {
      if (owner !== epoch) return;
      error = err.message;
      return;
    } finally {
      if (owner === epoch) busy = false;
      if (owner === epoch && active && error) {
        paint();
        if (error) overlay.querySelector('[data-act="e-save"]')?.focus();
      }
    }
    if (owner !== epoch) return;
    draft = empty();
    pending = null;
    error = "";
    active = false;
    clearAI();
    close();
    render();
    toast(t("express.saved"));
    // A failed refresh must never make a successful save look unsuccessful.
    try { await changed(); } catch { toast(t("express.refresh_failed")); }
  }

  async function handle(action, node) {
    if (busy || ai.busy) return;
    if (action === "open") { paint(true); return; }
    if (action === "mode" && !pending && EXPRESSION_MODES.includes(node.dataset.mode)) {
      draft.mode = node.dataset.mode;
      ai.error = "";
      paint();
      overlay.querySelector(`[data-act="e-mode"][data-mode="${draft.mode}"]`)?.focus();
    } else if (action === "clarify") {
      await runClarify();
    } else if (action === "steps") {
      await runSteps();
    } else if (action === "ai-fill") {
      fillFromClarify();
    } else if (action === "ai-discard") {
      clearAI();
      paint();
    } else if (action === "wheel") {
      close();
      openWheel();
    } else if (action === "practice") {
      close();
      goPractice();
    } else if (action === "save") {
      await save();
    } else if (action === "download") {
      if (!dirty()) { toast(t("express.text_required")); return; }
      download(draft.text + "\n\n" + expressionNote(draft, t));
      toast(t("express.downloaded"));
    } else if (action === "clear") {
      if (dirty() && !await confirm({ title: t("express.clear_confirm"),
        body: t(pending ? "express.pending_clear" : "express.clear_note"), ok: t("express.clear") })) return;
      reset();
      close();
      render();
      toast(t("express.cleared"));
    }
  }

  function suspend() { ++epoch; active = false; busy = false; ai.busy = false; }
  function reset() { suspend(); draft = empty(); pending = null; error = ""; ai.intent = ""; clearAI(); }
  return { card, handle, input, change, isDirty: dirty, reset, suspend, detach: () => { active = false; },
    get active() { return active; }, get busy() { return busy || ai.busy; } };
}
