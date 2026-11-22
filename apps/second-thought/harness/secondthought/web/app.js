// Second Thought - the demo page. Also the reference implementation of what the
// Android overlay does: render a screen read, render a reading, offer options,
// and match the guard's nudge against a draft as it is typed.
//
// The one behaviour to copy rather than reimplement: the draft is debounced,
// and when the guard says nothing the nudge disappears completely. No "all
// clear" badge, no green tick. Silence is the signal.

const $ = (id) => document.getElementById(id);
const state = {
  scenarios: [],
  scenario: null,
  reading: null,
  exclude: [],
  guardTimer: null,
  lastGuardAt: 0,
  token: new URLSearchParams(location.search).get('token') || '',
};

function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  return fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } })
    .then(async (r) => {
      const body = await r.json().catch(() => ({ ok: false, error: 'not JSON' }));
      if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
      return body;
    });
}

// ---------------------------------------------------------------- boot

async function boot() {
  health();
  setInterval(health, 20000);
  try {
    const res = await api('/scenarios');
    state.scenarios = res.scenarios;
  } catch (e) {
    $('picker').innerHTML = `<div class="err">cannot load scenarios: ${esc(e.message)}</div>`;
    return;
  }
  const picker = $('picker');
  state.scenarios.forEach((s, i) => {
    const b = document.createElement('button');
    b.textContent = s.label;
    b.title = s.blurb;
    b.onclick = () => load(i);
    picker.appendChild(b);
  });
  load(0);
  loadIntents();
}

async function health() {
  try {
    const h = await api('/health');
    const dot = $('dot');
    dot.className = 'dot ' + (h.backend === 'fixture' ? 'fixture' : h.ok ? 'live' : 'down');
    $('status-text').textContent =
      h.backend === 'fixture' ? 'demo mode · no model' : h.ok ? h.backend + ' · ' + shortModel(h.detail) : 'model down';
    $('status').title = h.detail || '';
  } catch (e) {
    $('dot').className = 'dot down';
    $('status-text').textContent = 'offline';
  }
}

function shortModel(detail) {
  const m = /serving (.+)$/.exec(detail || '');
  return m ? m[1].slice(0, 28) : 'local';
}

// ---------------------------------------------------------------- phone

function load(i) {
  state.scenario = state.scenarios[i];
  state.reading = null;
  state.exclude = [];
  document.querySelectorAll('.picker button').forEach((b, j) => b.classList.toggle('on', j === i));
  renderPhone(state.scenario.screen);
  // The demo's app field can be a display label; send the real one.
  state.scenario.screen.app = state.scenario.app_id || state.scenario.screen.app;
  $('reading-card').hidden = true;
  $('options-card').hidden = true;
  $('nudge').hidden = true;
  readNow('');
  // A scenario can arrive with a draft already in the box, which is the whole
  // point of the Slack one. Score it straight away instead of waiting for a
  // keystroke that will never come.
  if ((state.scenario.screen.draft || '').trim()) {
    setTimeout(() => runGuard(true), 350);
  }
}

function renderPhone(screen) {
  $('app-label').textContent = screen.app_label || screen.app || '';
  $('thread-label').textContent = screen.thread || '';
  const box = $('screen');
  box.innerHTML = '';
  (screen.messages || []).forEach((m) => {
    const el = document.createElement('div');
    el.className = 'bubble ' + (m.mine ? 'mine' : 'theirs');
    if (m.sender && !m.mine) {
      const who = document.createElement('span');
      who.className = 'who';
      who.textContent = m.sender;
      el.appendChild(who);
    }
    el.appendChild(document.createTextNode(m.text));
    box.appendChild(el);
  });
  if (!screen.messages || !screen.messages.length) {
    box.innerHTML = '<p class="empty">nothing readable here</p>';
  }
  box.scrollTop = box.scrollHeight;
  const draft = $('draft-display');
  draft.textContent = screen.draft || '';
  refreshSend();
}

function draftText() { return $('draft-display').textContent.trim(); }

function refreshSend() {
  $('send').disabled = !draftText();
}

// ---------------------------------------------------------------- reading

