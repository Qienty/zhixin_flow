/* Free expression stays in memory until an explicit download or account save.
   This module uses local prompts; it never calls a model or sends messages. */
const EXPRESSION_MODES = ["space", "clarify", "next"];
const EXPRESSION_FIELDS = { text: 500, facts: 160, need: 160, step: 160, support: 160 };

export function expressionNote(draft, t) {
  const lines = [t("express.saved_mode") + t("express.mode_" + draft.mode)];
  for (const field of ["facts", "need", "step", "support"]) {
    if (draft[field]?.trim()) lines.push(t("express." + field) + "\n" + draft[field].trim());
  }
  return lines.join("\n\n");
}

export function createExpressionController({
  api, t, esc, getAccount, getEmotions, tzOffset, overlay, open, close,
  confirm, toast, render, changed, download,
}) {
  const empty = () => ({ text: "", mode: "space", emotion: "", facts: "", need: "", step: "", support: "" });
  let draft = empty();
  let active = false;
  let busy = false;
  let pending = null;
  let error = "";
  let epoch = 0;
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
      <div class="stack gap-6"><span class="eyebrow">${t("express.eyebrow")}</span><h2 class="section-title" id="expression-home-title">${t("express.title")}</h2></div>
      ${field("text", "expression-home-text")}
      <p class="tiny expression-privacy">${t("express.memory")}</p>
      <button class="btn block" data-act="e-open">${t("express.open")} <span aria-hidden="true">→</span></button>
    </section>`;
  }

  function panel() {
    const prompts = draft.mode === "clarify" ? ["facts", "need"] : draft.mode === "next" ? ["step", "support"] : [];
    const emotions = getEmotions();
    return `<div class="sheet center expression-sheet stack gap-20">
      <div class="row-between"><div class="stack gap-6"><span class="eyebrow">${t("express.eyebrow")}</span><h2 class="section-title">${t("express.title")}</h2></div>
        <button class="icon-btn" data-act="close" aria-label="${t("common.close")}" ${busy ? "disabled" : ""}>×</button></div>
      <p class="tiny expression-privacy">${t("express.memory")}</p>
      ${field("text", "expression-dialog-text")}
      <div class="stack gap-8"><h3 class="field-label">${t("express.choose")}</h3>${modes()}</div>
      <div class="expression-prompts stack gap-16" aria-live="polite">
        <p class="small">${t("express.hint_" + draft.mode)}</p>
        ${prompts.length ? `<p class="tiny">${t("express.local_prompts")}</p>${prompts.map(key => field(key, "expression-" + key)).join("")}` : ""}
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
    active = true;
    open(panel(), { focus, label: t("express.title") });
    if (keepOpen) {
      const details = overlay.querySelector(".expression-save");
      if (details) details.open = true;
    }
  }

  function input(node) {
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
    close();
    render();
    toast(t("express.saved"));
    // A failed refresh must never make a successful save look unsuccessful.
    try { await changed(); } catch { toast(t("express.refresh_failed")); }
  }

  async function handle(action, node) {
    if (busy) return;
    if (action === "open") { paint(true); return; }
    if (action === "mode" && !pending && EXPRESSION_MODES.includes(node.dataset.mode)) {
      draft.mode = node.dataset.mode;
      paint();
      overlay.querySelector(`[data-act="e-mode"][data-mode="${draft.mode}"]`)?.focus();
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

  function suspend() { ++epoch; active = false; busy = false; }
  function reset() { suspend(); draft = empty(); pending = null; error = ""; }
  return { card, handle, input, change, isDirty: dirty, reset, suspend, detach: () => { active = false; },
    get active() { return active; }, get busy() { return busy; } };
}
