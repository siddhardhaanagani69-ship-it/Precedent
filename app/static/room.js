/* One small browser client. All dynamic text is inserted as text, never HTML. */
const $ = (selector) => document.querySelector(selector);
const room = $('#council-room');
const councilId = room?.dataset.councilId;
const csrf = $('meta[name="csrf-token"]').content;
const pct = (value) => `${Math.round(value * 100)}%`;
const seenTurns = new Set();
const agentCards = new Map();
let terminalReads = 0;

async function api(path, options = {}) {
  const response = await fetch(path, {signal: AbortSignal.timeout(10000), ...options,
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function citationText(node, text) {
  node.replaceChildren();
  const pattern = /\[([SE]\d+)\]/g;
  let offset = 0;
  for (const match of String(text).matchAll(pattern)) {
    node.append(document.createTextNode(text.slice(offset, match.index)));
    const button = element('button', 'citation', match[1]);
    button.type = 'button'; button.setAttribute('aria-label', `Read source ${match[1]}`);
    button.addEventListener('click', () => showReceipt(match[1]));
    node.append(button); offset = match.index + match[0].length;
  }
  node.append(document.createTextNode(String(text).slice(offset)));
}
async function showReceipt(label) {
  $('#receipt-title').textContent = `Source ${label}`;
  $('#receipt-body').textContent = 'Loading the source…';
  const link = $('#receipt-link'); link.hidden = true; link.removeAttribute('href');
  if (!$('#receipt-dialog').open) $('#receipt-dialog').showModal();
  try {
    const receipt = await api(`/api/councils/${councilId}/cite/${label}`);
    $('#receipt-body').textContent = receipt.summary;
    const url = new URL(receipt.url);
    if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) {
      link.href = url.href; link.hidden = false;
    }
  } catch { $('#receipt-body').textContent = 'This source could not be loaded. Please try again.'; }
}
async function health() {
  const node = $('#worker-status');
  try {
    const status = await api('/api/health');
    node.textContent = status.worker_online ? 'Worker connected' : 'Worker offline';
    node.className = `connection ${status.worker_online ? 'online' : 'offline'}`;
    node.title = status.worker_online ? 'Ready to run councils' : 'Start python -m engine.worker in a separate terminal.';
  } catch { node.textContent = 'Connection interrupted'; node.className = 'connection offline'; }
}
function renderAgents(agents, options) {
  if (!agents.length) return;
  $('#agents-empty')?.remove();
  const leader = [...options].sort((a, b) => agents.reduce((sum, agent) => sum + ((agent.stance[b.id] || 0) - (agent.stance[a.id] || 0)) * agent.story_count, 0))[0];
  if (!leader) return;
  $('#meter-label').textContent = `Computed preference for “${leader.label}”`;
  for (const agent of agents) {
    let card = agentCards.get(agent.id);
    if (!card) {
      card = element('article', 'agent-card');
      card.style.setProperty('--agent-color', agent.color);
      card.append(element('h3', '', agent.name));
      const meta = element('div', 'agent-meta');
      meta.append(element('span', '', `Based on ${agent.story_count} stories`), element('span', 'family', agent.family));
      const value = element('div', 'stance-value');
      value.append(element('span', 'meter-option'), element('strong', 'meter-number'));
      const meter = element('div', 'meter');
      meter.setAttribute('role', 'meter'); meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
      meter.append(element('div', 'meter-fill'), element('span', 'meter-previous'));
      card.append(meta, value, meter); $('#agents').append(card); agentCards.set(agent.id, card);
    }
    const preference = agent.stance[leader.id];
    card.querySelector('.meter-option').textContent = leader.label;
    card.querySelector('.meter-number').textContent = pct(preference);
    const meter = card.querySelector('.meter');
    meter.setAttribute('aria-label', `${agent.name}: preference for ${leader.label}`);
    meter.setAttribute('aria-valuenow', String(Math.round(preference * 100)));
    card.querySelector('.meter-fill').style.width = pct(preference);
    const previous = card.querySelector('.meter-previous');
    previous.hidden = agent.prev_stance[leader.id] === undefined;
    previous.style.left = pct(agent.prev_stance[leader.id] ?? preference);
  }
}
function renderTurns(turns, agents) {
  const byId = Object.fromEntries(agents.map(agent => [agent.id, agent]));
  for (const turn of turns) {
    if (seenTurns.has(turn.id)) continue;
    seenTurns.add(turn.id); $('#transcript-empty')?.remove();
    const node = element('article', `turn ${turn.kind}`);
    const agent = byId[turn.agent_id];
    if (agent) node.style.setProperty('--agent-color', agent.color);
    if (turn.kind !== 'system') {
      const byline = element('div', 'turn-byline');
      byline.append(element('span', '', agent?.name || 'Moderator'), element('span', '', turn.round ? `Round ${turn.round}` : ''));
      node.append(byline);
    }
    const message = element('p'); citationText(message, turn.message); node.append(message);
    $('#transcript').append(node);
  }
}
function renderVerdict(verdict) {
  if (!verdict) return;
  $('#verdict').hidden = false;
  $('#verdict-title').textContent = verdict.recommendation;
  $('#confidence').textContent = verdict.confidence === null ? 'Insufficient evidence' : `${pct(verdict.confidence)} computed confidence`;
  citationText($('#verdict-summary'), verdict.summary);
  citationText($('#verdict-crux'), verdict.crux);
  citationText($('#verdict-dissent'), verdict.dissent.message ? `${verdict.dissent.agent}: ${verdict.dissent.message}` : 'Not enough evidence to form opposing cohorts.');
  citationText($('#verdict-test'), verdict.cheap_test);
  const receipts = $('#verdict-receipts'); receipts.replaceChildren();
  if (verdict.receipts.length) receipts.append(element('span', '', 'The receipts:'));
  for (const receipt of verdict.receipts) {
    const holder = element('span'); citationText(holder, `[${receipt.label}]`); receipts.append(holder);
  }
}
function render(data) {
  const {council, agents, turns, evidence, verdict} = data;
  const descriptions = {queued:'Your decision is in the queue. The worker will pick it up shortly.', planning:'Defining the options and what could change the outcome.', scouting:'Looking for people who made this choice and wrote about what happened.', mining:'Reading accounts and keeping relevant firsthand outcomes.', forming:'Grouping experiences and calculating each cohort’s priorities.', debating:'The council is comparing experiences. Every cited story has a receipt.', awaiting_user:'The council is waiting for your answer.', answered:'Your answer is ready for the worker.', finalizing:'Weighing the evidence and writing the verdict.', done:'The council has finished. Read the discussion and its recommendation below.', failed:'This run could not finish. The available conversation is saved below.'};
  $('#stage-description').textContent = descriptions[council.status] || council.status;
  const stages = [...document.querySelectorAll('[data-stage]')];
  const current = stages.findIndex(node => node.dataset.stage === council.status);
  stages.forEach((node, i) => {node.className = i === current ? 'current' : i < current ? 'complete' : ''; if (i === current) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');});
  const p = council.progress;
  $('#progress-counts').textContent = `${p.stories_found} sources found → ${p.stories_kept} ${p.stories_kept === 1 ? 'story' : 'stories'} kept → ${p.cohorts} voices · Round ${p.round} of 3`;
  if (!agents.length && ['done', 'failed'].includes(council.status)) {
    $('#agents-empty').textContent = 'This run did not find enough substantial cohorts to seat a council.';
    $('#meter-label').textContent = 'No debate was held.';
  }
  renderAgents(agents, council.plan.options || []); renderTurns(turns, agents); renderVerdict(verdict);
  $('#evidence-count').textContent = evidence.length;
  if (evidence.length) {
    $('#evidence').replaceChildren();
    for (const item of evidence) {const node = element('div', 'evidence-item'); citationText(node, `${item.title} [${item.label}]`); $('#evidence').append(node);}
  }
  $('#failure').hidden = council.status !== 'failed';
  $('#failure-message').textContent = council.error || '';
  const time = p.first_argument_at ? `${Math.round((Date.parse(p.first_argument_at) - Date.parse(p.started_at)) / 1000)}s` : '—';
  const metrics = [[time, 'To first argument'], [p.stories_kept, 'Stories kept'], [p.llm_calls, 'Model calls'], [p.valid_citation_pct === null ? '—' : `${Math.round(p.valid_citation_pct)}%`, 'Valid citations'], [p.verifications, 'Live verifications']];
  $('#metrics').replaceChildren(...metrics.map(([value,label]) => {const node = element('div'); node.append(element('strong', '', value), document.createTextNode(label)); return node;}));
  return ['done', 'failed'].includes(council.status);
}
async function poll() {
  try {
    const [data] = await Promise.all([api(`/api/councils/${councilId}/state`), health()]);
    $('#connection-error').hidden = true;
    if (render(data)) terminalReads += 1;
    else terminalReads = 0;
  } catch (error) {$('#connection-error').textContent = `${error.message} Reconnecting…`; $('#connection-error').hidden = false;}
  if (terminalReads < 2) setTimeout(poll, 1000);
}
document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => {$('#decision').value = button.dataset.example; $('#decision').focus();}));
$('#decision-form')?.addEventListener('submit', (event) => {const button = event.currentTarget.querySelector('[type="submit"]'); button.disabled = true; button.textContent = 'Opening your council…';});
$('#rerun')?.addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try {const result = await api(`/api/councils/${councilId}/rerun`, {method:'POST', body:'{}'}); location.assign(result.url);}
  catch (error) {$('#failure-message').textContent = error.message; $('#rerun').disabled = false;}
});
if (room) poll();
else {health(); setInterval(health, 10000);}
