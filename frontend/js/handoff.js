/* 就诊摘要：把记录整理成一页纸，给咨询师或医生看。

   与导出 CSV 的分工：CSV 是原始行使，这里只给结论性事实——频率、时段、强度、
   触发场景、练习前后变化、几条原话——并按用户选择的窗口（14/30/90 天）生成。

   全部由服务端规则计算，不调用模型；页面上的「想问的三件事」只进打印与下载，
   不写数据库。 */
const HANDOFF_WINDOWS = [14, 30, 90];

export function createHandoffController({ api, t, esc, overlay, open, close, toast }) {
  let data = null;
  let busy = false;
  let days = 30;
  let pendingDays = null;
  let questions = ["", "", ""];
  let error = "";
  let active = false;
  let epoch = 0;

  const pad = (n) => String(n).padStart(2, "0");
  const today = () => {
    const now = new Date();
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  };

  function line(label, value) {
    return `<div class="handoff-row"><span class="handoff-label">${esc(label)}</span><span class="handoff-value">${esc(value)}</span></div>`;
  }

  function label(prefix, key) {
    return t(`handoff.${prefix}_${key}`);
  }

  function body() {
    if (!data) return "";
    const e = data.entries || {};
    const p = data.practices || {};
    const w = data.week || {};
    const tops = (e.top_causes || []).slice(0, 4).map((c) => `${c.label} ${t("handoff.times", { n: c.count })}`);
    const dist = (e.emotion_distribution || []).slice(0, 4).map((d) => `${d.label} ${d.count}`);
    const hours = (data.hours || []).map((h) => `${h.label} ${t("handoff.times", { n: h.count })}`);
    const highlights = (data.highlights || []).map(
      (h) => `${h.date} · ${h.emotion} · ${t("handoff.strength", { n: h.severity })}${h.cause ? ` · ${h.cause}` : ""}`,
    );
    return `
      <p class="tiny handoff-range">${esc(t("handoff.range", { since: data.since, until: data.until, days: data.days }))}</p>

      <h3 class="handoff-h">${t("handoff.sec_overview")}</h3>
      ${line(t("handoff.entries"), t("handoff.entries_value", { n: e.entries || 0, days: e.days_logged || 0 }))}
      ${line(t("handoff.severity"), t("handoff.severity_value", { avg: e.avg_severity ?? "—", severe: e.severe_count || 0 }))}
      ${dist.length ? line(t("handoff.emotions"), dist.join(" · ")) : ""}

      ${hours.length ? `<h3 class="handoff-h">${t("handoff.sec_hours")}</h3>${hours.map((h) => `<p class="handoff-line">${esc(h)}</p>`).join("")}` : ""}

      ${tops.length ? `<h3 class="handoff-h">${t("handoff.sec_triggers")}</h3>${tops.map((c) => `<p class="handoff-line">${esc(c)}</p>`).join("")}` : ""}

      <h3 class="handoff-h">${t("handoff.sec_week")}</h3>
      <p class="handoff-line">${esc(t("handoff.week_value", { n: w.entries || 0, severe: w.severe || 0, prev: w.previous_severe || 0 }))}</p>

      <h3 class="handoff-h">${t("handoff.sec_practice")}</h3>
      <p class="handoff-line">${esc(p.paired ? t("handoff.practice_value", { n: p.paired, before: p.before_average, after: p.after_average }) : t("handoff.practice_none", { n: p.completed || 0 }))}</p>

      ${highlights.length ? `<h3 class="handoff-h">${t("handoff.sec_high")}</h3>${highlights.map((h) => `<p class="handoff-line">${esc(h)}</p>`).join("")}` : ""}

      <h3 class="handoff-h">${t("handoff.sec_questions")}</h3>
      <p class="tiny">${t("handoff.questions_hint")}</p>
      <div class="stack gap-8 handoff-questions">
        ${questions.map((value, index) => `<label class="sr-only" for="handoff-q${index}">${t("handoff.question", { n: index + 1 })}</label>
          <input type="text" id="handoff-q${index}" data-handoff-q="${index}" maxlength="80" value="${esc(value)}" placeholder="${t("handoff.question", { n: index + 1 })}" ${busy ? "readonly" : ""} />`).join("")}
      </div>

      <p class="tiny handoff-note">${esc(data.notes || "")}</p>
    `;
  }

  function windowChips() {
    const current = pendingDays ?? days;
    return `<div class="chip-row handoff-actions">
      ${HANDOFF_WINDOWS.map((value) => `<button class="chip ${current === value ? "on" : ""}" data-act="h-window" data-days="${value}" ${busy ? "disabled" : ""}>${t("handoff.window", { n: value })}</button>`).join("")}
    </div>`;
  }

  function paint(focus = false) {
    active = true;
    open(
      `<div class="sheet center handoff-sheet stack gap-16">
        <div class="row-between">
          <div class="stack gap-4">
            <span class="eyebrow">${t("handoff.eyebrow")}</span>
            <h2 class="section-title">${t("handoff.title")}</h2>
          </div>
          <button class="icon-btn no-print" data-act="h-close" aria-label="${t("common.close")}">×</button>
        </div>
        ${windowChips()}
        ${busy && !data ? `<p class="small" role="status">${t("handoff.loading")}</p>` : ""}
        ${error ? `<p class="expression-error small" role="alert">${esc(error)}</p>` : ""}
        <div class="handoff-body stack gap-12">${body()}</div>
        ${data
          ? `<div class="stack gap-8 no-print">
              <button class="btn block" data-act="h-print" ${busy ? "disabled" : ""}>${t("handoff.print")}</button>
              <button class="btn soft block" data-act="h-download" ${busy ? "disabled" : ""}>${t("handoff.download")}</button>
            </div>`
          : ""}
      </div>`,
      { focus, label: t("handoff.title") },
    );
  }

  async function load(nextDays = days) {
    const requested = nextDays;
    const owner = epoch;
    busy = true;
    pendingDays = requested;
    error = "";
    paint();
    try {
      const result = await api.handoff(requested);
      if (owner !== epoch) return;
      data = result;
      days = requested; // 只有成功才把窗口定下来，失败时正文与高亮仍属于旧窗口
    } catch (err) {
      if (owner !== epoch) return;
      error = err.message;
    } finally {
      if (owner === epoch) {
        pendingDays = null;
        busy = false;
        if (active) paint();
      }
    }
  }

  function asText() {
    if (!data) return "";
    const e = data.entries || {};
    const p = data.practices || {};
    const w = data.week || {};
    const lines = [
      t("handoff.title"),
      t("handoff.range", { since: data.since, until: data.until, days: data.days }),
      "",
      `[${t("handoff.sec_overview")}]`,
      t("handoff.entries_value", { n: e.entries || 0, days: e.days_logged || 0 }),
      t("handoff.severity_value", { avg: e.avg_severity ?? "—", severe: e.severe_count || 0 }),
      ...(e.emotion_distribution || []).slice(0, 4).map((d) => `${d.label} ${d.count}`),
      "",
      `[${t("handoff.sec_hours")}]`,
      ...(data.hours || []).map((h) => `${h.label} ${t("handoff.times", { n: h.count })}`),
      "",
      `[${t("handoff.sec_triggers")}]`,
      ...(e.top_causes || []).slice(0, 4).map((c) => `${c.label} ${t("handoff.times", { n: c.count })}`),
      "",
      `[${t("handoff.sec_week")}]`,
      t("handoff.week_value", { n: w.entries || 0, severe: w.severe || 0, prev: w.previous_severe || 0 }),
      "",
      `[${t("handoff.sec_practice")}]`,
      p.paired
        ? t("handoff.practice_value", { n: p.paired, before: p.before_average, after: p.after_average })
        : t("handoff.practice_none", { n: p.completed || 0 }),
      "",
      `[${t("handoff.sec_high")}]`,
      ...(data.highlights || []).map(
        (h) => `${h.date} · ${h.emotion} · ${t("handoff.strength", { n: h.severity })}${h.cause ? ` · ${h.cause}` : ""}`,
      ),
      "",
      `[${t("handoff.sec_questions")}]`,
      ...questions.filter((q) => q.trim()).map((q, i) => `${i + 1}. ${q.trim()}`),
      "",
      data.notes || "",
    ];
    return lines.join("\n");
  }

  async function handle(action, node) {
    if (action === "open") {
      if (busy) return;
      questions = ["", "", ""];
      return load(days);
    }
    if (action === "window") {
      const next = Number(node.dataset.days);
      if (HANDOFF_WINDOWS.includes(next) && next !== days && !busy) return load(next);
      return;
    }
    if (action === "close") {
      ++epoch;
      active = false;
      data = null;
      return close();
    }
    if (action === "print") {
      window.print();
      return;
    }
    if (action === "download") {
      const text = asText();
      if (!text) return;
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `智心元流-就诊摘要-${today()}.txt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(t("handoff.downloaded"));
    }
  }

  function input(node) {
    const index = Number(node.dataset?.handoffQ);
    if (!Number.isInteger(index) || index < 0 || index > 2) return;
    questions[index] = node.value.slice(0, 80);
  }

  return {
    handle,
    input,
    reset() {
      ++epoch;
      active = false;
      data = null;
      busy = false;
      pendingDays = null;
      error = "";
    },
    get active() {
      return active;
    },
  };
}
