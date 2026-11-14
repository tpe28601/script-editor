/* Script Editor 前端：區塊庫 / 任務清單 / 內容面板 三欄編輯器。
   資料模型與 se_core.py 相同（block / project / step），全部改動作即時
   反映到 state.project，再送 /api/validate、/api/project/save、/api/export。 */
'use strict';

// ---------------------------------------------------------------- api helpers
function getJson(url) {
  return fetch(url).then(function (r) { return r.json(); });
}
function postJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Toolbox-Request': '1' },
    body: JSON.stringify(body || {})
  }).then(function (r) { return r.json(); });
}
function unwrap(res) {
  if (!res || res.ok !== true) {
    var msg = (res && res.error && (res.error.message || res.error.code)) || '服務無回應';
    throw new Error(msg);
  }
  return res.result;
}

var $ = function (id) { return document.getElementById(id); };

// ---------------------------------------------------------------- state
var state = {
  presets: [],        // 內建區塊
  custom: [],         // 自訂區塊
  errors: [],         // 區塊庫載入錯誤
  project: null,      // 編輯中的專案
  sel: { kind: 'project' },   // 'project' | {kind:'task',ti} | {kind:'step',ti,si}
  savedName: '',      // 上次儲存/開啟的專案名（dirty 判斷）
  savedJson: '',      // 上次儲存的 JSON（dirty 判斷）
  lastErrors: []
};

function defaultProject() {
  return {
    name: 'MyTool', title: 'My Tool', requireAdmin: true, runAll: true,
    bundleRoot: 'auto', logFile: '', tasks: []
  };
}
function blockById(id) {
  var all = state.presets.concat(state.custom);
  for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
  return null;
}
function dirty() {
  return state.savedJson !== '' && JSON.stringify(state.project) !== state.savedJson;
}
function markSaved() {
  state.savedName = state.project.name;
  state.savedJson = JSON.stringify(state.project);
  renderBadge();
}

// ---------------------------------------------------------------- badge / toast
function renderBadge() {
  var b = $('state-badge');
  if (!state.savedName) { b.textContent = '新專案（未儲存）'; b.className = 'tb-badge'; }
  else if (dirty()) { b.textContent = '已修改：' + state.savedName; b.className = 'tb-badge tb-badge--warn'; }
  else { b.textContent = '已儲存：' + state.savedName; b.className = 'tb-badge tb-badge--ok'; }
}
var toastTimer = 0;
function toast(msg, ok) {
  var t = $('toast');
  t.textContent = msg;
  t.className = 'tb-toast' + (ok === false ? ' tb-toast--danger' : ok ? ' tb-toast--ok' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.classList.add('hidden'); }, 4000);
}

// ---------------------------------------------------------------- 區塊庫
function renderLib() {
  var q = $('lib-search').value.trim().toLowerCase();
  var groups = {};   // category -> [block]
  state.presets.concat(state.custom).forEach(function (b) {
    if (q && (b.name + ' ' + b.id + ' ' + (b.desc || '') + ' ' + (b.category || '')).toLowerCase().indexOf(q) < 0) return;
    var c = b.category || '一般';
    (groups[c] = groups[c] || []).push(b);
  });
  var html = '';
  var closed = libCollapsed();
  Object.keys(groups).sort().forEach(function (cat) {
    // 搜尋中一律展開有結果的分類; 平常依記住的收合狀態
    var open = !!q || closed.indexOf(cat) < 0;
    html += '<button type="button" class="se-libcat" data-cat="' + esc(cat) + '" aria-expanded="' + open + '">' +
      '<span class="se-libcat__caret" aria-hidden="true">▾</span>' + esc(cat) +
      '<span class="se-libcat__count">' + groups[cat].length + '</span></button>';
    if (!open) return;
    groups[cat].forEach(function (b) {
      html += '<div class="se-libitem" draggable="true" data-bid="' + esc(b.id) + '" title="' + esc(b.desc || b.id) + '">' +
        '<span class="se-libitem__name">' + esc(b.name) + '</span>' +
        (b._src === 'custom' ? '<span class="se-tag se-tag--custom">自訂</span>' : '') +
        (b.needsBundle ? '<span class="se-tag">bundle</span>' : '') +
        (b.raw ? '<span class="se-tag">raw</span>' : '') +
        '</div>';
    });
  });
  $('lib-list').innerHTML = html || '<div class="tb-empty">沒有符合的區塊</div>';
  Array.prototype.forEach.call($('lib-list').querySelectorAll('.se-libcat'), function (btn) {
    btn.addEventListener('click', function () {
      if ($('lib-search').value.trim()) return;      // 搜尋中固定展開
      var list = libCollapsed(), cat = btn.dataset.cat, i = list.indexOf(cat);
      if (i >= 0) list.splice(i, 1); else list.push(cat);
      try { localStorage.setItem('se.libCollapsed', JSON.stringify(list)); } catch (e) { /* 只是記憶, 失敗不影響 */ }
      state.libCollapsedMem = list;
      renderLib();
    });
  });
  Array.prototype.forEach.call($('lib-list').querySelectorAll('.se-libitem'), function (el) {
    el.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/se-block', el.dataset.bid);
      ev.dataTransfer.effectAllowed = 'copy';
    });
    el.addEventListener('click', function () {
      var ti = state.sel.kind === 'task' ? state.sel.ti
             : state.sel.kind === 'step' ? state.sel.ti : firstTaskIdx();
      if (ti < 0) { toast('請先新增並選取一個任務', false); return; }
      addStep(ti, el.dataset.bid);
    });
    // 雙擊自訂區塊 → 開編輯窗（Update/Delete 入口）
    el.addEventListener('dblclick', function () {
      var blk = blockById(el.dataset.bid);
      if (blk && blk._src === 'custom') openBlockEditor(el.dataset.bid);
    });
  });
}
// 區塊庫收合的分類（localStorage 不可用時退回記憶體）
function libCollapsed() {
  try {
    var v = JSON.parse(localStorage.getItem('se.libCollapsed') || '[]');
    if (Array.isArray(v)) return v;
  } catch (e) { /* 私密模式或封鎖儲存 */ }
  return (state.libCollapsedMem || []).slice();
}
// 與 se_core.task_keys 相同: 有 key（字母）用它, 其餘依序編號 1, 2, 3…
function taskKeys(tasks) {
  var n = 0;
  return tasks.map(function (t) {
    var k = String(t.key || '').trim().toUpperCase();
    if (k) return k;
    n += 1;
    return String(n);
  });
}
function firstTaskIdx() {
  var ts = state.project.tasks;
  return ts.length ? 0 : -1;
}

