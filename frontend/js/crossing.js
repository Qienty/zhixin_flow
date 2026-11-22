/* 跨境表达：8 个真实场景，产出一条可以直接发出去的消息。

   和 expression.js 的分工：
   - expression.js 是「写下此刻」，保存与隐私优先；
   - 这里是「说给对方听」，目标是产出能复制的句子，所以结果区第一动作是复制，
     而不是替换原稿。没有任何内容会写入账号，除非用户自己去保存。 */

const SCENES = ["deadline", "decline", "apologize", "ask_help", "conflict", "bad_news", "negotiate", "boundary"];
const TONES = ["polite", "direct", "close"];
const RELATIONS = ["professor", "manager", "client", "peer", "friend"];
const FIELDS = ["what", "when", "want", "extra"];

export function createCrossingController({ api, t, esc, overlay, open, close, toast, getAccount, getLang }) {
  let active = false;
  let step = "scenes";
  let scene = "";
  let tone = "polite";
  let relation = "peer";
  let target = "";
  let fields = { what: "", when: "", want: "", extra: "" };
  let busy = false;
  let error = "";
  let result = { text: "", alternatives: [], notes: [], intent: "" };
  let epoch = 0;

  const defaultTarget = () => (getLang() === "zh" ? "en" : "zh");
  const sceneTitle = (id) => t("cross.scene_" + id);
  const sceneHint = (id) => t("cross.hint_" + id);
  const filled = () => FIELDS.some((key) => fields[key].trim());
  const resetResult = () => { result = { text: "", alternatives: [], notes: [], intent: "" }; error = ""; };
  const reset = () => { fields = { what: "", when: "", want: "", extra: "" }; resetResult(); };

  function card() {
    return `<section class="card cross-card stack gap-12" aria-labelledby="cross-home-title">
      <div class="row-between">
        <h2 class="section-title" id="cross-home-title">${t("cross.title")}</h2>
        <span class="tiny">${t("cross.eyebrow")}</span>
      </div>
      <p class="tiny">${t("cross.intro")}</p>
      <div class="cross-chips">
        ${SCENES.map((id) => `<button class="chip" data-act="c-start" data-id="${id}">${esc(sceneTitle(id))}</button>`).join("")}
      </div>
      <p class="tiny">${t("cross.disclaimer")}</p>
    </section>`;
  }

  function scenesStep() {
    return `<div class="stack gap-16">
      <div><span class="eyebrow">${t("cross.eyebrow")}</span><h2 class="section-title">${t("cross.pick")}</h2></div>
      <p class="small">${t("cross.pick_note")}</p>
      <div class="cross-scenes">
        ${SCENES.map((id) => `<button class="cross-scene" data-act="c-scene" data-id="${id}">
          <strong>${esc(sceneTitle(id))}</strong><span class="tiny">${esc(sceneHint(id))}</span>
        </button>`).join("")}
      </div>
      <p class="tiny">${t("cross.disclaimer")}</p>
    </div>`;
  }

  function formStep() {
    return `<div class="stack gap-16">
      <div class="stack gap-6">
        <span class="eyebrow">${t("cross.eyebrow")}</span>
        <h2 class="section-title">${esc(sceneTitle(scene))}</h2>
        <p class="small">${esc(sceneHint(scene))}</p>
      </div>
      ${FIELDS.map((key) => `<label class="stack gap-8">
        <span class="field-label">${t("cross.field_" + key)}</span>
        <textarea data-cross-field="${key}" maxlength="200" rows="2" placeholder="${t("cross.optional")}" ${busy ? "readonly" : ""}>${esc(fields[key])}</textarea>
      </label>`).join("")}
      <div class="chip-row">
        <label class="sr-only" for="cross-target">${t("cross.target")}</label>
        <select id="cross-target" data-cross-target ${busy ? "disabled" : ""}>
          <option value="en" ${target === "en" ? "selected" : ""}>${t("cross.lang_en")}</option>
          <option value="zh" ${target === "zh" ? "selected" : ""}>${t("cross.lang_zh")}</option>
        </select>
        <label class="sr-only" for="cross-relation">${t("cross.relation")}</label>
        <select id="cross-relation" data-cross-relation ${busy ? "disabled" : ""}>
          ${RELATIONS.map((id) => `<option value="${id}" ${relation === id ? "selected" : ""}>${t("cross.rel_" + id)}</option>`).join("")}
        </select>
      </div>
      <div class="stack gap-8">
        <h3 class="field-label">${t("cross.tone")}</h3>
        <div class="chip-row">
          ${TONES.map((id) => `<button class="chip ${tone === id ? "on" : ""}" data-act="c-tone" data-id="${id}" aria-pressed="${tone === id}">${t("cross.tone_" + id)}</button>`).join("")}
        </div>
      </div>
      ${error ? `<p class="expression-error small" role="alert">${esc(error)}</p>` : ""}
      <div class="stack gap-8">
        <button class="btn block" data-act="c-generate" ${busy ? "disabled" : ""}>${busy ? t("cross.working") : t("cross.generate")}</button>
        <button class="btn ghost block" data-act="c-back" ${busy ? "disabled" : ""}>${t("cross.back")}</button>
      </div>
      <p class="tiny">${t("cross.disclaimer")}</p>
    </div>`;
  }

  function resultStep() {
    return `<div class="stack gap-16">
      <div class="stack gap-6">
        <span class="eyebrow">${esc(sceneTitle(scene))} · ${t("cross.tone_" + tone)}</span>
        <h2 class="section-title">${t("cross.result")}</h2>
      </div>
      <label class="stack gap-8" for="cross-result-text">
        <textarea id="cross-result-text" class="cross-result" readonly rows="5">${esc(result.text)}</textarea>
      </label>
      <button class="btn block" data-act="c-copy" ${busy ? "disabled" : ""}>${t("cross.copy")}</button>
      ${result.alternatives.length ? result.alternatives.map((alt, index) => `<div class="cross-alt stack gap-8">
        <div class="row-between"><span class="field-label">${t("cross.alternative")} · ${t("cross.tone_" + alt.tone)}</span>
          <button class="text-link" data-act="c-copy-alt" data-index="${index}">${t("cross.copy")}</button></div>
        <p class="small cross-alt-text">${esc(alt.text)}</p>
      </div>`).join("") : ""}
      ${result.notes.length ? `<div class="stack gap-6"><h3 class="field-label">${t("cross.notes")}</h3>
        <ul class="tiny cross-notes">${result.notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul></div>` : ""}
      ${error ? `<p class="expression-error small" role="alert">${esc(error)}</p>` : ""}
      <div class="stack gap-8">
        <button class="btn soft block" data-act="c-again" ${busy ? "disabled" : ""}>${busy ? t("cross.working") : t("cross.again")}</button>
        <button class="btn ghost block" data-act="c-back" ${busy ? "disabled" : ""}>${t("cross.back")}</button>
      </div>
      <p class="tiny">${t("cross.disclaimer")}</p>
    </div>`;
  }

  function panel() {
    if (step === "form") return formStep();
    if (step === "result") return resultStep();
    return scenesStep();
  }

  function paint(focus = false) {
    active = true;
    open(panel(), { focus, label: t("cross.title") });
    if (focus) overlay.querySelector("textarea")?.focus();
  }

  function start(id) {
    if (!getAccount()) { toast(t("cross.guest")); return; }
    scene = SCENES.includes(id) ? id : "deadline";
    target = defaultTarget();
    reset();
    step = "form";
    paint(true);
  }

  async function generate() {
    if (busy) return;
    if (!filled()) { toast(t("cross.required")); return; }
    const owner = epoch;
    busy = true;
    error = "";
    paint();
    try {
      const details = {};
      for (const key of FIELDS) if (fields[key].trim()) details[key] = fields[key].trim();
      const payload = { scene, tone, relation, target_lang: target, mode: "next", details };
      const response = await api.translateExpression(payload);
      if (owner !== epoch) return;
      result = {
        text: response.text || "",
        alternatives: Array.isArray(response.alternatives) ? response.alternatives : [],
        notes: Array.isArray(response.cultural_notes) ? response.cultural_notes : [],
        intent: response.intent || "",
      };
      step = "result";
    } catch (err) {
      if (owner !== epoch) return;
      error = err.message;
    } finally {
      if (owner === epoch) {
        busy = false;
        if (active) paint();
      }
    }
  }

  async function copy(text) {
    if (!text) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      toast(t("cross.copied"));
      return;
    } catch {
      /* 不是所有 webview 都给 clipboard 权限，退回选中文本 */
    }
    const area = overlay.querySelector("#cross-result-text");
    if (!area) { toast(t("cross.copy_failed")); return; }
    area.removeAttribute("readonly");
    area.select();
    try { document.execCommand("copy"); toast(t("cross.copied")); }
    catch { toast(t("cross.copy_failed")); }
    area.setAttribute("readonly", "");
  }

  async function handle(action, node) {
    if (busy && action !== "copy-alt") return;
    if (action === "start" || action === "scene") return start(node.dataset.id);
    if (action === "tone") {
      tone = TONES.includes(node.dataset.id) ? node.dataset.id : tone;
      return paint(false);
    }
    if (action === "back") { step = "scenes"; resetResult(); return paint(true); }
    if (action === "generate" || action === "again") {
      if (step === "result") step = "form";
      return generate();
    }
    if (action === "copy") return copy(overlay.querySelector("#cross-result-text")?.value || result.text);
    if (action === "copy-alt") {
      const alt = result.alternatives[Number(node.dataset.index)];
      if (!alt) return;
      return copy(alt.text);
    }
    if (action === "close") return close();
  }

  function input(node) {
    const key = node.dataset?.crossField;
    if (!key || busy || !FIELDS.includes(key)) return;
    fields[key] = node.value.slice(0, 200);
  }

  function change(node) {
    if (node.matches("[data-cross-target]") && !busy) target = node.value === "zh" ? "zh" : "en";
    if (node.matches("[data-cross-relation]") && !busy && RELATIONS.includes(node.value)) relation = node.value;
  }

  return {
    card, handle, input, change, reset,
    get active() { return active; },
    get busy() { return busy; },
    detach() { active = false; ++epoch; busy = false; },
  };
}