const $ = s => document.querySelector(s);
const api = async (p, o) => {
  const r = await fetch(p, o && {method: o.m || 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(o.b)});
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const CAMERAS = ['push_in', 'pull_out', 'pan_left', 'pan_right', 'static'];
const CPS = 4.5;
let cur = null, curSlug = null, jobs = {};

/* ---------- 系統列 ---------- */
async function health() {
  const h = await api('/api/health', {m: 'GET'});
  $('#health').innerHTML = [
    ['Ollama', h.ollama], ['Draw Things', h.drawthings], ['ffmpeg', h.ffmpeg]
  ].map(([n, v]) => `<span class="pill ${v.ok ? 'ok' : 'bad'}" title="${
    v.ok ? '正常' : (v.hint || v.error || '')}">${v.ok ? '●' : '○'} ${n}</span>`).join('');
}
function paintSys(s, loaded) {
  const pct = s.percent;
  $('#memFill').style.width = pct + '%';
  $('#memFill').style.background = pct > 88 ? 'var(--bad)' : pct > 72 ? 'var(--warn)' : 'var(--ok)';
  $('#memTxt').textContent = `${s.used_gb} / ${s.total_gb} GB` + (s.swap_gb > 0.1 ? ` · swap ${s.swap_gb}` : '');
  $('#loaded').innerHTML = (loaded || []).map(m =>
    `<span class="chip"><b>${m.name}</b> ${m.size_gb}G<span data-unload="${m.name}" title="踢出記憶體">×</span></span>`).join('');
}
document.addEventListener('click', async e => {
  const m = e.target.dataset.unload;
  if (m) { e.target.textContent = '…'; await api('/api/unload', {b: {model: m}}); }
});

/* ---------- 下拉 ---------- */
async function loadSelects() {
  const ms = await api('/api/models', {m: 'GET'}).catch(() => []);
  $('#model').innerHTML = ms.map(m =>
    `<option value="${m.name}">${m.name} · ${m.params || m.size_gb + 'G'}</option>`).join('')
    || '<option value="">（沒有模型，先 ollama pull）</option>';
  const cs = await api('/api/characters', {m: 'GET'}).catch(() => []);
  $('#character').innerHTML = cs.map(c =>
    `<option value="${c.id}">${c.name}</option>`).join('') || '<option value="">（無角色）</option>';
}

/* ---------- 專案 ---------- */
async function loadProjects() {
  const ps = await api('/api/projects', {m: 'GET'}).catch(() => []);
  $('#projects').innerHTML = ps.map(p => `<div class="item ${p.slug === curSlug ? 'on' : ''}" data-slug="${p.slug}">
    <div class="t">${p.title}</div>
    <div class="m">${p.slug} · ${p.shots} shots · ${p.duration}s · 圖 ${p.images}</div></div>`).join('')
    || '<div class="hint">還沒有專案</div>';
  $('#projects').querySelectorAll('.item').forEach(el =>
    el.onclick = () => openProject(el.dataset.slug));
}

async function openProject(slug) {
  cur = await api('/api/projects/' + slug, {m: 'GET'});
  curSlug = slug;
  $('#empty').hidden = true; $('#editor').hidden = false;
  $('#edTitle').textContent = cur.script.title || slug;
  $('#critique').textContent = cur.critique || '（無）';
  renderEndings(cur.endings);
  renderShots();
  loadProjects();
}

function renderShots() {
  const s = cur.script, cons = Object.fromEntries((cur.consistency || []).map(c => [c.id, c]));
  $('#edMeta').textContent = `${curSlug} · ${s.shots.length} shots · ${cur.total} 秒`
    + (s.hook ? ` · Hook：${s.hook}` : '');
  $('#shots').innerHTML = s.shots.map((sh, i) => {
    const cap = Math.floor((sh.duration_sec || 0) * CPS), n = (sh.narration || '').length;
    const miss = cons[sh.id] && !cons[sh.id].ok ? cons[sh.id].missing : [];
    return `<div class="shot" data-i="${i}">
      <div class="shot-head">
        <div class="shot-no">${sh.id}</div>
        <input type="number" data-f="duration_sec" value="${sh.duration_sec}" min="1" max="10" step="1">
        <span class="hint">秒</span>
        <select data-f="camera">${CAMERAS.map(c =>
          `<option ${c === sh.camera ? 'selected' : ''}>${c}</option>`).join('')}</select>
        <div class="grow"></div>
        <div class="cnt ${n > cap ? 'over' : ''}">旁白 ${n}/${cap} 字</div>
      </div>
      <label>旁白（主角心聲）</label>
      <input data-f="narration" value="${esc(sh.narration)}">
      <label>字幕</label>
      <input data-f="subtitle" value="${esc(sh.subtitle)}">
      <label>畫面動作</label>
      <input data-f="action" value="${esc(sh.action)}">
      <label>image_prompt <span class="hint">送給圖片模型</span></label>
      <textarea data-f="image_prompt" rows="3">${esc(sh.image_prompt)}</textarea>
      ${miss.length ? `<div class="miss">⚠ 角色描述缺：${miss.join(' / ')}</div>` : ''}
    </div>`;
  }).join('');
  $('#shots').querySelectorAll('.shot').forEach(el => {
    el.querySelectorAll('[data-f]').forEach(inp => inp.oninput = () => {
      const sh = cur.script.shots[+el.dataset.i], f = inp.dataset.f;
      sh[f] = inp.type === 'number' ? +inp.value : inp.value;
      if (f === 'narration' || f === 'duration_sec') {
        const cap = Math.floor(sh.duration_sec * CPS), n = (sh.narration || '').length;
        const c = el.querySelector('.cnt');
        c.textContent = `旁白 ${n}/${cap} 字`; c.classList.toggle('over', n > cap);
      }
    });
  });
  renderIssues(cur.issues);
}
const esc = t => (t || '').replace(/"/g, '&quot;').replace(/</g, '&lt;');

function renderIssues(is) {
  const el = $('#issues');
  el.hidden = !is || !is.length;
  if (is && is.length) el.innerHTML = `<b>硬檢查未過（${is.length}）</b><ul>${
    is.map(i => `<li>${i}</li>`).join('')}</ul>`;
}

function renderEndings(d) {
  const el = $('#endings');
  el.hidden = !d || !d.endings;
  if (!d || !d.endings) return;
  el.innerHTML = d.endings.map(e => `<div class="ending">
    <h4>版本 ${e.label}</h4><div class="why">${e.why || ''}</div>
    <div class="nar">「${e.shot?.narration || ''}」</div>
    <button data-ap="${e.label}">採用這個結尾</button></div>`).join('');
  el.querySelectorAll('[data-ap]').forEach(b => b.onclick = async () => {
    b.textContent = '套用中…';
    const r = await api('/api/apply_ending', {b: {slug: curSlug, label: b.dataset.ap}});
    cur.script = r.script; cur.total = r.total; cur.issues = r.issues;
    renderShots();
  });
}

/* ---------- 動作 ---------- */
$('#genBtn').onclick = async () => {
  const idea = $('#idea').value.trim();
  if (!idea) return $('#idea').focus();
  const b = {idea, slug: $('#slug').value.trim() || idea,
    character: $('#character').value, model: $('#model').value,
    think: $('#think').checked, n_shots: +$('#nShots').value,
    duration: +$('#duration').value, temperature: +$('#temp').value};
  $('#genBtn').disabled = true;
  try { const r = await api('/api/script', {b}); curSlug = r.slug; }
  catch (e) { alert('失敗：' + e.message); }
  $('#genBtn').disabled = false;
};
$('#saveBtn').onclick = async () => {
  const r = await api('/api/projects/' + curSlug, {m: 'PUT', b: {script: cur.script}});
  cur.total = r.total; cur.issues = r.issues;
  renderIssues(r.issues);
  $('#saveBtn').textContent = '已儲存'; setTimeout(() => $('#saveBtn').textContent = '儲存', 1200);
};
$('#endingsBtn').onclick = async () => {
  $('#endingsBtn').disabled = true;
  try { await api('/api/endings', {b: {slug: curSlug, model: $('#model').value}}); }
  finally { $('#endingsBtn').disabled = false; }
};
$('#refreshBtn').onclick = loadProjects;
$('#autoUnload').onchange = e => api('/api/autounload', {b: {on: e.target.checked}});
$('#temp').oninput = e => $('#tempVal').textContent = e.target.value;
$('#duration').oninput = e => {
  const d = +e.target.value, n = Math.max(3, Math.min(12, Math.round(d / 3.2)));
  $('#nShots').value = n;
  $('#shotHint').textContent = `建議 ${n}（每格 ${(d / n).toFixed(1)}s）`;
  $('#genBtn').textContent = d <= 18 ? '生成腳本（三拍）' : d <= 35 ? '生成腳本（四拍）' : '生成腳本（五拍）';
};
$('#duration').dispatchEvent(new Event('input'));

/* ---------- 佇列 / SSE ---------- */
function paintJobs() {
  const list = Object.values(jobs).sort((a, b) => b.id.localeCompare(a.id, undefined, {numeric: true})).slice(0, 12);
  $('#jobs').innerHTML = list.map(j => {
    const last = j.steps[j.steps.length - 1];
    return `<div class="job ${j.status}">
      <div class="t">${j.label}</div>
      <div class="s">${j.status} · ${j.elapsed}s${last ? ' · ' + last.text : ''}${j.error ? ' · ' + j.error : ''}</div>
      ${j.status === 'running' ? `<div class="bar"><div style="width:${last?.pct || 5}%"></div></div>` : ''}
    </div>`;
  }).join('') || '<div class="hint">閒置</div>';
}

function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = m => {
    const d = JSON.parse(m.data);
    if (d.type === 'sys') paintSys(d.sys, d.loaded);
    if (d.type === 'notice') {
      const el = $('#notice'); el.hidden = false; el.textContent = d.text;
      clearTimeout(el._t); el._t = setTimeout(() => el.hidden = true, 6000);
    }
    if (d.type === 'job') {
      const was = jobs[d.job.id]?.status;
      jobs[d.job.id] = d.job; paintJobs();
      if (d.job.status === 'done' && was !== 'done') {
        loadProjects();
        const s = d.job.result?.slug || curSlug;
        if (d.job.kind === 'script' && s) openProject(s);
        if (d.job.kind === 'endings' && curSlug) openProject(curSlug);
      }
    }
  };
  es.onerror = () => { es.close(); setTimeout(connect, 2500); };
}

(async function init() {
  await health(); await loadSelects(); await loadProjects();
  const j = await api('/api/jobs', {m: 'GET'}); j.jobs.forEach(x => jobs[x.id] = x); paintJobs();
  connect(); setInterval(health, 15000);
})();