// ---------------------------------------------------------------- 任務清單
function renderTasks() {
  var host = $('task-list');
  var tasks = state.project.tasks;
  if (!tasks.length) {
    host.innerHTML = '<div class="tb-empty">還沒有任務：按「＋新增任務」，再把左側區塊拖進來。<br>' +
      '每個任務 = 匯出腳本主選單的一項。</div>';
    return;
  }
  var html = '';
  var keys = taskKeys(tasks), group = '';
  tasks.forEach(function (t, ti) {
    var sel = state.sel.kind === 'task' && state.sel.ti === ti;
    // 所有任務一律展開: 步驟永遠可見, 每個任務都能直接拖放
    if (t.group && t.group !== group) html += '<div class="se-group">' + esc(t.group) + '</div>';
    if (t.group) group = t.group;
    html += '<div class="se-task' + (sel ? ' se-task--sel' : '') + '" data-ti="' + ti + '">' +
      '<div class="se-task__head">' +
      '<span class="se-task__num" title="選單按鍵">' + esc(keys[ti]) + '</span>' +
      '<span class="se-task__name">' + esc(t.name || '(未命名)') + '</span>' +
      (t.desc ? '<span class="se-task__desc">' + esc(t.desc) + '</span>' : '') +
      '<span class="tb-spacer"></span>' +
      '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-act="up" title="上移">↑</button>' +
      '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-act="down" title="下移">↓</button>' +
      '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-act="del" title="刪除任務">✕</button>' +
      '</div>';
    html += '<div class="se-steps" data-ti="' + ti + '">';
    (t.steps || []).forEach(function (s, si) {
      var b = blockById(s.block);
      var ssel = state.sel.kind === 'step' && state.sel.ti === ti && state.sel.si === si;
      var flags = [];
      if (s.onFail && s.onFail !== 'stop') flags.push(s.onFail);
      if (s.loop) flags.push('loop×' + (s.loop.mode === 'times' ? s.loop.times : s.loop.minutes + 'm'));
      if (s.confirm) flags.push('confirm');
      if (s.askAfter) flags.push('ask after');
      html += '<div class="se-step' + (ssel ? ' se-step--sel' : '') + '" draggable="true" data-ti="' + ti + '" data-si="' + si + '">' +
        '<span class="se-step__idx">' + (si + 1) + '</span>' +
        '<span class="se-step__name">' + esc(b ? b.name : '?' + s.block) + '</span>' +
        (flags.length ? '<span class="se-step__flags">' + esc(flags.join(' · ')) + '</span>' : '') +
        '<button class="tb-btn tb-btn--sm tb-btn--ghost se-step__del" data-sact="del" title="移除步驟">✕</button>' +
        '</div>';
    });
    html += '</div></div>';
  });
  host.innerHTML = html;

  Array.prototype.forEach.call(host.querySelectorAll('.se-task'), function (el) {
    var ti = +el.dataset.ti;
    el.querySelector('.se-task__head').addEventListener('click', function (ev) {
      if (ev.target.closest('button')) return;
      select({ kind: 'task', ti: ti });
    });
    // 整張任務卡都是放置目標（放到最後, 例如放在標題列上）;
    // 步驟區與各步驟另有自己的放置點, 會先處理並 stopPropagation
    bindDrop(el, ti, null);
    Array.prototype.forEach.call(el.querySelectorAll('button[data-act]'), function (btn) {
      btn.addEventListener('click', function () {
        if (btn.dataset.act === 'up' && ti > 0) { move(tasks, ti, ti - 1); followMove(ti, ti - 1); }
        else if (btn.dataset.act === 'down' && ti < tasks.length - 1) { move(tasks, ti, ti + 1); followMove(ti, ti + 1); }
        else if (btn.dataset.act === 'del') {
          if (!confirm('刪除任務「' + (tasks[ti].name || '') + '」？')) return;
          tasks.splice(ti, 1);
          if (state.sel.ti === ti) state.sel = { kind: 'project' };
          else if (state.sel.ti > ti) state.sel.ti--;
        }
        renderAll();
      });
    });
  });
  Array.prototype.forEach.call(host.querySelectorAll('.se-step'), function (el) {
    el.addEventListener('click', function (ev) {
      if (ev.target.closest('button')) return;
      select({ kind: 'step', ti: +el.dataset.ti, si: +el.dataset.si });
    });
    el.querySelector('button[data-sact="del"]').addEventListener('click', function (ev) {
      ev.stopPropagation();
      var ti = +el.dataset.ti, si = +el.dataset.si;
      tasks[ti].steps.splice(si, 1);
      if (state.sel.kind === 'step' && state.sel.ti === ti) state.sel = { kind: 'task', ti: ti };
      renderAll();
    });
    el.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/se-step', el.dataset.ti + ':' + el.dataset.si);
      ev.dataTransfer.effectAllowed = 'move';
      ev.stopPropagation();
    });
  });
  // 放置目標：整個步驟區 = 附加；每個 step = 插入其前
  Array.prototype.forEach.call(host.querySelectorAll('.se-steps'), function (zone) {
    bindDrop(zone, +zone.dataset.ti, null);
    Array.prototype.forEach.call(zone.querySelectorAll('.se-step'), function (el) {
      bindDrop(el, +el.dataset.ti, +el.dataset.si);
    });
  });
}
function bindDrop(el, ti, beforeSi) {
  el.addEventListener('dragover', function (ev) {
    var d = ev.dataTransfer.types;
    if (d.indexOf('text/se-block') >= 0 || d.indexOf('text/se-step') >= 0) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = d.indexOf('text/se-block') >= 0 ? 'copy' : 'move';
      el.classList.add('se-drop');
    }
  });
  el.addEventListener('dragleave', function () { el.classList.remove('se-drop'); });
  el.addEventListener('drop', function (ev) {
    el.classList.remove('se-drop');
    ev.preventDefault();
    ev.stopPropagation();
    var bid = ev.dataTransfer.getData('text/se-block');
    if (bid) { addStep(ti, bid, beforeSi); return; }
    var mv = ev.dataTransfer.getData('text/se-step');
    if (!mv) return;
    var from = mv.split(':').map(Number);
    moveStep(from[0], from[1], ti, beforeSi);
  });
}
function addStep(ti, bid, beforeSi) {
  var steps = state.project.tasks[ti].steps;
  var idx = (beforeSi == null) ? steps.length : beforeSi;
  steps.splice(idx, 0, { block: bid });
  state.sel = { kind: 'step', ti: ti, si: idx };
  renderAll();
}
function move(arr, from, to) { var t = arr[from]; arr[from] = arr[to]; arr[to] = t; }
// 任務 from 與 to 對調後, 讓選取（任務或其中的步驟）跟著原本的任務走
function followMove(from, to) {
  if (state.sel.kind !== 'task' && state.sel.kind !== 'step') return;
  if (state.sel.ti === from) state.sel.ti = to;
  else if (state.sel.ti === to) state.sel.ti = from;
}
function moveStep(fti, fsi, tti, beforeSi) {
  var steps = state.project.tasks[tti].steps;
  if (fti === tti && beforeSi != null && (beforeSi === fsi || beforeSi === fsi + 1)) return;
  var s = state.project.tasks[fti].steps.splice(fsi, 1)[0];
  var idx = (beforeSi == null) ? steps.length : (fti === tti && beforeSi > fsi ? beforeSi - 1 : beforeSi);
  steps.splice(idx, 0, s);
  state.sel = { kind: 'step', ti: tti, si: idx };
  renderAll();
}

// ---------------------------------------------------------------- 內容面板
function select(sel) {
  state.sel = sel;
  renderTasks();
  renderInspector();
}
function renderInspector() {
  if (state.sel.kind === 'task') renderTaskPanel(state.sel.ti);
  else if (state.sel.kind === 'step') renderStepPanel(state.sel.ti, state.sel.si);
  else renderProjectPanel();
}

