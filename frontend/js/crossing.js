/* 双向跨语言沟通：说出去 + 听明白。

   和 expression.js 的分工：
   - expression.js 是「写下此刻」，保存与隐私优先；
   - 这里是「说给对方听 / 听懂对方」，目标是产出能复制、能用的句子，
     结果第一动作是复制，而不是替换原稿。任何内容都不写入账号。 */

const SCENES = ["deadline", "decline", "apologize", "ask_help", "conflict", "bad_news", "negotiate", "boundary"];
const TONES = ["polite", "direct", "close"];
const RELATIONS = ["professor", "manager", "client", "peer", "friend"];
const FIELDS = ["what", "when", "want", "extra"];

export function createCrossingController({ api, t, esc, overlay, open, close, toast, getAccount, getLang, getAI, getLanguages }) {
  let active = false;
  let mode = "say"; // say | hear
  let step = "scenes"; // scenes | form | result | hear-form | hear-result

  // 说出去
  let scene = "";
  let tone = "polite";
  let relation = "peer";
  let target = "";
  let fields = { what: "", when: "", want: "", extra: "" };
  let sayBusy = false;
  let sayError = "";
  let sayResult = { text: "", alternatives: [], notes: [], intent: "" };

  // 听明白
  let heard = "";
  let heardSource = "";
  let heardRelation = "peer";
  let hearBusy = false;
  let hearError = "";
  let reading = { meaning: "", tone: "", subtext: "", replies: [], notes: [] };

  let epoch = 0;

  const defaultTarget = () => {
    const codes = languages().map((item) => item.code);
    const opposite = getLang() === "zh" ? "en" : "zh";
    return codes.includes(opposite) ? opposite : codes[0];
  };
  /* 语言列表由服务端配置（TETHER_LANGUAGES）决定：加一门语言不用改前端。 */
  const languages = () => {
    const list = (typeof getLanguages === "function" ? getLanguages() : null) || [];
    const usable = list
      .filter((item) => item && item.code)
      .map((item) => ({ code: String(item.code), label: item.label || String(item.code) }));
    return usable.length ? usable : [{ code: "en", label: t("cross.lang_en") }, { code: "zh", label: t("cross.lang_zh") }];
  };
  const languageOptions = (selected) => languages()
    .map((item) => `<option value="${esc(item.code)}" ${selected === item.code ? "selected" : ""}>${esc(item.label)}</option>`)
    .join("");
  /* AI 是产品的一部分，不是可选插件：没连上时先说清楚，不让用户填完表单再收到 503。 */
  const aiReady = () => {
    const ai = (typeof getAI === "function" ? getAI() : null) || {};
    return ai.enabled !== false;
  };
  const aiGap = () => `<p class="cross-ai-gap tiny" role="status">${t("cross.ai_disabled")}</p>`;
  const sceneTitle = (id) => t("cross.scene_" + id);
  const sceneHint = (id) => t("cross.hint_" + id);
  const filled = () => FIELDS.some((key) => fields[key].trim());
  const resetSay = () => { sayResult = { text: "", alternatives: [], notes: [], intent: "" }; sayError = ""; };
  const resetHear = () => { reading = { meaning: "", tone: "", subtext: "", replies: [], notes: [] }; hearError = ""; };
  const reset = () => { fields = { what: "", when: "", want: "", extra: "" }; resetSay(); heard = ""; resetHear(); };

  function card() {
    return `<section class="card cross-card stack gap-12" aria-labelledby="cross-home-title">
      <div class="row-between">
        <h2 class="section-title" id="cross-home-title">${t("cross.title")}</h2>
        <span class="tiny">${t("cross.eyebrow")}</span>
      </div>
      <p class="tiny">${t("cross.intro")}</p>
      ${aiReady() ? "" : aiGap()}
      <div class="cross-chips">
        ${SCENES.map((id) => `<button class="chip" data-act="c-start" data-id="${id}">${esc(sceneTitle(id))}</button>`).join("")}
      </div>
      <div class="cross-hear">
        <p class="tiny">${t("cross.hear_entry")}</p>
        <button class="btn soft sm block" data-act="c-hear">${t("cross.hear_button")} <span aria-hidden="true">→</span></button>
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
      <button class="btn ghost block" data-act="c-hear">${t("cross.hear_button")}</button>
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
        <textarea data-cross-field="${key}" maxlength="200" rows="2" placeholder="${t("cross.optional")}" ${sayBusy ? "readonly" : ""}>${esc(fields[key])}</textarea>
      </label>`).join("")}
      <div class="chip-row">
        <label class="sr-only" for="cross-target">${t("cross.target")}</label>
        <select id="cross-target" data-cross-target ${sayBusy ? "disabled" : ""}>
          ${languageOptions(target)}
        </select>
        <label class="sr-only" for="cross-relation">${t("cross.relation")}</label>
        <select id="cross-relation" data-cross-relation ${sayBusy ? "disabled" : ""}>
          ${RELATIONS.map((id) => `<option value="${id}" ${relation === id ? "selected" : ""}>${t("cross.rel_" + id)}</option>`).join("")}
        </select>
      </div>
      <div class="stack gap-8">
        <h3 class="field-label">${t("cross.tone")}</h3>
        <div class="chip-row">
          ${TONES.map((id) => `<button class="chip ${tone === id ? "on" : ""}" data-act="c-tone" data-id="${id}" aria-pressed="${tone === id}">${t("cross.tone_" + id)}</button>`).join("")}
        </div>
      </div>
      ${sayError ? `<p class="expression-error small" role="alert">${esc(sayError)}</p>` : ""}
      <div class="stack gap-8">
        ${aiReady() ? "" : aiGap()}
        <button class="btn block" data-act="c-generate" ${sayBusy || !aiReady() ? "disabled" : ""}>${sayBusy ? t("cross.working") : t("cross.generate")}</button>
        <button class="btn ghost block" data-act="c-back" ${sayBusy ? "disabled" : ""}>${t("cross.back")}</button>
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
        <textarea id="cross-result-text" class="cross-result" readonly rows="5">${esc(sayResult.text)}</textarea>
      </label>
      <button class="btn block" data-act="c-copy" ${sayBusy ? "disabled" : ""}>${t("cross.copy")}</button>
      ${sayResult.alternatives.length ? sayResult.alternatives.map((alt, index) => `<div class="cross-alt stack gap-8">
        <div class="row-between"><span class="field-label">${t("cross.alternative")} · ${t("cross.tone_" + alt.tone)}</span>
          <button class="text-link" data-act="c-copy-alt" data-index="${index}">${t("cross.copy")}</button></div>
        <p class="small cross-alt-text">${esc(alt.text)}</p>
      </div>`).join("") : ""}
      ${sayResult.notes.length ? `<div class="stack gap-6"><h3 class="field-label">${t("cross.notes")}</h3>
        <ul class="tiny cross-notes">${sayResult.notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul></div>` : ""}
      ${sayError ? `<p class="expression-error small" role="alert">${esc(sayError)}</p>` : ""}
      <div class="stack gap-8">
        <button class="btn soft block" data-act="c-again" ${sayBusy ? "disabled" : ""}>${sayBusy ? t("cross.working") : t("cross.again")}</button>
        <button class="btn ghost block" data-act="c-back" ${sayBusy ? "disabled" : ""}>${t("cross.back")}</button>
      </div>
      <p class="tiny">${t("cross.disclaimer")}</p>
    </div>`;
  }

  function hearForm() {
    return `<div class="stack gap-16">
      <div class="stack gap-6">
        <span class="eyebrow">${t("cross.eyebrow")}</span>
        <h2 class="section-title">${t("cross.hear_title")}</h2>
        <p class="small">${t("cross.hear_intro")}</p>
      </div>
      <label class="stack gap-8" for="cross-heard">
        <span class="field-label">${t("cross.hear_placeholder")}</span>
        <textarea id="cross-heard" data-heard maxlength="500" rows="4" ${hearBusy ? "readonly" : ""}>${esc(heard)}</textarea>
      </label>
      <div class="chip-row">
        <label class="sr-only" for="cross-heard-source">${t("cross.hear_source")}</label>
        <select id="cross-heard-source" data-heard-source ${hearBusy ? "disabled" : ""}>
          ${languageOptions(heardSource)}
        </select>
        <label class="sr-only" for="cross-heard-relation">${t("cross.relation")}</label>
        <select id="cross-heard-relation" data-heard-relation ${hearBusy ? "disabled" : ""}>
          ${RELATIONS.map((id) => `<option value="${id}" ${heardRelation === id ? "selected" : ""}>${t("cross.rel_" + id)}</option>`).join("")}
        </select>
      </div>
      ${hearError ? `<p class="expression-error small" role="alert">${esc(hearError)}</p>` : ""}
      <div class="stack gap-8">
        ${aiReady() ? "" : aiGap()}
        <button class="btn block" data-act="c-hear-generate" ${hearBusy || !aiReady() ? "disabled" : ""}>${hearBusy ? t("cross.hear_working") : t("cross.hear_generate")}</button>
        <button class="btn ghost block" data-act="c-back" ${hearBusy ? "disabled" : ""}>${t("cross.back")}</button>
      </div>
      <p class="tiny">${t("cross.hear_note")}</p>
    </div>`;
  }

  function hearResult() {
    const replies = reading.replies.length ? `<div class="stack gap-8">
      <h3 class="field-label">${t("cross.hear_replies")}</h3>
      ${reading.replies.map((reply, index) => `<div class="cross-alt stack gap-8">
        <div class="row-between"><span class="field-label">${t("cross.tone_" + reply.tone)}</span>
          <button class="text-link" data-act="c-copy-reply" data-index="${index}">${t("cross.copy")}</button></div>
        <p class="small cross-alt-text">${esc(reply.text)}</p>
      </div>`).join("")}
    </div>` : "";
    return `<div class="stack gap-16">
      <div class="stack gap-6">
        <span class="eyebrow">${t("cross.hear_title")}</span>
        <h2 class="section-title">${t("cross.hear_result")}</h2>
      </div>
      <p class="cross-tone-badge">${t("cross.hear_tone_" + reading.tone)}</p>
      <div class="stack gap-6">
        <h3 class="field-label">${t("cross.hear_meaning")}</h3>
        <p class="small cross-alt-text">${esc(reading.meaning)}</p>
      </div>
      ${reading.subtext ? `<div class="stack gap-6">
        <h3 class="field-label">${t("cross.hear_subtext")}</h3>
        <p class="small cross-alt-text">${esc(reading.subtext)}</p>
      </div>` : ""}
      ${reading.notes.length ? `<ul class="tiny cross-notes">${reading.notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>` : ""}
      ${replies}
      ${hearError ? `<p class="expression-error small" role="alert">${esc(hearError)}</p>` : ""}
      <div class="stack gap-8">
        <button class="btn soft block" data-act="c-hear-again" ${hearBusy ? "disabled" : ""}>${t("cross.hear_again")}</button>
        <button class="btn ghost block" data-act="c-back" ${hearBusy ? "disabled" : ""}>${t("cross.back")}</button>
      </div>
      <p class="tiny">${t("cross.hear_note")}</p>
    </div>`;
  }

  function panel() {
    if (mode === "hear") return step === "hear-result" ? hearResult() : hearForm();
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
    mode = "say";
    scene = SCENES.includes(id) ? id : "deadline";
    target = defaultTarget();
    reset();
    step = "form";
    paint(true);
  }

  function startHear() {
    if (!getAccount()) { toast(t("cross.guest")); return; }
    mode = "hear";
    step = "hear-form";
    heardSource = defaultTarget();
    resetHear();
    paint(true);
  }

  async function generate() {
    if (sayBusy) return;
    if (!aiReady()) { toast(t("cross.ai_disabled")); return; }
    if (!filled()) { toast(t("cross.required")); return; }
    const owner = epoch;
    sayBusy = true;
    sayError = "";
    paint();
    try {
      const details = {};
      for (const key of FIELDS) if (fields[key].trim()) details[key] = fields[key].trim();
      const payload = { scene, tone, relation, target_lang: target, mode: "next", details };
      const response = await api.translateExpression(payload);
      if (owner !== epoch) return;
      sayResult = {
        text: response.text || "",
        alternatives: Array.isArray(response.alternatives) ? response.alternatives : [],
        notes: Array.isArray(response.cultural_notes) ? response.cultural_notes : [],
        intent: response.intent || "",
      };
      step = "result";
    } catch (err) {
      if (owner !== epoch) return;
      sayError = err.message;
    } finally {
      if (owner === epoch) {
        sayBusy = false;
        if (active) paint();
      }
    }
  }

  async function interpret() {
    if (hearBusy) return;
    if (!aiReady()) { toast(t("cross.ai_disabled")); return; }
    if (!heard.trim()) { toast(t("cross.hear_required")); return; }
    const owner = epoch;
    hearBusy = true;
    hearError = "";
    paint();
    try {
      const response = await api.interpretMessage({
        text: heard.trim(), source_lang: heardSource, target_lang: getLang() === "en" ? "en" : "zh",
        relation: heardRelation,
      });
      if (owner !== epoch) return;
      reading = {
        meaning: response.meaning || "",
        tone: response.tone || "ambiguous",
        subtext: response.subtext || "",
        replies: Array.isArray(response.replies) ? response.replies : [],
        notes: Array.isArray(response.cultural_notes) ? response.cultural_notes : [],
      };
      step = "hear-result";
    } catch (err) {
      if (owner !== epoch) return;
      hearError = err.message;
    } finally {
      if (owner === epoch) {
        hearBusy = false;
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
    if ((sayBusy || hearBusy) && !["copy-alt", "copy-reply"].includes(action)) return;
    if (action === "start" || action === "scene") return start(node.dataset.id);
    if (action === "hear") return startHear();
    if (action === "hear-generate") return interpret();
    if (action === "hear-again") { step = "hear-form"; resetHear(); return paint(true); }
    if (action === "tone") {
      tone = TONES.includes(node.dataset.id) ? node.dataset.id : tone;
      return paint(false);
    }
    if (action === "back") {
      if (mode === "hear") { mode = "say"; step = "scenes"; resetHear(); return paint(true); }
      step = "scenes"; resetSay(); return paint(true);
    }
    if (action === "generate" || action === "again") {
      if (step === "result") step = "form";
      return generate();
    }
    if (action === "copy") return copy(overlay.querySelector("#cross-result-text")?.value || sayResult.text);
    if (action === "copy-alt") {
      const alt = sayResult.alternatives[Number(node.dataset.index)];
      if (!alt) return;
      return copy(alt.text);
    }
    if (action === "copy-reply") {
      const reply = reading.replies[Number(node.dataset.index)];
      if (!reply) return;
      return copy(reply.text);
    }
    if (action === "close") return close();
  }

  function input(node) {
    if (node.dataset?.heard !== undefined && !hearBusy) { heard = node.value.slice(0, 500); return; }
    const key = node.dataset?.crossField;
    if (!key || sayBusy || !FIELDS.includes(key)) return;
    fields[key] = node.value.slice(0, 200);
  }

  function change(node) {
    if (node.matches("[data-cross-target]") && !sayBusy) target = node.value;
    if (node.matches("[data-cross-relation]") && !sayBusy && RELATIONS.includes(node.value)) relation = node.value;
    if (node.matches("[data-heard-source]") && !hearBusy) heardSource = node.value;
    if (node.matches("[data-heard-relation]") && !hearBusy && RELATIONS.includes(node.value)) heardRelation = node.value;
  }

  return {
    card, handle, input, change, reset,
    get active() { return active; },
    get busy() { return sayBusy || hearBusy; },
    detach() { active = false; ++epoch; sayBusy = false; hearBusy = false; },
  };
}