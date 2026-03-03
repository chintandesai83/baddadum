/* ====================================================================
   LinkedIn Job Matcher – Frontend Logic
   ==================================================================== */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  step: 1,              // 1=input 2=loading 3=profile 4=prefs 5=results
  rawProfile: null,     // JSON from /api/extract
  file: null,           // File object
  jobs: [],             // Ranked jobs from /api/search
};

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupDropZone();
  setupTagsInput();
  setupEventListeners();
  renderStep(1);
});

// ---------------------------------------------------------------------------
// Step rendering
// ---------------------------------------------------------------------------
function renderStep(n) {
  state.step = n;
  document.querySelectorAll('.screen').forEach(el => el.classList.add('hidden'));
  const screen = document.getElementById(`screen-${n}`);
  if (screen) screen.classList.remove('hidden');
  updateStepBar(n);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function updateStepBar(active) {
  const steps = document.querySelectorAll('.step');
  steps.forEach((el, i) => {
    const n = i + 1;
    el.classList.toggle('active', n === active);
    el.classList.toggle('done', n < active);
    const circle = el.querySelector('.step-circle');
    if (n < active) {
      circle.innerHTML = '✓';
    } else {
      circle.textContent = n;
    }
  });
}

// ---------------------------------------------------------------------------
// Tabs (Input step)
// ---------------------------------------------------------------------------
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${target}`).classList.add('active');
    });
  });
}

// ---------------------------------------------------------------------------
// Drop zone
// ---------------------------------------------------------------------------
function setupDropZone() {
  const zone = document.getElementById('drop-zone');
  const input = document.getElementById('file-input');

  zone.addEventListener('click', () => input.click());

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });

  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));

  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  });

  input.addEventListener('change', () => {
    if (input.files[0]) setFile(input.files[0]);
  });
}

function setFile(file) {
  const allowed = ['.pdf', '.docx', '.txt'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!allowed.includes(ext)) {
    showError('file-error', 'Only PDF, DOCX, and TXT files are supported.');
    return;
  }
  state.file = file;
  document.getElementById('file-preview').classList.remove('hidden');
  document.getElementById('file-name').textContent = file.name;
  clearError('file-error');
}

function clearFile() {
  state.file = null;
  document.getElementById('file-preview').classList.add('hidden');
  document.getElementById('file-input').value = '';
}

// ---------------------------------------------------------------------------
// Tags input (preferred job titles)
// ---------------------------------------------------------------------------
const tags = [];

function setupTagsInput() {
  const wrapper = document.getElementById('tags-wrapper');
  const input   = document.getElementById('tags-input');

  input.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
      e.preventDefault();
      addTag(input.value.trim());
      input.value = '';
    } else if (e.key === 'Backspace' && !input.value && tags.length) {
      removeTag(tags.length - 1);
    }
  });

  wrapper.addEventListener('click', () => input.focus());
}

function addTag(text) {
  if (tags.includes(text) || tags.length >= 8) return;
  tags.push(text);
  renderTags();
}

function removeTag(idx) {
  tags.splice(idx, 1);
  renderTags();
}

function renderTags() {
  const wrapper = document.getElementById('tags-wrapper');
  const input   = document.getElementById('tags-input');
  wrapper.querySelectorAll('.tag').forEach(el => el.remove());
  tags.forEach((t, i) => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.innerHTML = `${t} <button type="button" onclick="removeTag(${i})" aria-label="Remove">×</button>`;
    wrapper.insertBefore(tag, input);
  });
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
function setupEventListeners() {
  document.getElementById('btn-analyze').addEventListener('click', handleAnalyze);
  document.getElementById('btn-back-to-input').addEventListener('click', () => renderStep(1));
  document.getElementById('btn-search').addEventListener('click', handleSearch);
  document.getElementById('btn-back-to-prefs').addEventListener('click', () => renderStep(4));
  document.getElementById('btn-new-search').addEventListener('click', () => renderStep(1));
}

// ---------------------------------------------------------------------------
// Step 1 → 2/3: Extract profile
// ---------------------------------------------------------------------------
async function handleAnalyze() {
  clearAllErrors();

  const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
  const fd = new FormData();
  let hasInput = false;

  if (activeTab === 'file') {
    if (!state.file) { showError('file-error', 'Please select a file.'); return; }
    fd.append('file', state.file);
    hasInput = true;
  } else if (activeTab === 'url') {
    const url = document.getElementById('linkedin-url').value.trim();
    if (!url) { showError('url-error', 'Please enter a LinkedIn URL.'); return; }
    if (!url.includes('linkedin.com')) { showError('url-error', 'Enter a valid LinkedIn profile URL.'); return; }
    fd.append('linkedin_url', url);
    hasInput = true;
  } else {
    const text = document.getElementById('profile-text').value.trim();
    if (text.length < 80) { showError('text-error', 'Please paste at least a few sentences of your profile.'); return; }
    fd.append('profile_text', text);
    hasInput = true;
  }

  if (!hasInput) return;

  renderStep(2);
  setLoadingMsg('Extracting your profile information…');

  try {
    const res = await fetch('/api/extract', { method: 'POST', body: fd });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || 'Extraction failed.');

    state.rawProfile = data.profile;
    renderProfileReview(data.profile);
    renderStep(3);
  } catch (err) {
    renderStep(1);
    showGlobalError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Step 3: Profile review
// ---------------------------------------------------------------------------
function renderProfileReview(p) {
  // Avatar initials
  const name = p.name || 'You';
  document.getElementById('avatar-initials').textContent = name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  document.getElementById('profile-name').textContent = name;
  document.getElementById('profile-title').textContent = p.current_title || 'Professional';
  document.getElementById('profile-exp').textContent =
    p.experience_years ? `${p.experience_years} years of experience` : '';
  document.getElementById('profile-summary').textContent = p.summary || '';

  // Skills
  const skillsEl = document.getElementById('profile-skills');
  skillsEl.innerHTML = (p.skills || []).slice(0, 15).map(s => `<span class="skill-tag">${esc(s)}</span>`).join('');

  // Experience
  const expEl = document.getElementById('profile-experience');
  expEl.innerHTML = (p.experience || []).slice(0, 4).map(e => `
    <div style="margin-bottom:0.85rem">
      <div style="font-weight:700;font-size:0.9rem">${esc(e.title || '')}</div>
      <div style="color:var(--text-muted);font-size:0.82rem">${esc(e.company || '')} · ${esc(e.duration || '')}</div>
      ${e.description ? `<div style="font-size:0.82rem;margin-top:0.2rem">${esc(e.description)}</div>` : ''}
    </div>`).join('');

  // Education
  const eduEl = document.getElementById('profile-education');
  eduEl.innerHTML = (p.education || []).slice(0, 2).map(e => `
    <div style="font-size:0.875rem;margin-bottom:0.5rem">
      <strong>${esc(e.degree || '')} ${e.field ? 'in ' + esc(e.field) : ''}</strong><br>
      <span style="color:var(--text-muted)">${esc(e.institution || '')}${e.year ? ' · ' + esc(e.year) : ''}</span>
    </div>`).join('') || '<span class="text-muted">Not found</span>';

  // Pre-fill preferred titles from AI suggestions
  (p.desired_job_titles || []).slice(0, 3).forEach(t => { if (!tags.includes(t)) addTag(t); });
}

// ---------------------------------------------------------------------------
// Step 4 → 5: Search jobs
// ---------------------------------------------------------------------------
async function handleSearch() {
  const location = document.getElementById('city').value.trim();
  const country  = document.getElementById('country').value.trim();
  const jobType  = document.querySelector('input[name="job-type"]:checked')?.value || 'any';

  renderStep(2);
  setLoadingMsg('Searching hundreds of job listings for you…');

  const body = {
    profile: state.rawProfile,
    location,
    country,
    job_type: jobType,
    job_titles: [...tags],
    additional_preferences: document.getElementById('extra-prefs').value.trim(),
  };

  try {
    setLoadingMsg('Scoring and ranking your top matches with AI…');
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Search failed.');

    state.jobs = data.jobs || [];
    renderResults(state.jobs, data.total_found || 0, data.message);
    renderStep(5);
  } catch (err) {
    renderStep(4);
    showGlobalError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Step 5: Results
// ---------------------------------------------------------------------------
function renderResults(jobs, total, message) {
  const container = document.getElementById('jobs-container');
  const header    = document.getElementById('results-header');

  if (!jobs.length) {
    header.textContent = 'No matches found';
    container.innerHTML = `<div class="alert alert-info">ℹ️ ${esc(message || 'No jobs found. Try adjusting your preferences.')}</div>`;
    return;
  }

  header.innerHTML = `Top ${jobs.length} Matches <span class="results-count">(from ${total} listings)</span>`;
  container.innerHTML = jobs.map((job, i) => renderJobCard(job, i + 1)).join('');
}

function renderJobCard(job, rank) {
  const score    = job.match_score ?? 0;
  const scoreClass = score >= 75 ? 'high' : score >= 50 ? 'mid' : 'low';
  const cardClass  = score >= 75 ? 'score-high' : score >= 50 ? 'score-mid' : 'score-low';
  const typeClass  = (job.job_type || 'onsite').toLowerCase();
  const typeLabel  = typeClass.charAt(0).toUpperCase() + typeClass.slice(1);
  const applyUrl   = job.url || '#';

  const meta = [
    job.company    ? `🏢 ${esc(job.company)}`    : '',
    job.location   ? `📍 ${esc(job.location)}`   : '',
    job.posted_at  ? `🗓 ${formatDate(job.posted_at)}` : '',
  ].filter(Boolean).join(' ');

  return `
<div class="job-card ${cardClass}">
  <div class="job-rank">#${rank}</div>
  <div class="match-badge ${scoreClass}">
    ${score}%
    <small>match</small>
  </div>
  <a href="${applyUrl}" target="_blank" rel="noopener noreferrer" class="job-title">
    ${esc(job.title || 'Untitled Position')}
  </a>
  <div class="job-meta">${meta}</div>
  ${job.description ? `<p class="job-description">${esc(job.description).slice(0, 280)}…</p>` : ''}
  ${job.match_reason ? `<div class="job-reason">✅ ${esc(job.match_reason)}</div>` : ''}
  <div class="job-footer">
    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center">
      <span class="source-badge">${esc(job.source || 'Job Board')}</span>
      <span class="job-type-badge ${typeClass}">${typeLabel}</span>
      ${job.salary ? `<span class="salary-badge">💰 ${esc(job.salary)}</span>` : ''}
    </div>
    <a href="${applyUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-primary" style="padding:0.45rem 1rem;font-size:0.82rem">
      Apply →
    </a>
  </div>
</div>`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function setLoadingMsg(msg) {
  document.getElementById('loading-msg').textContent = msg;
}

function showError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}

function clearError(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

function clearAllErrors() {
  document.querySelectorAll('.alert-error').forEach(el => {
    el.textContent = '';
    el.classList.add('hidden');
  });
  document.getElementById('global-error')?.classList.add('hidden');
}

function showGlobalError(msg) {
  const el = document.getElementById('global-error');
  if (!el) return;
  el.textContent = '⚠️ ' + msg;
  el.classList.remove('hidden');
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}