function renderProjectPanel() {
  $('insp-title').textContent = '專案設定';
  var p = state.project;
  $('insp-body').innerHTML =
    field('視窗標題（ASCII，會進 .bat 的 title 與選單）',
      '<input class="tb-input" id="ip-title" value="' + esc(p.title) + '">') +
    field('選項（Task 0 = ' + (p.runAll ? '有' : '無') + ' Run all）', '') +
    check('ip-admin', '需要系統管理員權限（自動請求提昇）', p.requireAdmin) +
    check('ip-runall', '主選單加 [A] Run all tasks', p.runAll) +
    field('工具包根目錄（auto = 先找桌面、再找腳本所在資料夾，取名稱日期最新的）',
      '<input class="tb-input tb-input--mono" id="ip-bundleroot" value="' + esc(p.bundleRoot || 'auto') + '">') +
    '<div class="se-fgrid">' +
    field('工具包資料夾名稱（可用 *）',
      '<input class="tb-input tb-input--mono" id="ip-bfolder" value="' + esc(p.bundleFolder || '') + '" placeholder="ToolBundle_*">') +
    field('工具所在子資料夾',
      '<input class="tb-input tb-input--mono" id="ip-bsub" value="' + esc(p.bundleSub != null ? p.bundleSub : 'win64') + '" placeholder="（空白 = 資料夾本身）">') +
    field('檢查檔（選填，找到它才算有效）',
      '<input class="tb-input tb-input--mono" id="ip-bcheck" value="' + esc(p.bundleCheck || '') + '" placeholder="Tool64W.exe">') +
    '</div>' +
    field('Log 檔路徑（空白 = 腳本同目錄 .log）',
      '<input class="tb-input tb-input--mono" id="ip-logfile" value="' + esc(p.logFile || '') + '" placeholder="tool.log">') +
    check('ip-summary', '任務結束時列出每一步的結果（OK／FAILED／SKIPPED）', p.summary !== false) +
    statusEditorHtml(p) +
    '<div class="se-subhead">驗證錯誤</div><div id="ip-errors" class="se-errlist">' +
    (state.lastErrors.length ? state.lastErrors.map(function (e) { return '<div>' + esc(e) + '</div>'; }).join('') : '<div class="tb-muted">尚未驗證</div>') +
    '</div>';
  bind('ip-title', 'input', function (v) { state.project.title = v; });
  bind('ip-bundleroot', 'input', function (v) { state.project.bundleRoot = v.trim() || 'auto'; });
  bind('ip-bfolder', 'input', function (v) { v = v.trim(); if (v) state.project.bundleFolder = v; else delete state.project.bundleFolder; });
  bind('ip-bsub', 'input', function (v) { state.project.bundleSub = v.trim(); });
  bind('ip-bcheck', 'input', function (v) { v = v.trim(); if (v) state.project.bundleCheck = v; else delete state.project.bundleCheck; });
  bind('ip-logfile', 'input', function (v) { state.project.logFile = v.trim(); });
  bindCheck('ip-admin', function (v) { state.project.requireAdmin = v; });
  bindCheck('ip-runall', function (v) { state.project.runAll = v; renderTasks(); });
  bindCheck('ip-summary', function (v) { if (v) delete state.project.summary; else state.project.summary = false; });
  bindStatusEditor();
}

// ---- 主選單狀態列（唯讀區塊; 每次重畫主選單執行, GUI 版顯示成狀態卡片）
function statusEligible(b) { return b && !b.raw && !b.needsBundle; }
function statusEditorHtml(p) {
  var rows = (p.statusSteps || []).map(function (s, i) {
    var b = blockById(s.block);
    var params = ((b && b.params) || []).map(function (pp) {
      var ov = (s.params || {})[pp.name] || {};
      var v = ov.value != null ? ov.value : (pp.default || '');
      return '<input class="tb-input tb-input--mono tb-input--sm" data-st="' + i + '" data-sp="' + esc(pp.name) +
        '" value="' + esc(v) + '" placeholder="' + esc(pp.name) + '" title="' + esc(pp.prompt || pp.name) + '">';
    }).join('');
    return '<div class="se-status-row"><span>' + esc(b ? b.name : '?' + s.block) + '</span>' + params +
      '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-st-del="' + i + '" title="移除">✕</button></div>';
  }).join('');
  var opts = state.presets.concat(state.custom).filter(statusEligible).map(function (b) {
    return '<option value="' + esc(b.id) + '">' + esc(b.name) + '</option>';
  }).join('');
  return '<div class="se-subhead">主選單狀態列</div>' +
    '<div class="tb-muted tb-small">每次畫主選單時先執行這些唯讀區塊，輸出顯示在選單上方；' +
    'GUI 版顯示成狀態卡片（「名稱 : 值」的行各成一張），F5 重新讀取。只能用不需工具包的單行區塊，參數一律填死。</div>' +
    (rows || '<div class="tb-muted tb-small" style="margin:4px 0">（沒有狀態列）</div>') +
    '<div class="tb-input-row" style="margin-top:6px"><select class="tb-input" id="ip-status-add">' + opts +
    '</select><button class="tb-btn tb-btn--sm" id="ip-status-add-btn" type="button">加入狀態列</button></div>';
}
function bindStatusEditor() {
  var p = state.project;
  var add = $('ip-status-add-btn');
  if (add) add.addEventListener('click', function () {
    var id = $('ip-status-add').value;
    var b = blockById(id);
    if (!b) return;
    var params = {};
    (b.params || []).forEach(function (pp) { params[pp.name] = { mode: 'fixed', value: pp.default || '' }; });
    (p.statusSteps = p.statusSteps || []).push({ block: id, params: params });
    renderInspector(); renderBadge();
  });
  Array.prototype.forEach.call($('insp-body').querySelectorAll('[data-st-del]'), function (btn) {
    btn.addEventListener('click', function () {
      p.statusSteps.splice(+btn.dataset.stDel, 1);
      if (!p.statusSteps.length) delete p.statusSteps;
      renderInspector(); renderBadge();
    });
  });
  Array.prototype.forEach.call($('insp-body').querySelectorAll('[data-st]'), function (el) {
    el.addEventListener('input', function () {
      var s = p.statusSteps[+el.dataset.st];
      (s.params = s.params || {})[el.dataset.sp] = { mode: 'fixed', value: el.value };
      renderBadge();
    });
  });
}

function renderTaskPanel(ti) {
  var t = state.project.tasks[ti];
  $('insp-title').textContent = '任務 ' + (ti + 1) + ' 設定';
  if (!t) { $('insp-body').innerHTML = '<div class="tb-empty">任務不存在</div>'; return; }
  $('insp-body').innerHTML =
    field('任務名稱（ASCII = 選單文字）', '<input class="tb-input" id="tp-name" value="' + esc(t.name) + '">') +
    field('選單說明（ASCII，可留空）', '<input class="tb-input" id="tp-desc" value="' + esc(t.desc || '') + '">') +
    field('分組標題（ASCII，可留空；和上一個任務不同時，選單上會先印這個標題）',
      '<input class="tb-input" id="tp-group" value="' + esc(t.group || '') + '" placeholder="Windows settings">') +
    field('選單按鍵（留空 = 自動編號 1, 2, 3…；或填一個英文字母 B~Z，例如 M）',
      '<input class="tb-input tb-input--mono" id="tp-key" maxlength="1" style="width:70px;text-transform:uppercase" value="' +
      esc(t.key || '') + '">') +
    '<div class="se-subhead">執行前檢查</div>' +
    check('tp-checktools', '檢查步驟用到的工具檔是否存在（{TOOLS}\\xxx.exe，缺少就列出並停止）', !!t.checkTools) +
    field('其他必要檔案（每行一個路徑，可用 {TOOLS} {BUNDLE}）',
      '<textarea class="tb-input tb-input--mono se-ta" id="tp-files" rows="3" spellcheck="false">' +
      esc((t.requireFiles || []).join('\n')) + '</textarea>') +
    check('tp-bundleopt', '找不到工具包時不算失敗：顯示 [INFO]，只略過需要工具包的步驟', !!t.bundleOptional) +
    check('tp-restarts', 'GUI 按鈕標「Restarts」（未勾 = 依步驟自動判斷）', t.restarts === true) +
    '<div class="tb-muted" style="margin-top:10px">共 ' + t.steps.length + ' 個步驟。' +
    '點選步驟可編輯其參數與失敗處理。</div>' +
    errBox();
  var task = state.project.tasks[ti];
  bind('tp-name', 'input', function (v) { task.name = v; renderTasks(); });
  bind('tp-desc', 'input', function (v) { task.desc = v; renderTasks(); });
  bind('tp-group', 'input', function (v) { if (v.trim()) task.group = v.trim(); else delete task.group; renderTasks(); });
  bind('tp-key', 'input', function (v) {
    v = v.trim().toUpperCase();
    if (v) task.key = v; else delete task.key;
    renderTasks();
  });
  bind('tp-files', 'input', function (v) {
    var files = v.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean);
    if (files.length) task.requireFiles = files; else delete task.requireFiles;
  });
  bindCheck('tp-checktools', function (v) { if (v) task.checkTools = true; else delete task.checkTools; });
  bindCheck('tp-bundleopt', function (v) { if (v) task.bundleOptional = true; else delete task.bundleOptional; });
  bindCheck('tp-restarts', function (v) { if (v) task.restarts = true; else delete task.restarts; });
}