async function readNow(steer) {
  const s = state.scenario;
  if (!s) return;
  const body = {
    ...s.screen,
    app: s.screen.app,
    steer: steer || '',
    want_to_say: steer || '',
    draft: draftText(),
  };
  $('reading-card').hidden = false;
  $('situation').innerHTML = '<span class="spin">◌</span> reading the thread…';
  $('they-want').textContent = '';
  $('you-want').textContent = '';
  $('detail').hidden = true;
  $('stakes').textContent = '';
  $('stakes').className = 'stakes';
  try {
    const res = await api('/read', { method: 'POST', body: JSON.stringify(body) });
    state.reading = res;
    renderReading(res);
  } catch (e) {
    $('situation').innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

function renderReading(res) {
  const r = res.reading || {};
  document.querySelectorAll('#reading-card .err').forEach((e) => e.remove());
  $('situation').textContent = r.situation || '—';
  $('they-want').textContent = r.what_they_want || '—';
  $('you-want').textContent = r.what_you_want || '—';
  $('stakes').textContent = r.stakes || '';
  $('stakes').className = 'stakes ' + (r.stakes || '');

  const noticed = [...(r.noticed || []), ...(r.open_questions || [])];
  $('detail').hidden = !noticed.length;
  $('noticed').innerHTML = (r.noticed || []).map((n) => `<li>${esc(n)}</li>`).join('');
  $('questions').innerHTML = (r.open_questions || []).map((n) => `<li>open: ${esc(n)}</li>`).join('');

  renderOptions(res.options || []);
  renderSuggested(res.suggested_intents || []);
  if (res.error) {
    $('situation').insertAdjacentHTML('afterend',
      `<div class="err" style="margin:0 0 12px">${esc(res.error)}</div>`);
  }
}

function renderOptions(opts) {
  $('options-card').hidden = false;
  const box = $('options');
  box.innerHTML = '';
  if (!opts.length) {
    box.innerHTML = '<p class="empty-note">Nothing worth sending. That is an answer too.</p>';
    return;
  }
  opts.forEach((o) => {
    const risk = o.risk ?? 0;
    const cls = risk < 20 ? 'safe' : risk < 55 ? 'mid' : 'bad';
    const el = document.createElement('div');
    el.className = 'opt';
    el.innerHTML = `
      <div class="opt-top"><div class="opt-text">${esc(o.text)}</div></div>
      <div class="opt-meta">
        <span class="move">${esc(o.move_label || o.move)}</span>
        <span class="risk ${cls}">risk ${risk}</span>
      </div>
      <p class="why">${esc(o.why || '')}</p>
      ${(o.notes || []).length ? `<ul class="opt-notes">${o.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
      <div class="opt-actions">
        <button class="take">put it in the box</button>
        <button class="ghost">copy</button>
      </div>`;
    el.querySelector('.take').onclick = () => {
      $('draft-display').textContent = o.text;
      refreshSend();
      feedback(o, 'copied_to_box');
      runGuard(true);
    };
    el.querySelector('.ghost').onclick = (ev) => {
      copy(o.text);
      ev.target.textContent = 'copied';
      feedback(o, 'copy');
    };
    box.appendChild(el);
  });
}

function renderSuggested(items) {
  const box = $('suggested');
  box.innerHTML = '';
  items.forEach((text) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = '+ ' + text;
    chip.title = 'add this to your standing intentions';
    chip.onclick = async () => {
      await api('/intent', { method: 'POST', body: JSON.stringify({ add_goal: text }) });
      chip.textContent = 'added ✓';
      chip.style.borderStyle = 'solid';
      loadIntents();
    };
    box.appendChild(chip);
  });
}

function feedback(o, action) {
  if (!state.reading) return;
  api('/feedback', {
    method: 'POST',
    body: JSON.stringify({
      reading_id: state.reading.reading_id,
      app: state.scenario.screen.app,
      text: o.text,
      move: o.move,
      risk: o.risk,
      action,
    }),
  }).catch(() => {});
  state.exclude.push(o.text);
}

// ---------------------------------------------------------------- guard

function onDraftInput() {
  refreshSend();
  clearTimeout(state.guardTimer);
  const d = draftText();
  if (!d) { $('nudge').hidden = true; return; }
  // Matches GuardConfig.debounce_ms. Long enough not to fire on every keystroke,
  // short enough to land before the thumb reaches send.
  state.guardTimer = setTimeout(() => runGuard(false), 700);
}

async function runGuard(force) {
  const s = state.scenario;
  const draft = force ? ($('draft-display').textContent || '') : draftText();
  if (!s || !draft.trim()) { $('nudge').hidden = true; return; }
  state.lastGuardAt = Date.now();
  const stamp = state.lastGuardAt;
  try {
    const res = await api('/guard', {
      method: 'POST',
      body: JSON.stringify({ ...s.screen, app: s.screen.app, draft }),
    });
    if (stamp !== state.lastGuardAt) return; // a newer draft is already in flight
    renderNudge(res, draft);
  } catch (e) {
    $('nudge').hidden = true;
  }
}

function renderNudge(res, draft) {
  const nudge = $('nudge');
  if (!res.worth_showing) { nudge.hidden = true; return; }
  nudge.hidden = false;
  nudge.classList.toggle('hard', res.risk >= 65);
  $('nudge-title').textContent =
    res.risk >= 65 ? 'are you sure' : res.risk >= 45 ? 'hold on' : 'heads up';

  const rules = (res.findings || []).filter((f) => f.rule !== 'model').map((f) => f.detail);
  const model = (res.findings || []).filter((f) => f.rule === 'model').map((f) => f.detail);
  $('nudge-why').textContent = res.why || rules[0] || '';
  const reasons = [...new Set([...rules.slice(0, 2), ...model.slice(0, 2)])];
  $('nudge-reasons').innerHTML = reasons
    .filter((r) => r !== res.why)
    .slice(0, 3)
    .map((r) => `<li>${esc(r)}</li>`)
    .join('');

  const fix = res.fix || res.alt;
  $('nudge-fix').hidden = !fix;
  $('nudge-fix-text').textContent = fix || '';
  $('nudge-copy').onclick = () => {
    $('draft-display').textContent = fix;
    refreshSend();
    nudge.hidden = true;
    // The only learning signal that means anything: they took the rewrite.
    api('/feedback', {
      method: 'POST',
      body: JSON.stringify({ action: 'used_fix', app: state.scenario.screen.app }),
    }).catch(() => {});
  };
}

// ---------------------------------------------------------------- intents

async function loadIntents() {
  try {
    const res = await api('/intent');
    const list = $('intent-list');
    list.innerHTML = '';
    (res.intent.goals || []).forEach((g) => {
      const li = document.createElement('li');
      li.innerHTML = `${esc(g)} <button title="remove">✕</button>`;
      li.querySelector('button').onclick = async () => {
        await api('/intent', { method: 'POST', body: JSON.stringify({ remove_goal: g }) });
        loadIntents();
      };
      list.appendChild(li);
    });
    if (!(res.intent.goals || []).length) {
      list.innerHTML = '<li>No intentions set. Add one below and every suggestion will follow it.</li>';
    }
  } catch (e) { /* the panel is optional */ }
}

function addIntent() {
  const input = $('want-to-say');
  const text = input.value.trim();
  if (!text) return;
  api('/intent', { method: 'POST', body: JSON.stringify({ add_goal: text }) })
    .then(() => { input.value = ''; loadIntents(); })
    .catch(() => {});
}

// ---------------------------------------------------------------- misc

function copy(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

$('draft-display').addEventListener('input', onDraftInput);
$('draft-display').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.preventDefault(); });
$('nudge-close').onclick = () => { $('nudge').hidden = true; };
$('again').onclick = () => readNow('');
$('want-go').onclick = () => {
  const v = $('want-to-say').value.trim();
  if (v) { readNow(v); $('want-to-say').value = ''; }
};
$('want-to-say').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('want-go').click(); });
$('send').onclick = () => {
  const nudgeVisible = !$('nudge').hidden;
  if (nudgeVisible && $('nudge').classList.contains('hard')) {
    $('nudge-title').textContent = 'tap again to send anyway';
    return;
  }
  $('draft-display').textContent = '';
  refreshSend();
  $('nudge').hidden = true;
};

boot();
