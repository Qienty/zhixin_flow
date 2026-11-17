/* Independent, versioned self-help sessions. Private drafts stay in memory. */
export function createPracticeController(env) {
  const { api, t, esc, getLang } = env;
  let items = [], records = [], summary = null, cursor = null, ready = false;
  let active = null, pending = null, busy = false, linkedId = null;
  let epoch = 0, loadSeq = 0, openSeq = 0, loading = false, loadError = '';
  let error = '', conflict = false, timer = null, remaining = 0, running = false, deadline = 0;
  const key = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
  const tr = (name, vars) => t('practice.' + name, vars);
  const button = (act, label, cls = 'btn ghost', attrs = '') => `<button class="${cls}" data-act="p-${act}" ${attrs}>${label}</button>`;
  const date = value => new Date(value).toLocaleString(getLang() === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const score = value => value == null ? tr('unrated') : String(value) + '/5';
  const icon = id => {
    const paths = { grounding: '<path d="M6 20h20M10 16V8m6 8V4m6 12v-6"/>', thoughts: '<path d="M6 8h20v14H15l-6 4v-4H6zM11 13h10m-10 4h6"/>', room: '<path d="M16 26S5 19 5 12a6 6 0 0 1 11-3 6 6 0 0 1 11 3c0 7-11 14-11 14Z"/>', action: '<path d="M16 27V14m0 6C7 20 6 12 7 6c7 0 11 5 9 12m0 5c8 0 11-5 10-11-6 0-10 3-10 8"/>' };
    return `<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[id] || paths.action}</svg>`;
  };
  function pause() {
    if (running) remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    clearInterval(timer); timer = null; running = false;
    updateTimer();
  }
  function updateTimer() {
    const value = env.overlay.querySelector('[data-practice-time]');
    if (value) value.textContent = String(remaining).padStart(2, '0');
    const toggle = env.overlay.querySelector('[data-act="p-timer"]');
    if (toggle) { toggle.textContent = tr(running ? 'pause' : 'resume_timer'); toggle.disabled = busy || !!pending || remaining === 0; }
    const status = env.overlay.querySelector('[data-practice-timer-status]');
    if (status) status.textContent = tr(remaining === 0 ? 'timer_done' : running ? 'timer_running' : 'timer_paused');
  }
  function toggleTimer() {
    if (running) { pause(); return; }
    if (!remaining || document.hidden || busy || pending) return;
    deadline = Date.now() + remaining * 1000; running = true;
    timer = setInterval(() => {
      remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (!remaining || document.hidden) pause();
      else updateTimer();
    }, 250);
    updateTimer();
  }
  function dispose() { pause(); active = null; pending = null; error = ''; conflict = false; busy = false; ++openSeq; }
  function reset() { ++epoch; ++loadSeq; dispose(); items = []; records = []; summary = null; cursor = null; ready = false; loading = false; loadError = ''; linkedId = null; }
  async function load(more = false) {
    if (more && (!cursor || loading)) return;
    const ticket = ++loadSeq, owner = epoch;
    loading = true; loadError = '';
    env.render();
    try {
      const [catalog, history, stats] = await Promise.all([api.practices(), api.practiceSessions({ limit: 10, cursor: more ? cursor : null }), api.practiceSummary()]);
      if (ticket !== loadSeq || owner !== epoch) return;
      items = catalog.items; records = more ? records.concat(history.items.filter(row => !records.some(old => old.id === row.id))) : history.items;
      cursor = history.next_cursor; summary = stats; ready = true;
    } catch (err) { if (ticket === loadSeq && owner === epoch) loadError = err.message; }
    finally { if (ticket === loadSeq && owner === epoch) { loading = false; env.render(); } }
  }
  function view() {
    return `<div class="practice-page stack gap-24"><header class="practice-heading"><p class="eyebrow">${tr('eyebrow')}</p><h1>${tr('title')}</h1><p class="small">${tr('intro')}</p></header>
      ${linkedId ? `<div class="practice-linked"><span>${tr('linked', { id: linkedId })}</span>${button('unlink', tr('unlink'), 'text-link')}</div>` : ''}
      ${loadError ? `<div class="practice-error" role="alert">${esc(loadError)}${button('reload', t('common.retry'))}</div>` : ''}
      ${!ready ? `<p role="status" class="small">${tr('loading')}</p>` : `<section class="practice-library" aria-label="${tr('library')}">${items.map((p, i) => `<button class="practice-card" data-act="p-intro" data-id="${esc(p.id)}"><span class="practice-icon">${icon(p.icon)}</span><span class="practice-card-copy"><span class="practice-card-meta">0${i + 1} · ${tr('minutes', { n: p.minutes })}</span><strong>${esc(p.title)}</strong><span>${esc(p.subtitle)}</span></span><span class="practice-arrow" aria-hidden="true">↗</span></button>`).join('')}</section>
      <section class="practice-summary"><h2>${tr('recent')}</h2><div class="practice-metrics"><div><strong>${summary.completed}</strong><span>${tr('completed_count')}</span></div><div><strong>${summary.paired}</strong><span>${tr('paired_count')}</span></div></div>
      <p class="small">${summary.paired ? tr('averages', { before: summary.before_average, after: summary.after_average, n: summary.paired }) : tr('no_pairs')}</p><p class="tiny">${tr('summary_note')}</p></section>
      <section><div class="practice-section-head"><h2>${tr('history')}</h2><a class="text-link" href="${api.practiceExportUrl()}" download>${tr('export')}</a></div>
      ${records.length ? `<div class="practice-history">${records.map(row => `<button class="practice-history-row" data-act="p-open" data-id="${row.id}"><span><strong>${esc(row.practice.title)}</strong><span>${esc(date(row.started_at))} · ${tr(row.status)}</span><span>${tr('score_pair', { before: score(row.before_score), after: score(row.after_score) })}</span></span><span class="practice-history-link">${tr(row.status === 'in_progress' ? 'continue' : 'review')} →</span></button>`).join('')}</div>` : `<div class="practice-empty">${tr('empty')}</div>`}
      ${cursor ? button('more', loading ? tr('loading') : tr('more'), 'btn ghost block', loading ? 'disabled' : '') : ''}</section>`}
      <p class="tiny practice-disclaimer">${tr('scope')}</p></div>`;
  }
  function rating(field, value) {
    return `<fieldset class="practice-rating" data-rating="${field}"><legend>${tr(field === 'before_score' ? 'before' : 'after')}</legend><p class="small">${tr('scale')}</p><div class="practice-score-buttons">${[1,2,3,4,5].map(n => button('score', String(n), 'practice-score' + (value === n ? ' on' : ''), `data-field="${field}" data-value="${n}" aria-pressed="${value === n}" aria-label="${tr('score_label', { n })}"`)).join('')}</div>${button('clear-score', tr('skip_score'), 'text-link', `data-field="${field}"`)}<p class="tiny">${tr(value == null ? 'score_skipped' : 'score_selected', { n: value })}</p></fieldset>`;
  }
  function textInput(name, label, value) { return `<label class="practice-input">${esc(label)}<textarea data-practice-input="${name}" maxlength="500" rows="4" placeholder="${tr('optional')}">${esc(value || '')}</textarea><span class="tiny">${tr('text_limit')}</span></label>`; }
  function restoreControl(node) {
    Array.from(env.overlay.querySelectorAll('button')).find(item => item.dataset.act === node.dataset.act && item.dataset.field === node.dataset.field && item.dataset.value === node.dataset.value)?.focus();
  }
  function updateRating(node) {
    const field = node.dataset.field;
    const group = env.overlay.querySelector(`[data-rating="${field}"]`);
    if (group) group.outerHTML = rating(field, active[field]);
    else present({ focus: false });
    restoreControl(node);
  }
  function present({ focus = true } = {}) {
    if (!active) return;
    pause();
    const a = active, s = a.session, p = a.practice;
    let body = '', foot = '', eyebrow = tr('eyebrow');
    if (a.kind === 'loading') body = `<p role="status">${tr('loading')}</p>`;
    else if (a.kind === 'intro') {
      body = `<div class="practice-focus-icon">${icon(p.icon)}</div><p class="eyebrow">${tr('minutes', { n: p.minutes })} · ${tr('steps', { n: p.steps.length })}</p><h2 tabindex="-1" data-autofocus>${esc(p.title)}</h2><p class="practice-body-copy">${esc(p.subtitle)}</p><p class="small">${tr('pace')}</p>${rating('before_score', a.before_score)}`;
      foot = button('start', tr('start'), 'btn block');
    } else if (a.kind === 'session' && s.step_index < p.steps.length) {
      const step = p.steps[s.step_index];
      eyebrow = tr('step', { n: s.step_index + 1, total: p.steps.length });
      body = `<div class="practice-progress" aria-hidden="true">${p.steps.map((_, i) => `<span class="${i <= s.step_index ? 'on' : ''}"></span>`).join('')}</div><p class="eyebrow">${esc(p.title)}</p><h2 tabindex="-1" data-autofocus>${esc(step.title)}</h2><p class="practice-body-copy">${esc(step.body)}</p>
        ${step.seconds ? `<div class="practice-clock"><span><strong data-practice-time>${remaining}</strong><span>${tr('seconds')}</span></span>${button('timer', tr('resume_timer'), 'btn soft')}<p class="tiny" data-practice-timer-status>${tr('timer_paused')}</p></div>` : ''}
        ${step.prompt ? textInput(step.id, step.prompt, a.responses[step.id]) : ''}<p class="small">${tr('free_to_stop')}</p>`;
      foot = button('next', tr(s.step_index === p.steps.length - 1 ? 'to_feedback' : 'next'), 'btn block') + button('stop', tr('stop'), 'text-link');
    } else if (a.kind === 'session') {
      eyebrow = tr('feedback');
      body = `<h2 tabindex="-1" data-autofocus>${tr('check_in')}</h2><p class="small">${tr('feedback_note')}</p>${rating('after_score', a.after_score)}<fieldset class="practice-rating"><legend>${tr('change')}</legend><div class="practice-feedback-options">${['better','same','worse'].map(value => button('relief', tr(value), 'chip' + (a.relief === value ? ' on' : ''), `data-value="${value}" aria-pressed="${a.relief === value}"`)).join('')}</div></fieldset>
        ${a.relief === 'worse' ? `<p class="practice-gentle-note">${tr('worse_note')}</p>` : ''}${textInput('note', tr('note'), a.note)}`;
      foot = button('finish', tr('save'), 'btn block') + button('stop', tr('stop'), 'text-link');
    } else if (s) {
      eyebrow = tr(s.status);
      body = `<div class="practice-focus-icon">${icon(p.icon)}</div><h2 tabindex="-1" data-autofocus>${a.justSaved ? tr('saved') : esc(p.title)}</h2><p class="small">${a.justSaved ? esc(p.title) + ' · ' : ''}${esc(date(s.started_at))}</p><p class="practice-result-pair">${tr('score_pair', { before: score(s.before_score), after: score(s.after_score) })}</p>${s.relief ? `<p class="small">${tr('change')}：${tr(s.relief)}</p>` : ''}
        ${s.relief === 'worse' ? `<p class="practice-gentle-note">${tr('worse_note')}</p>${button('support', tr('support'), 'btn soft block')}` : ''}
        ${s.entry_id ? `<p class="tiny">${tr('linked', { id: s.entry_id })}</p>` : ''}
        ${p.steps.filter(step => s.responses[step.id]).map(step => `<div class="practice-answer"><h3>${esc(step.title)}</h3><p>${esc(s.responses[step.id])}</p></div>`).join('')}
        ${s.note ? `<div class="practice-answer"><h3>${tr('note')}</h3><p>${esc(s.note)}</p></div>` : ''}<p class="tiny">${tr('saved_note')}</p>`;
      foot = button('close', tr('back'), 'btn block') + button('delete', t('common.delete'), 'text-link');
    }
    const err = error ? `<div class="practice-error" role="alert"><p>${esc(error)}</p>${conflict ? button('reopen', tr('reopen'), 'btn soft') : pending ? button('retry', t('common.retry'), 'btn soft') : ''}${pending ? button('discard', tr('leave_page'), 'text-link') : ''}</div>` : '';
    env.open(`<div class="focus practice-focus"><div class="focus-head"><span class="eyebrow">${eyebrow}</span>${button('close', '×', 'icon-btn', `aria-label="${tr('save_exit')}"`)}</div><div class="focus-body">${body}</div>${err}<div class="focus-foot stack gap-8">${foot}${a.kind === 'session' ? button('close', tr('save_exit'), 'btn ghost block') : ''}<p class="tiny" role="status">${busy ? tr('saving') : a.kind === 'session' ? tr('checkpoint') : ''}</p></div></div>`, { focus, label: p?.title || tr('title') });
    env.overlay.querySelectorAll('button, textarea').forEach(node => { if (busy || (pending && !['p-retry','p-reopen','p-discard'].includes(node.dataset.act))) node.disabled = true; });
    updateTimer();
  }
  function accept(session, justSaved = false) {
    active = { kind: session.status === 'in_progress' ? 'session' : 'detail', session, practice: session.practice, responses: { ...session.responses }, note: session.note, after_score: session.after_score, relief: session.relief, justSaved };
    remaining = session.practice.steps[session.step_index]?.seconds || 0;
    error = ''; conflict = false;
  }
  async function execute() {
    if (busy || !pending) return;
    pause(); busy = true; error = ''; present();
    const job = pending, owner = epoch;
    try {
      const result = job.type === 'start' ? await api.startPractice(job.body) : await api.updatePractice(job.id, job.body);
      if (owner !== epoch || pending !== job) return;
      if (job.type === 'start') linkedId = null;
      pending = null; busy = false; accept(result, job.body.status === 'completed' || job.body.status === 'stopped');
      if (job.after) job.after(); else present();
      void load();
    } catch (err) {
      if (owner !== epoch || pending !== job) return;
      busy = false; conflict = err.status === 409;
      error = conflict ? tr('conflict') : tr('save_failed') + ' ' + err.message;
      present();
    }
  }
  async function save(patch = {}, after) {
    if (!active?.session || busy || pending) return;
    const a = active;
    pending = { type: 'patch', id: a.session.id, body: { request_id: key(), revision: a.session.revision, responses: { ...a.responses }, after_score: a.after_score, relief: a.relief, note: a.note, ...patch }, after };
    return execute();
  }
  function isDirty() {
    if (pending) return true;
    if (active?.kind !== 'session') return false;
    const a = active, s = a.session;
    return a.note !== s.note || a.relief !== s.relief || a.after_score !== s.after_score || JSON.stringify(a.responses) !== JSON.stringify(s.responses);
  }
  async function leave(after = env.close) {
    if (busy) return;
    if (pending) { env.toast(tr('retry_first')); return; }
    const done = () => { dispose(); after(); env.render(); };
    if (active?.kind === 'session' && isDirty()) await save({}, done);
    else done();
  }
  async function openSession(id) {
    if (busy) return;
    dispose(); active = { kind: 'loading' }; const ticket = ++openSeq, owner = epoch; present();
    try {
      const result = await api.practiceSession(id);
      if (ticket !== openSeq || owner !== epoch) return;
      accept(result); present();
    } catch (err) { if (ticket === openSeq && owner === epoch) { dispose(); env.close(); env.toast(err.message); } }
  }
  async function handle(action, node) {
    if (busy) return;
    if (pending && !['retry', 'reopen', 'discard'].includes(action)) { env.toast(tr('retry_first')); return; }
    if (action === 'reload') return load();
    if (action === 'more') return load(true);
    if (action === 'unlink') { linkedId = null; env.render(); return; }
    if (action === 'intro') {
      const p = items.find(item => item.id === node.dataset.id); if (!p) return;
      dispose(); active = { kind: 'intro', practice: p, before_score: null, client_id: key() }; present();
    } else if (action === 'open') return openSession(Number(node.dataset.id));
    else if (action === 'start' && active?.kind === 'intro') {
      pending = { type: 'start', body: { practice_id: active.practice.id, client_id: active.client_id, before_score: active.before_score, entry_id: linkedId, tz_offset: env.tzOffset() } };
      return execute();
    } else if (action === 'retry') return execute();
    else if (action === 'discard' && pending) {
      const owner = epoch, job = pending;
      if (!await env.confirm({ title: tr('leave_page'), body: tr('leave_page_note'), ok: tr('leave_page') }) || owner !== epoch || pending !== job) return;
      dispose(); env.close(); env.render(); void load();
    }
    else if (action === 'reopen') {
      const id = active?.session?.id;
      if (id && await env.confirm({ title: tr('reopen'), body: tr('reopen_note'), ok: tr('reopen') })) return openSession(id);
    } else if (action === 'close') return leave();
    else if (action === 'timer') toggleTimer();
    else if (action === 'score' && active) { active[node.dataset.field] = active[node.dataset.field] === Number(node.dataset.value) ? null : Number(node.dataset.value); updateRating(node); }
    else if (action === 'clear-score' && active) { active[node.dataset.field] = null; updateRating(node); }
    else if (action === 'relief' && active) { active.relief = active.relief === node.dataset.value ? null : node.dataset.value; present({ focus: false }); restoreControl(node); }
    else if (action === 'next' && active?.kind === 'session') return save({ step_index: active.session.step_index + 1 });
    else if (action === 'finish' && active?.kind === 'session') return save({ status: 'completed' });
    else if (action === 'stop' && active?.kind === 'session') {
      pause();
      if (await env.confirm({ title: tr('stop'), body: tr('stop_note'), ok: tr('stop') })) return save({ status: 'stopped' });
    } else if (action === 'support') return leave(env.support);
    else if (action === 'delete' && active?.session) {
      const owner = epoch, id = active.session.id;
      if (!await env.confirm({ title: t('common.delete'), body: tr('delete_note'), ok: t('common.delete') }) || owner !== epoch) return;
      busy = true; present();
      try {
        await api.deletePractice(id);
        if (owner !== epoch) return;
        dispose(); env.close(); env.toast(t('common.deleted')); await load();
      } catch (err) { if (owner === epoch) { busy = false; error = err.message; present(); } }
    }
  }
  function input(node) {
    if (!active || busy || pending) return;
    const field = node.dataset.practiceInput; if (!field) return;
    if (field === 'note') active.note = node.value;
    else if (active.practice.steps.some(step => step.id === field && step.prompt)) active.responses[field] = node.value;
  }
  return { view, load, handle, input, leave, dispose, reset, pause, isDirty,
    get active() { return !!active; }, get busy() { return busy; }, get ready() { return ready; },
    link(id) { linkedId = id; },
  };
}