function renderStepPanel(ti, si) {
  var t = state.project.tasks[ti], s = t && t.steps[si];
  if (!s) { $('insp-title').textContent = '步驟'; $('insp-body').innerHTML = '<div class="tb-empty">步驟不存在</div>'; return; }
  var b = blockById(s.block) || { name: '?' + s.block, desc: '', params: [], expectRc: [0] };
  $('insp-title').textContent = '步驟 ' + (si + 1) + '：' + b.name;
  var html = '<div class="tb-muted" style="margin-bottom:8px">' + esc(b.desc || '') +
    '<br><code>' + esc(b.id) + '</code></div>';
  if (b.raw) {
    html += '<div class="se-note">這是 raw 區塊：匯出時直接展開批次行，不做 rc 檢查；' +
      '失敗處理與重複設定無效。</div>';
  }
  // 參數
  var ps = (b.params || []);
  if (ps.length) {
    html += '<div class="se-subhead">參數</div>';
    ps.forEach(function (p, pi) {
      var ov = (s.params || {})[p.name] || {};
      var mode = ov.mode || p.mode || 'ask';
      var value = ov.value != null ? ov.value : (p.value || '');
      var dflt = ov.default != null ? ov.default : (p.default || '');
      html += '<div class="se-param">' +
        '<div class="se-param__head"><b>' + esc(p.name) + '</b>' +
        '<select class="tb-input tb-input--sm" data-k="mode" data-p="' + esc(p.name) + '">' +
        '<option value="ask"' + (mode === 'ask' ? ' selected' : '') + '>執行時詢問</option>' +
        '<option value="fixed"' + (mode === 'fixed' ? ' selected' : '') + '>製作時填死</option>' +
        '</select></div>';
      if (mode === 'fixed') {
        html += '<input class="tb-input tb-input--mono" data-k="value" data-p="' + esc(p.name) +
          '" value="' + esc(value) + '" placeholder="固定值">';
      } else {
        html += '<div class="se-param__row">' +
          '<label class="tb-small tb-muted">預設值</label>' +
          '<input class="tb-input tb-input--mono" data-k="default" data-p="' + esc(p.name) +
          '" value="' + esc(dflt) + '"></div>';
      }
      var up = ov.uppercase != null ? ov.uppercase : !!p.uppercase;
      var lo = ov.minLength != null ? ov.minLength : (p.minLength || '');
      var hi = ov.maxLength != null ? ov.maxLength : (p.maxLength || '');
      html += '<div class="se-param__row">' +
        '<label class="tb-check"><input type="checkbox" data-k="uppercase" data-p="' + esc(p.name) + '"' +
        (up ? ' checked' : '') + '> 轉大寫</label>' +
        '<label class="tb-small tb-muted">長度</label>' +
        '<input class="tb-input tb-input--mono" type="number" min="0" max="256" style="width:70px" data-k="minLength" data-p="' +
        esc(p.name) + '" value="' + esc(lo) + '" placeholder="最少">' +
        '<span class="tb-muted">~</span>' +
        '<input class="tb-input tb-input--mono" type="number" min="0" max="256" style="width:70px" data-k="maxLength" data-p="' +
        esc(p.name) + '" value="' + esc(hi) + '" placeholder="最多"></div>';
      html += '</div>';
    });
  }
  if (!b.raw) {
    // rc / onFail / loop / confirm
    var rc = (Array.isArray(s.expectRc) && s.expectRc.length) ? s.expectRc
           : (Array.isArray(b.expectRc) && b.expectRc.length) ? b.expectRc : [0];
    html += '<div class="se-subhead">執行控制</div>' +
      field('可接受 exit code（逗號分隔）',
        '<div class="tb-input-row">' +
        '<label class="tb-check"><input type="checkbox" id="sp-rcov"' + (s.expectRc ? ' checked' : '') + '> 覆蓋區塊預設</label>' +
        '<input class="tb-input tb-input--mono" id="sp-rc" value="' + esc(rc.join(', ')) + '"' +
        (s.expectRc ? '' : ' disabled') + '></div>') +
      field('步驟失敗時',
        '<select class="tb-input" id="sp-onfail">' +
        ['stop', 'continue', 'ask'].map(function (m) {
          return '<option value="' + m + '"' + ((s.onFail || 'stop') === m ? ' selected' : '') + '>' +
            ({ stop: '停止任務 (stop)', continue: '跳過繼續 (continue)', ask: '詢問操作員 (ask)' })[m] + '</option>';
        }).join('') + '</select>') +
      check('sp-confirm', '執行前詢問操作員（可跳過此步驟）', !!s.confirm) +
      field('成功後再確認（ASCII 問題，例如 Restart now?；選否就不跑本任務後續步驟，任務仍算成功）',
        '<input class="tb-input" id="sp-askafter" value="' + esc(s.askAfter || '') + '" placeholder="Restart now?">') +
      field('重複執行',
        '<select class="tb-input" id="sp-loopmode">' +
        '<option value="">不重複</option>' +
        '<option value="times"' + (s.loop && s.loop.mode === 'times' ? ' selected' : '') + '>固定次數</option>' +
        '<option value="duration"' + (s.loop && s.loop.mode === 'duration' ? ' selected' : '') + '>持續時間</option>' +
        '</select>' +
        '<div class="tb-input-row" id="sp-loop-args" style="margin-top:6px">' +
        '<input class="tb-input tb-input--mono" id="sp-times" type="number" min="2" max="9999" style="width:110px" value="' +
        ((s.loop && s.loop.times) || 3) + '" placeholder="次數">' +
        '<input class="tb-input tb-input--mono" id="sp-min" type="number" min="1" max="1440" style="width:110px" value="' +
        ((s.loop && s.loop.minutes) || 10) + '" placeholder="分鐘">' +
        '<input class="tb-input tb-input--mono" id="sp-delay" type="number" min="0" max="3600" style="width:110px" value="' +
        ((s.loop && s.loop.delaySec) || 0) + '" placeholder="間隔秒">' +
        '</div>');
  }
  html += errBox();
  $('insp-body').innerHTML = html;
  if (!b.raw) updateLoopVisibility();

  // ---- bindings
  Array.prototype.forEach.call($('insp-body').querySelectorAll('[data-p]'), function (el) {
    var handler = function () {
      var name = el.dataset.p, k = el.dataset.k;
      if (!s.params) s.params = {};
      if (!s.params[name]) s.params[name] = {};
      if (el.type === 'checkbox') s.params[name][k] = el.checked;
      else if (el.type === 'number') {
        var n = parseInt(el.value, 10);
        if (isNaN(n)) delete s.params[name][k]; else s.params[name][k] = n;
      } else s.params[name][k] = el.value;
      if (k === 'mode') renderInspector();
      renderTasks();
    };
    el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input', handler);
  });
  if (!b.raw) {
    var rcOv = $('sp-rcov'), rcIn = $('sp-rc');
    var syncRc = function () {
      if (!rcOv.checked) { delete s.expectRc; rcIn.disabled = true; return; }
      rcIn.disabled = false;
      var codes = rcIn.value.split(',').map(function (x) { return parseInt(x.trim(), 10); })
        .filter(function (x) { return !isNaN(x); });
      if (codes.length) s.expectRc = codes;
    };
    rcOv.addEventListener('change', function () { syncRc(); renderTasks(); });
    rcIn.addEventListener('input', syncRc);
    $('sp-onfail').addEventListener('change', function () { s.onFail = this.value; renderTasks(); });
    bindCheck('sp-confirm', function (v) { s.confirm = v; renderTasks(); });
    bind('sp-askafter', 'input', function (v) { if (v.trim()) s.askAfter = v.trim(); else delete s.askAfter; renderTasks(); });
    var syncLoop = function () {
      var mode = $('sp-loopmode').value;
      if (!mode) { delete s.loop; renderInspector(); return; }
      s.loop = { mode: mode,
                 times: Math.max(2, parseInt($('sp-times').value, 10) || 3),
                 minutes: Math.max(1, parseInt($('sp-min').value, 10) || 10),
                 delaySec: Math.max(0, parseInt($('sp-delay').value, 10) || 0) };
      updateLoopVisibility();
      renderTasks();
    };
    $('sp-loopmode').addEventListener('change', syncLoop);
    ['sp-times', 'sp-min', 'sp-delay'].forEach(function (id) {
      $(id).addEventListener('input', function () {
        if (!s.loop) return;
        s.loop.times = Math.max(2, parseInt($('sp-times').value, 10) || 3);
        s.loop.minutes = Math.max(1, parseInt($('sp-min').value, 10) || 10);
        s.loop.delaySec = Math.max(0, parseInt($('sp-delay').value, 10) || 0);
        renderTasks();
      });
    });
  }
}
function updateLoopVisibility() {
  var mode = $('sp-loopmode') && $('sp-loopmode').value;
  var box = $('sp-loop-args');
  if (!box) return;
  $('sp-times').style.display = mode === 'times' ? '' : 'none';
  $('sp-min').style.display = mode === 'duration' ? '' : 'none';
  $('sp-delay').style.display = mode ? '' : 'none';
  if (!mode) box.style.display = 'none';
  else box.style.display = '';
}
function errBox() {
  var mine = state.lastErrors.filter(function (e) {
    var t = state.project.tasks[state.sel.ti];
    return t && e.indexOf(t.name) >= 0;
  });
  if (!mine.length) return '';
  return '<div class="se-subhead">此任務的驗證錯誤</div><div class="se-errlist">' +
    mine.map(function (e) { return '<div>' + esc(e) + '</div>'; }).join('') + '</div>';
}

// ---------------------------------------------------------------- 小工具
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function field(label, inner) {
  return '<label class="se-flabel">' + esc(label) + '</label>' + inner;
}
function check(id, label, on) {
  return '<label class="tb-check" style="display:block;margin:6px 0">' +
    '<input type="checkbox" id="' + id + '"' + (on ? ' checked' : '') + '> ' + esc(label) + '</label>';
}
function bind(id, ev, fn) {
  var el = $(id);
  if (el) el.addEventListener(ev, function () { fn(el.value); renderBadge(); });
}
function bindCheck(id, fn) {
  var el = $(id);
  if (el) el.addEventListener('change', function () { fn(el.checked); renderBadge(); });
}
function renderAll() {
  renderTasks();
  renderInspector();
  renderBadge();
}

// ---------------------------------------------------------------- 專案动作
function newProject() {
  if (dirty() && !confirm('目前的修改尚未儲存，確定要開新專案？')) return;
  state.project = defaultProject();
  state.sel = { kind: 'project' };
  state.savedJson = '';
  state.savedName = '';
  state.lastErrors = [];
  $('proj-name').value = '';
  renderAll();
  renderLib();
}
function loadProject(p) {
  if (dirty() && !confirm('目前的修改尚未儲存，確定要放棄？')) return;
  // 補預設欄位 + 防呆
  var d = defaultProject();
  Object.keys(d).forEach(function (k) { if (p[k] === undefined) p[k] = d[k]; });
  if (!Array.isArray(p.tasks)) p.tasks = [];
  p.tasks.forEach(function (t) { if (!Array.isArray(t.steps)) t.steps = []; });
  state.project = p;
  state.sel = { kind: 'project' };
  state.lastErrors = [];
  state.savedName = '';      // 先清, 避免 markSaved 前被誤判 dirty
  state.savedJson = '';
  markSaved();
  $('proj-name').value = p.name;
  renderAll();
}

function currentProject() {
  state.project.name = $('proj-name').value.trim();
  return state.project;
}
function showErrors(errs) {
  state.lastErrors = errs;
  if (errs.length) {
    toast('驗證失敗：' + errs.length + ' 個錯誤', false);
    renderInspector();
  } else {
    toast('驗證通過', true);
    renderInspector();
  }
}
function doValidate() {
  postJson('/api/validate', { project: currentProject() })
    .then(unwrap)
    .then(function (r) { showErrors(r.errors || []); })
    .catch(function (e) { toast('驗證失敗：' + e.message, false); });
}
function doSave() {
  var p = currentProject();
  postJson('/api/project/save', { project: p })
    .then(unwrap)
    .then(function () {
      markSaved();
      toast('已儲存：' + p.name, true);
    })
    .catch(function (e) { toast('儲存失敗：' + e.message, false); });
}
function doExport(gui) {
  var p = currentProject();
  postJson('/api/export', { project: p, format: gui ? 'gui' : 'bat' })
    .then(unwrap)
    .then(function (r) {
      var blob = new Blob([r.text], { type: 'application/octet-stream' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = r.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      toast('已匯出 ' + r.filename + '（' + r.bytes + ' bytes）', true);
    })
    .catch(function (e) { toast('匯出失敗：' + e.message, false); });
}

// ---------------------------------------------------------------- 開啟專案彈窗
function projectPickerRow(title, details, actions) {
  return '<li class="se-picker-row"><div class="se-picker-row__info">' +
    '<strong>' + esc(title) + '</strong><span class="tb-small tb-muted">' + esc(details) + '</span></div>' +
    '<div class="se-picker-row__actions">' + actions + '</div></li>';
}
function savedProjectRow(it, allowDelete) {
  var details = it.tasks + ' 個任務' + (it.updated ? ' · ' + it.updated : '');
  var actions = '<button class="tb-btn tb-btn--sm" type="button" data-open="' + esc(it.name) + '">開啟</button>';
  if (allowDelete) actions += '<button class="tb-btn tb-btn--sm tb-btn--danger" type="button" data-del="' +
    esc(it.name) + '" aria-label="刪除專案 ' + esc(it.name) + '">刪除</button>';
  return projectPickerRow(it.name, details, actions);
}
function projectPickerList(rows, empty) {
  return rows.length ? '<ul class="se-picker-list">' + rows.join('') + '</ul>' :
    '<div class="tb-empty">' + esc(empty) + '</div>';
}
function openProjectModal() {
  $('open-title').textContent = '開啟專案';
  getJson('/api/projects').then(unwrap).then(function (list) {
    $('open-list').innerHTML = projectPickerList(list.map(function (it) {
      return savedProjectRow(it, true);
    }), '尚無已儲存的專案');
    $('open-mask').classList.remove('hidden');
    Array.prototype.forEach.call($('open-list').querySelectorAll('[data-open]'), function (btn) {
      btn.addEventListener('click', function () {
        getJson('/api/project?name=' + encodeURIComponent(btn.dataset.open)).then(unwrap)
          .then(function (p) {
            $('open-mask').classList.add('hidden');
            loadProject(p);
            toast('已開啟：' + p.name, true);
          })
          .catch(function (e) { toast('開啟失敗：' + e.message, false); });
      });
    });
    Array.prototype.forEach.call($('open-list').querySelectorAll('[data-del]'), function (btn) {
      btn.addEventListener('click', function () {
        if (!confirm('刪除專案「' + btn.dataset.del + '」？')) return;
        postJson('/api/project/delete', { name: btn.dataset.del }).then(unwrap)
          .then(openProjectModal)
          .catch(function (e) { toast('刪除失敗：' + e.message, false); });
      });
    });
  }).catch(function (e) { toast('讀取清單失敗：' + e.message, false); });
}

// ---------------------------------------------------------------- 載入範例彈窗
// 內建範例（唯讀）優先顯示，下方保留已儲存或匯入的專案。
function openSampleModal() {
  $('open-title').textContent = '載入範例';
  Promise.all([getJson('/api/projects').then(unwrap), getJson('/api/samples').then(unwrap)])
    .then(function (r) {
      var mine = r[0], samples = r[1];
      var sampleRows = samples.map(function (it) {
        var details = (it.title && it.title !== it.name ? it.name + ' · ' : '') + it.tasks + ' 個任務';
        return projectPickerRow(it.title || it.name, details,
          '<button class="tb-btn tb-btn--sm" type="button" data-sample="' + esc(it.id) + '">載入</button>');
      });
      var projectRows = mine.map(function (it) { return savedProjectRow(it, false); });
      $('open-list').innerHTML =
        '<section class="se-picker-section" aria-labelledby="picker-samples"><h3 id="picker-samples">內建範例</h3>' +
        '<p class="tb-small tb-muted">唯讀，修改後請以新名稱儲存。</p>' +
        projectPickerList(sampleRows, '無內建範例') + '</section>' +
        '<section class="se-picker-section" aria-labelledby="picker-projects"><h3 id="picker-projects">我的專案</h3>' +
        projectPickerList(projectRows, '尚無專案。儲存或匯入後會顯示在這裡。') + '</section>';
      $('open-mask').classList.remove('hidden');
      Array.prototype.forEach.call($('open-list').querySelectorAll('[data-open]'), function (btn) {
        btn.addEventListener('click', function () { openSavedProject(btn.dataset.open); });
      });
      Array.prototype.forEach.call($('open-list').querySelectorAll('[data-sample]'), function (btn) {
        btn.addEventListener('click', function () {
          if (dirty() && !confirm('目前專案有未儲存的修改，仍要載入範例？')) return;
          getJson('/api/sample?id=' + encodeURIComponent(btn.dataset.sample)).then(unwrap)
            .then(function (p) {
              $('open-mask').classList.add('hidden');
              loadProject(p);
              toast('已載入範例：' + p.name + '（修改後另存新名稱）', true);
            })
            .catch(function (e) { toast('範例載入失敗：' + e.message, false); });
        });
      });
    }).catch(function (e) { toast('範例清單載入失敗：' + e.message, false); });
}
function openSavedProject(name) {
  if (dirty() && !confirm('目前專案有未儲存的修改，仍要開啟「' + name + '」？')) return Promise.resolve();
  return getJson('/api/project?name=' + encodeURIComponent(name)).then(unwrap)
    .then(function (p) {
      $('open-mask').classList.add('hidden');
      loadProject(p);
      toast('已開啟：' + p.name, true);
    })
    .catch(function (e) { toast('開啟失敗：' + e.message, false); });
}

// ---------------------------------------------------------------- 自訂區塊編輯
var blkEditing = null;   // 編輯中的自訂區塊 id（null = 新增）
function openBlockEditor(bid) {
  blkEditing = bid || null;
  var b = bid ? blockById(bid) : null;
  $('blk-title').textContent = bid ? '編輯自訂區塊：' + bid : '新增自訂區塊';
  $('blk-id').value = b ? b.id : '';
  $('blk-id').disabled = !!bid;
  $('blk-name').value = b ? b.name : '';
  $('blk-cat').value = b ? (b.category || '自訂') : '自訂';
  $('blk-desc').value = b ? (b.desc || '') : '';
  $('blk-cmd').value = b && b.cmd ? b.cmd : '';
  $('blk-display').value = b && b.display ? b.display : '';
  $('blk-raw').value = b && b.raw ? b.raw.join('\n') : '';
  $('blk-rc').value = b && b.expectRc ? b.expectRc.join(', ') : '0';
  $('blk-bundle').checked = !!(b && b.needsBundle);
  $('blk-restarts').checked = !!(b && b.restarts);
  $('blk-err').classList.add('hidden');
  var d = $('blk-delete');
  if (d) d.classList.toggle('hidden', !bid);
  $('blk-params').innerHTML = '';
  ((b && b.params) || []).forEach(addParamRow);
  $('blk-mask').classList.remove('hidden');
}
function addParamRow(p) {
  p = p || { name: '', prompt: '', mode: 'ask', default: '', validate: '' };
  var div = document.createElement('div');
  div.className = 'se-paramrow';
  div.innerHTML =
    '<input class="tb-input tb-input--mono" data-k="name" value="' + esc(p.name) + '" placeholder="NAME" style="width:110px">' +
    '<input class="tb-input" data-k="prompt" value="' + esc(p.prompt || '') + '" placeholder="提示文字（ASCII）">' +
    '<select class="tb-input" data-k="mode" style="width:120px">' +
    '<option value="ask"' + (p.mode !== 'fixed' ? ' selected' : '') + '>詢問</option>' +
    '<option value="fixed"' + (p.mode === 'fixed' ? ' selected' : '') + '>填死</option></select>' +
    '<input class="tb-input tb-input--mono" data-k="default" value="' + esc(p.default || '') + '" placeholder="預設/固定值" style="width:130px">' +
    '<input class="tb-input tb-input--mono" data-k="validate" value="' + esc(p.validate || '') + '" placeholder="regex（選填）" style="width:150px">' +
    '<label class="tb-check" title="輸入值轉大寫"><input type="checkbox" data-k="uppercase"' + (p.uppercase ? ' checked' : '') + '> 大寫</label>' +
    '<input class="tb-input tb-input--mono" type="number" min="0" max="256" data-k="minLength" value="' + esc(p.minLength || '') + '" placeholder="最短" style="width:70px">' +
    '<input class="tb-input tb-input--mono" type="number" min="0" max="256" data-k="maxLength" value="' + esc(p.maxLength || '') + '" placeholder="最長" style="width:70px">' +
    '<button class="tb-btn tb-btn--sm tb-btn--ghost" title="移除參數">✕</button>';
  div.querySelector('button').addEventListener('click', function () { div.remove(); });
  $('blk-params').appendChild(div);
}
function collectBlock() {
  var b = {
    id: $('blk-id').value.trim(),
    name: $('blk-name').value.trim(),
    category: $('blk-cat').value.trim() || '自訂',
    desc: $('blk-desc').value.trim()
  };
  var cmd = $('blk-cmd').value.trim();
  var raw = $('blk-raw').value.split(/\r?\n/).map(function (l) { return l.replace(/\s+$/, ''); })
    .filter(function (l) { return l.length; });
  if (cmd) b.cmd = cmd;
  var display = $('blk-display').value.trim();
  if (display) b.display = display;
  if (raw.length) b.raw = raw;
  var rc = $('blk-rc').value.split(',').map(function (x) { return parseInt(x.trim(), 10); })
    .filter(function (x) { return !isNaN(x); });
  if (rc.length) b.expectRc = rc;
  if ($('blk-bundle').checked) b.needsBundle = true;
  if ($('blk-restarts').checked) b.restarts = true;
  var params = [];
  Array.prototype.forEach.call($('blk-params').querySelectorAll('.se-paramrow'), function (row) {
    var p = { name: '', prompt: '', mode: 'ask', default: '', validate: '' };
    Array.prototype.forEach.call(row.querySelectorAll('[data-k]'), function (el) {
      if (el.type === 'checkbox') { if (el.checked) p[el.dataset.k] = true; return; }
      if (el.type === 'number') {
        var n = parseInt(el.value, 10);
        if (!isNaN(n)) p[el.dataset.k] = n;
        return;
      }
      p[el.dataset.k] = el.value.trim();
    });
    if (!p.name) return;
    if (!p.prompt) p.prompt = p.name;
    if (!p.default) delete p.default;
    if (!p.validate) delete p.validate;
    params.push(p);
  });
  if (params.length) b.params = params;
  return b;
}
function saveBlock() {
  var b = collectBlock();
  var err = $('blk-err');
  err.classList.add('hidden');
  postJson('/api/blocks/save', { block: b }).then(unwrap).then(function () {
    $('blk-mask').classList.add('hidden');
    return refreshBlocks();
  }).then(function () {
    toast('自訂區塊已儲存：' + b.id, true);
    renderAll();
  }).catch(function (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  });
}
function deleteBlock(bid) {
  if (!confirm('刪除自訂區塊「' + bid + '」？引用它的專案會驗證失敗。')) return;
  postJson('/api/blocks/delete', { id: bid }).then(unwrap).then(refreshBlocks)
    .then(function () { toast('已刪除區塊：' + bid, true); renderAll(); })
    .catch(function (e) { toast('刪除失敗：' + e.message, false); });
}

// ---------------------------------------------------------------- 區塊包（.sepack.json）
var packMode = null;      // 'export' | 'import'
var packImport = null;    // { content, preview }
function packOpen(mode, title, goText) {
  packMode = mode;
  $('pack-title').textContent = title;
  $('pack-go').textContent = goText;
  $('pack-go').disabled = false;
  $('pack-err').classList.add('hidden');
  $('pack-mask').classList.remove('hidden');
}
function packClose() { $('pack-mask').classList.add('hidden'); packMode = null; packImport = null; }
function packErr(msg) { $('pack-err').textContent = msg; $('pack-err').classList.remove('hidden'); }

function openPackExport() {
  getJson('/api/pack/inventory').then(unwrap).then(function (inv) {
    var customIds = {};
    inv.blocks.forEach(function (b) { customIds[b.id] = true; });
    var cur = state.savedName;
    var html =
      '<div class="se-fgrid">' +
      '<label>包名稱（檔名由來）<input class="tb-input tb-input--mono" id="pk-name" spellcheck="false" value="' +
      esc(cur || 'my-blocks') + '"></label>' +
      '<label class="se-fspan">說明（選填，同事匯入時會看到）<input class="tb-input" id="pk-desc"></label></div>' +
      '<p class="tb-small tb-muted">只打包自訂區塊與已儲存的專案；內建區塊隨工具發佈，不會放進包裡。勾選專案時會自動勾選它用到的自訂區塊。</p>' +
      '<div class="se-subhead">專案（' + inv.projects.length + '）</div>' +
      (inv.projects.length ? inv.projects.map(function (p) {
        var used = p.blocks.filter(function (id) { return customIds[id]; });
        return '<label class="tb-check se-pack-row"><input type="checkbox" data-pk-proj="' + esc(p.name) + '" data-uses="' +
          esc(used.join(' ')) + '"' + (p.name === cur ? ' checked' : '') + '> <b>' + esc(p.name) + '</b> ' +
          '<span class="tb-small tb-muted">' + esc(p.title || '') + '・' + p.tasks + ' 個任務' +
          (used.length ? '・用到 ' + used.length + ' 個自訂區塊' : '') + '</span></label>';
      }).join('') : '<div class="tb-empty">尚無已儲存的專案</div>') +
      '<div class="se-subhead">自訂區塊（' + inv.blocks.length + '）' +
      (inv.blocks.length ? ' <button class="tb-btn tb-btn--sm tb-btn--ghost" id="pk-all" type="button">全選／全不選</button>' : '') +
      '</div>' +
      (inv.blocks.length ? '<div class="se-pack-list">' + inv.blocks.map(function (b) {
        return '<label class="tb-check se-pack-row"><input type="checkbox" data-pk-block="' + esc(b.id) + '"> ' +
          '<span class="tb-input--mono">' + esc(b.id) + '</span> <span class="tb-small tb-muted">' +
          esc(b.name) + (b.category ? '・' + esc(b.category) : '') + '</span></label>';
      }).join('') + '</div>' : '<div class="tb-empty">尚無自訂區塊</div>');
    $('pack-body').innerHTML = html;
    var body = $('pack-body');
    function syncUses(cb) {
      (cb.dataset.uses || '').split(' ').filter(Boolean).forEach(function (id) {
        var t = body.querySelector('[data-pk-block="' + CSS.escape(id) + '"]');
        if (t && cb.checked) t.checked = true;
      });
    }
    Array.prototype.forEach.call(body.querySelectorAll('[data-pk-proj]'), function (cb) {
      if (cb.checked) syncUses(cb);
      cb.addEventListener('change', function () { syncUses(cb); });
    });
    var all = $('pk-all');
    if (all) all.addEventListener('click', function () {
      var boxes = body.querySelectorAll('[data-pk-block]');
      var on = Array.prototype.some.call(boxes, function (b) { return !b.checked; });
      Array.prototype.forEach.call(boxes, function (b) { b.checked = on; });
    });
    packOpen('export', '匯出區塊包（.sepack.json）', '匯出');
  }).catch(function (e) { toast('讀取清單失敗：' + e.message, false); });
}
function doPackExport() {
  var body = $('pack-body');
  var pick = function (attr) {
    return Array.prototype.filter.call(body.querySelectorAll('[' + attr + ']'), function (c) { return c.checked; })
      .map(function (c) { return c.getAttribute(attr); });
  };
  var req = { name: $('pk-name').value.trim(), description: $('pk-desc').value.trim(),
              blocks: pick('data-pk-block'), projects: pick('data-pk-proj') };
  if (!req.blocks.length && !req.projects.length) return packErr('請至少勾選一個區塊或專案');
  postJson('/api/pack/export', req).then(unwrap).then(function (r) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([r.content], { type: 'application/json' }));
    a.download = r.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    packClose();
    toast('已匯出 ' + r.filename + '（' + r.counts.blocks + ' 個區塊、' + r.counts.projects + ' 個專案）' +
          (r.warnings.length ? '；注意：' + r.warnings.join('；') : ''), !r.warnings.length);
  }).catch(function (e) { packErr(e.message); });
}

var PACK_ACT = { create: '新增', overwrite: '覆蓋', rename: '改名', skip: '略過', conflict: '名稱重複' };
function openPackImport() { $('pack-file').value = ''; $('pack-file').click(); }
function onPackFile() {
  var f = $('pack-file').files[0];
  if (!f) return;
  if (f.size > 900 * 1024) return toast('檔案太大（上限約 900 KB）', false);
  f.text().then(function (text) {
    packImport = { content: text, preview: null, file: f.name };
    return packPreview({});
  }).catch(function (e) { toast('讀取檔案失敗：' + e.message, false); });
}
function packDecisions() {
  var d = {};
  Array.prototype.forEach.call($('pack-body').querySelectorAll('[data-pk-key]'), function (s) {
    d[s.dataset.pkKey] = s.value;
  });
  return d;
}
function packPreview(decisions) {
  return postJson('/api/pack/preview', { content: packImport.content, decisions: decisions }).then(unwrap)
    .then(function (r) {
      packImport.preview = r;
      var p = r.pack || {};
      var html = '<div class="tb-small">' +
        '<b>' + esc(p.name || '') + '</b>　<span class="tb-muted">' + esc(packImport.file) + '・' +
        esc(p.createdAt || '') + '・Script-Editor ' + esc((p.tool && p.tool.version) || '?') + '</span>' +
        (p.description ? '<br>' + esc(p.description) : '') + '</div>' +
        '<p class="tb-small tb-muted">名稱重複的項目請選擇處理方式。覆蓋前會把原檔備份到資料資料夾的 backups\\pack-import-&lt;時間&gt;\\；內建區塊不能覆蓋，只能改名（-2）或略過。區塊改名時，包內專案的引用會一起改。</p>' +
        '<div class="se-pack-tools tb-small">名稱重複的全部：' +
        '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-pk-all="overwrite" type="button">覆蓋</button> ' +
        '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-pk-all="rename" type="button">改名</button> ' +
        '<button class="tb-btn tb-btn--sm tb-btn--ghost" data-pk-all="skip" type="button">略過</button></div>' +
        '<div class="tb-table-wrap se-pack-list"><table class="tb-table"><thead><tr><th>類型</th><th>名稱</th><th>狀態</th><th>處理</th></tr></thead><tbody>' +
        r.items.map(function (it) {
          var sel = '';
          if (it.conflict) {
            var opts = it.builtin ? ['rename', 'skip'] : ['overwrite', 'rename', 'skip'];
            var cur = decisions[it.key] || (it.action !== 'conflict' ? it.action : '');
            sel = '<select class="tb-input" data-pk-key="' + esc(it.key) + '">' +
              (cur ? '' : '<option value="" selected disabled>請選擇…</option>') +
              opts.map(function (o) {
                return '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + PACK_ACT[o] + '</option>';
              }).join('') + '</select>' +
              (it.action === 'rename' ? ' <span class="tb-small tb-muted">→ ' + esc(it.target) + '</span>' : '');
          } else {
            sel = '<span class="tb-small">新增</span>';
          }
          return '<tr><td>' + (it.kind === 'block' ? '區塊' : '專案') + '</td>' +
            '<td><span class="tb-input--mono">' + esc(it.name) + '</span><br><span class="tb-small tb-muted">' +
            esc(it.label || '') + '</span></td>' +
            '<td>' + (it.conflict ? '<span class="tb-badge tb-badge--warn">' + (it.builtin ? '與內建區塊重複' : '已存在') + '</span>'
                                  : '<span class="tb-badge tb-badge--ok">新項目</span>') + '</td>' +
            '<td>' + sel + '</td></tr>';
        }).join('') + '</tbody></table></div>' +
        (r.warnings.length ? '<div class="tb-small tb-warn" style="margin-top:8px">' +
          r.warnings.map(esc).join('<br>') + '</div>' : '');
      $('pack-body').innerHTML = html;
      var body = $('pack-body');
      Array.prototype.forEach.call(body.querySelectorAll('[data-pk-key]'), function (s) {
        s.addEventListener('change', function () { packPreview(packDecisions()).catch(function (e) { packErr(e.message); }); });
      });
      Array.prototype.forEach.call(body.querySelectorAll('[data-pk-all]'), function (b) {
        b.addEventListener('click', function () {
          var d = {};
          r.items.forEach(function (it) {
            if (!it.conflict) return;
            d[it.key] = (b.dataset.pkAll === 'overwrite' && it.builtin) ? 'rename' : b.dataset.pkAll;
          });
          packPreview(d).catch(function (e) { packErr(e.message); });
        });
      });
      if (packMode !== 'import') packOpen('import', '匯入區塊包：預覽', '匯入');
      $('pack-err').classList.add('hidden');
      var n = r.items.filter(function (it) { return it.action !== 'skip' && it.action !== 'conflict'; }).length;
      $('pack-go').textContent = r.unresolved.length ? '匯入（尚有 ' + r.unresolved.length + ' 項未選）' : '匯入 ' + n + ' 項';
      $('pack-go').disabled = !!r.unresolved.length || !n;
    })
    .catch(function (e) {
      if (packMode === 'import') return packErr(e.message);
      toast('無法讀取區塊包：' + e.message, false);
    });
}
function doPackImport() {
  var decisions = packDecisions();
  if (!confirm('確定匯入？覆蓋的檔案會先備份。')) return;
  postJson('/api/pack/import', { content: packImport.content, decisions: decisions, confirm: true }).then(unwrap)
    .then(function (r) {
      packClose();
      return refreshBlocks().then(function () {
        renderAll();
        var b = r.written.filter(function (w) { return w.kind === 'block'; }).length;
        var projs = r.written.filter(function (w) { return w.kind === 'project'; });
        toast('已匯入 ' + b + ' 個區塊、' + projs.length + ' 個專案' + (r.backups ? '；原檔備份在 ' + r.backups : ''), true);
        // 匯入的專案直接問要不要開（之後也可在「開啟…」或「載入範例 → 我的專案」找到）
        if (projs.length && confirm('已匯入專案「' + projs[0].name + '」' +
            (projs.length > 1 ? '等 ' + projs.length + ' 個' : '') + '，現在開啟？')) {
          return openSavedProject(projs[0].name);
        }
      });
    }).catch(function (e) { packErr(e.message); });
}

// ---------------------------------------------------------------- init
function refreshBlocks() {
  return getJson('/api/blocks').then(unwrap).then(function (r) {
    state.presets = r.presets;
    state.custom = r.custom;
    state.errors = r.errors || [];
    renderLib();
    if (state.errors.length) console.warn('block load errors:', state.errors);
  });
}
function initThemeToggle() {
  var btn = $('theme-toggle');
  if (!btn || !window.TbTheme) return;
  var labels = { light: '淺色', dark: '深色', system: '跟隨系統' };
  var icons = { light: 'sun', dark: 'moon', system: 'monitor' };
  function paint() {
    var mode = TbTheme.get().mode;
    if (window.TbIcons) btn.innerHTML = TbIcons.svg(icons[mode], { anim: 'wiggle' });
    btn.title = '主題：' + labels[mode] + '（點一下切換）';
    btn.setAttribute('aria-label', btn.title);
  }
  paint();
  document.addEventListener('tb-theme-change', paint);
  btn.addEventListener('click', function () {
    var m = TbTheme.get().mode;
    TbTheme.set(m === 'light' ? 'dark' : m === 'dark' ? 'system' : 'light');
  });
}
function init() {
  state.project = defaultProject();
  // /api/health 是平鋪回應（無 result 包裝, TOOL-SPEC 5）
  getJson('/api/health').then(function (h) {
    $('ver').textContent = (h && h.ok) ? 'v' + h.version : '?';
  }).catch(function () { $('ver').textContent = '?'; });

  refreshBlocks().catch(function (e) { toast('區塊庫載入失敗：' + e.message, false); });

  $('proj-name').addEventListener('input', renderBadge);
  $('lib-search').addEventListener('input', renderLib);
  $('btn-new').addEventListener('click', newProject);
  $('btn-open').addEventListener('click', openProjectModal);
  $('open-close').addEventListener('click', function () { $('open-mask').classList.add('hidden'); });
  $('open-mask').addEventListener('click', function (ev) {
    if (ev.target === $('open-mask')) $('open-mask').classList.add('hidden');
  });
  $('btn-sample').addEventListener('click', openSampleModal);
  $('btn-add-task').addEventListener('click', function () {
    currentProject();
    state.project.tasks.push({ name: 'Task ' + (state.project.tasks.length + 1), desc: '', steps: [] });
    state.sel = { kind: 'task', ti: state.project.tasks.length - 1 };
    renderAll();
  });
  $('btn-validate').addEventListener('click', doValidate);
  $('btn-save').addEventListener('click', doSave);
  $('btn-export').addEventListener('click', function () { doExport(false); });
  $('btn-export-gui').addEventListener('click', function () { doExport(true); });
  $('btn-add-block').addEventListener('click', function () { openBlockEditor(null); });
  $('btn-pack-export').addEventListener('click', openPackExport);
  $('btn-pack-import').addEventListener('click', openPackImport);
  $('pack-file').addEventListener('change', onPackFile);
  $('pack-close').addEventListener('click', packClose);
  $('pack-go').addEventListener('click', function () {
    if (packMode === 'export') doPackExport();
    else if (packMode === 'import') doPackImport();
  });
  $('blk-close').addEventListener('click', function () { $('blk-mask').classList.add('hidden'); });
  $('blk-add-param').addEventListener('click', function () { addParamRow(); });
  $('blk-save').addEventListener('click', saveBlock);
  // 區塊編輯彈窗內的刪除（動態加, 只對 custom 顯示）
  var delBtn = document.createElement('button');
  delBtn.className = 'tb-btn tb-btn--sm tb-btn--ghost tb-danger hidden';
  delBtn.id = 'blk-delete';
  delBtn.type = 'button';
  delBtn.textContent = '刪除區塊';
  delBtn.addEventListener('click', function () {
    if (!blkEditing) return;
    deleteBlock(blkEditing);
    $('blk-mask').classList.add('hidden');
  });
  $('blk-mask').querySelector('.se-modal__foot').insertBefore(delBtn, $('blk-err'));

  initThemeToggle();
  renderAll();

  window.addEventListener('beforeunload', function (ev) {
    if (dirty()) { ev.preventDefault(); ev.returnValue = ''; }
  });
}

// CDP/自動化測試鉤子
window.__se = {
  state: state,
  blocksById: blockById,
  addTask: function (name) {
    state.project.tasks.push({ name: name, desc: '', steps: [] });
    renderAll();
  },
  addStep: addStep,
  currentProject: currentProject,
  validate: doValidate,
  exportText: function () {
    return postJson('/api/export', { project: currentProject() }).then(unwrap);
  }
};

document.addEventListener('DOMContentLoaded', init);
