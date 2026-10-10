'use strict';

const $ = (id) => document.getElementById(id);
const app = {state: null, token: null, phone: null, simPhone: '+12025550101', view: 'inbox', privacy: true, busy: false, lastEvent: null, businessDirty: false, authenticated: false, mode: 'demo', realOutbound: false};
const viewCopy = {
  inbox: ['Inbox', 'Your reception desk, organized.', 'Keep every inquiry in view. Give your team a clear next step.'],
  appointments: ['Appointment requests', 'From inquiry to a clear request.', 'Collect the details. Let the business confirm the appointment.'],
  consent: ['Consent records', 'Customer preferences, respected.', 'See what was agreed to, where it came from, and what changed.'],
  business: ['Business profile', 'Good answers start with good facts.', 'Keep the information your reception desk is allowed to share up to date.'],
  outbox: ['Delivery queue', 'Every outgoing message, accounted for.', 'Track delivery outcomes. Pause sending whenever your team needs to review.']
};
const purposes = ['sms_inquiry', 'appointment_request', 'missed_call_followup'];
const scenarioDefinitions = [
  ['faq', 'Business hours', '◷'], ['booking', 'Incomplete request', '▦'],
  ['complete', 'Full request', '✓'], ['outside', 'Outside service area', '⌁'],
  ['handoff', 'Human takeover', '♧'], ['no-consent', 'Call · no consent', '○'],
  ['consent', 'Call · with consent', '✓'], ['stop', 'STOP & block', '⊘'],
  ['start', 'START preference', '↻'], ['duplicate', 'Duplicate event', '⧉'],
  ['failure', 'Delivery failure', '!'], ['unavailable', 'Model unavailable', '◇']
];

function clean(value) {
  const text = typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
  if (!app.privacy) return text;
  const qualifier = isService() ? 'private' : 'demo';
  return text
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, `[${qualifier} contact]`)
    .replace(/https?:\/\/[^\s]+/gi, `[${qualifier} reference]`)
    .replace(/\+\d[\d\s().-]{8,}\d/g, `[${qualifier} number]`)
    .replace(/\b\d{3}[-. ]\d{3}[-. ]\d{4}\b/g, `[${qualifier} number]`)
    .replace(/\b(?:SM|CA|AC)[0-9a-fA-F]{32}\b/g, `[${qualifier} provider identifier]`)
    .replace(/\b[\w.-]+\.example\.test\b/gi, `[${qualifier} reference]`);
}
function isService() { return app.mode === 'service'; }
function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value != null) element.textContent = clean(value);
  return element;
}
function badge(value, type = 'neutral') { return node('span', `badge ${type}`, value); }
function initials(name) { return String(name || '?').split(/\s+/).slice(0, 2).map(s => s[0]).join('').toUpperCase(); }
function pretty(value) { return String(value || '').replace(/[_-]/g, ' ').replace(/\b\w/g, s => s.toUpperCase()); }
function contactFor(phone) { return app.state?.contacts?.find(c => c.phone === phone) || {phone, name: isService() ? 'Customer' : 'Demo customer', consent_status: 'none', handoff: false}; }
function contactName(phone) { return contactFor(phone).name || (app.privacy ? isService() ? 'Customer' : 'Demo customer' : phone); }
function time(value, full = false) {
  if (!value) return 'Just now';
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return 'Time unavailable';
  return full ? d.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}) : d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
}
function errorMessage(error) {
  const messages = {
    operator_sign_in_required: 'Sign in to open your workspace.',
    invalid_credentials: 'The password was not accepted. Check it and try again.',
    login_rate_limited: 'Too many sign-in attempts. Wait five minutes before trying again.',
    session_limit: 'Too many operator sessions are active. Try again after an existing session expires.',
    csrf_token_invalid: 'Session verification failed. Refresh the page and sign in again.',
    host_not_allowed: 'This workspace address is not approved. Open the configured service address.',
    cross_origin_blocked: 'The request came from a different site. Open this workspace directly and try again.',
    cross_site_blocked: 'The request came from a different site. Open this workspace directly and try again.',
    invalid_request_check_fields_and_limits: 'Check the entered fields and configured limits, then try again.',
    reply_source_required: 'Select a verified customer inquiry before replying.',
    reply_source_not_eligible: 'That inquiry cannot authorize a reply. Review its verification and deadline.',
    service_inquiry_confirmation_required: 'Review the selected inquiry and confirm that your reply relates to the customer’s service question.',
    service_reply_window_expired: 'The original service reply window has expired. A fresh verified customer inquiry is required.',
    source_not_verified: 'The customer inquiry has not passed provider verification.',
    source_unavailable: 'The customer inquiry is no longer available as a reply source.',
    source_predates_opt_out: 'That inquiry came before the withdrawal. It cannot restore reply permission.',
    opted_out: 'The customer has opted out. This reply remains blocked.',
    contact_blocked: 'A contact or carrier block prevents this reply.',
    service_operation_unavailable_check_audit: 'The action is temporarily unavailable. Check recent activity before trying again.'
  };
  return messages[error?.message] || error?.message || 'This action could not be completed. Refresh the workspace and try again.';
}
function showError(error) { if (!app.authenticated) { $('login-error').textContent = errorMessage(error); $('login-error').hidden = false; return; } $('global-error').textContent = clean(errorMessage(error)); $('global-error').hidden = false; }
function clearError() { $('global-error').hidden = true; }
let toastTimer;
function toast(message) {
  $('action-toast').textContent = clean(message);
  $('action-toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('action-toast').hidden = true; }, 4800);
}
async function request(path, body) {
  const headers = {'Accept': 'application/json'};
  const options = {headers, cache: 'no-store'};
  if (body !== undefined) { options.method = 'POST'; headers['Content-Type'] = 'application/json'; if (app.token) headers['X-Demo-Token'] = app.token; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('The workspace server is unavailable. Check that the service is running, then refresh this page.'); }
  let data;
  try { data = await response.json(); } catch { throw new Error('The server returned an unreadable response. Refresh the workspace and try again.'); }
  if (!response.ok) {
    const error = new Error(data.error || data.message || `The action was rejected (${response.status}). Refresh the workspace and try again.`); error.status = response.status;
    if (response.status === 401) lockWorkspace();
    throw error;
  }
  return data;
}
async function refresh() {
  app.state = await request('/api/state');
  if (app.state.runtime?.mode) app.mode = app.state.runtime.mode === 'service' ? 'service' : 'demo';
  app.realOutbound = app.state.runtime?.real_outbound === true;
  if (!app.phone || !(app.state.conversations || []).some(c => c.phone === app.phone)) app.phone = app.state.conversations?.[0]?.phone || app.state.contacts?.[0]?.phone || null;
  render();
  $('last-updated').textContent = `Updated ${time(new Date().toISOString())} · ${isService() ? app.realOutbound ? 'Service workspace' : 'Service dry run' : 'Local demo'}`;
}
async function perform(action, success) {
  if (app.busy) return;
  app.busy = true;
  clearError();
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try { const result = await action(); await refresh(); if (success) toast(success); return result; }
  catch (error) { showError(error); }
  finally { app.busy = false; document.querySelectorAll('button').forEach(button => { button.disabled = button.dataset.locked === 'true'; }); }
}
function empty(container, title, description) {
  const box = node('div', 'empty-state'); box.append(node('span', 'empty-mark', '▤'), node('strong', '', title), node('p', '', description)); container.append(box);
}
function setView(view, historyMode = 'push') {
  if (!viewCopy[view]) view = 'inbox';
  if (view === 'outbox' && !isService()) view = 'inbox';
  app.view = view;
  document.querySelectorAll('[data-view]').forEach(button => { const active = button.dataset.view === view; button.classList.toggle('active', active); button.setAttribute('aria-current', active ? 'page' : 'false'); });
  for (const key of Object.keys(viewCopy)) $(`view-${key}`).hidden = key !== view;
  const [crumb, title, description] = viewCopy[view];
  $('breadcrumb-view').textContent = crumb; $('page-title').textContent = title; $('page-description').textContent = description;
  if (view === 'business' && !app.businessDirty && app.state) renderBusiness();
  const hash = `#${view}`;
  if (historyMode === 'push' && location.hash !== hash) history.pushState(null, '', hash);
  else if (historyMode === 'replace' && location.hash !== hash) history.replaceState(null, '', hash);
}
function render() {
  const s = app.state;
  renderRuntime();
  $('metric-contacts').textContent = s.metrics?.contacts ?? s.conversations?.length ?? 0;
  $('metric-appointments').textContent = s.metrics?.appointments ?? s.appointments?.length ?? 0;
  $('metric-handoffs').textContent = s.metrics?.handoffs ?? s.contacts?.filter(c => c.handoff).length ?? 0;
  $('metric-outbound').textContent = isService() ? (s.conversations || []).reduce((total, c) => total + (c.messages || []).filter(m => m.direction === 'outbound').length, 0) : s.metrics?.simulated_outbound ?? 0;
  $('nav-inbox-count').textContent = s.conversations?.length ?? 0;
  $('conversation-count').textContent = s.conversations?.length ?? 0;
  const model = s.business?.model_mode || 'rules';
  $('mode-label').textContent = isService() ? 'Rules only · No AI model' : model === 'rules' ? 'Rule-based · No live AI' : 'Mock understanding · No live AI';
  renderContacts(); renderConversations(); renderConversation(); renderAppointments(); renderConsent(); renderActivity(); renderOutbox();
  if (!app.businessDirty) renderBusiness();
}
function renderRuntime() {
  const service = isService(); const paused = app.state?.outbox?.paused === true;
  const restoreReview = service && app.state?.outbox?.restore_review_required === true;
  const intakeCounts = app.state.inbound_processing?.counts || {};
  const intakeIssues = Number(intakeCounts.verification_failed || 0) + Number(intakeCounts.rejected || 0);
  document.body.classList.toggle('service-mode', service);
  $('runtime-pill').textContent = service ? app.realOutbound ? 'SERVICE / LIVE' : 'SERVICE / DRY RUN' : 'DEMO / SIMULATED';
  $('runtime-pill').classList.toggle('live-pill', service && app.realOutbound);
  $('queue-nav').hidden = !service; $('logout-button').hidden = !service; $('queue-pause-button').hidden = !service;
  $('queue-pause-button').textContent = restoreReview ? 'Restore review required' : paused ? 'Resume sending' : 'Pause sending';
  $('queue-pause-button').className = `button ${paused ? 'button-teal' : 'button-danger'}`;
  $('queue-pause-button').dataset.locked = String(restoreReview); $('queue-pause-button').disabled = restoreReview;
  $('reset-button').hidden = service;
  document.querySelector('.simulator-panel').hidden = service || app.state.runtime?.simulator_enabled === false;
  $('metric-outbound-label').textContent = service ? 'Outbound messages' : 'Simulated replies';
  $('metric-outbound-note').textContent = service ? 'Check delivery status separately' : 'No real messages sent';
  const notice = $('runtime-notice'); notice.replaceChildren();
  if (service) {
    notice.append(node('strong', '', restoreReview ? 'Restore review required. ' : paused ? 'Sending is paused. ' : app.realOutbound ? 'Live sending is enabled. ' : 'Service dry run. '), node('span', '', restoreReview ? 'Sending remains blocked until an offline reconciliation is recorded.' : paused ? 'Queued work is held for review. This control cannot recall messages already submitted to the provider.' : app.realOutbound ? 'Live delivery readiness has not been verified by this dashboard. Monitor the delivery queue before relying on it.' : 'Real outbound is disabled. Customer records may contain real data; handle them privately.'));
    if (intakeIssues > 0) notice.append(node('span', '', ' Inbound processing issues need review in the Delivery queue.'));
    const recipientPolicy = app.state.runtime?.recipient_policy;
    notice.append(node('span', 'recipient-policy', recipientPolicy === 'inbound_service' ? 'Recipient policy: Inbound service. Replies require a verified, current customer inquiry; unrelated outreach is blocked.' : recipientPolicy === 'test_allowlist' ? 'Recipient policy: Controlled test. Replies are limited to approved test recipients.' : 'Recipient policy is unavailable. Check the service configuration before sending.'));
  } else notice.append(node('strong', '', 'A safe space to explore. '), node('span', '', 'All people, numbers, and business details are synthetic. Replies are simulated; real outbound is disabled.'));
  const sideNote = document.querySelector('.sidebar-note');
  sideNote.querySelector('.small-caption').textContent = service ? 'OPERATOR WORKSPACE' : 'BUILT FOR SMALL TEAMS';
  sideNote.querySelector('p').textContent = service ? 'Approved answers, request collection, and delivery review for your team.' : 'Answers, request collection, and a clear handoff to your team.';
  sideNote.lastChild.textContent = service ? ' Service workspace' : ' Local demonstration';
  document.querySelector('.sidebar-foot small').textContent = service ? 'Single-business workspace' : 'Fictional cleaning business';
  document.querySelector('.list-foot').replaceChildren(node('span', 'status-dot'), node('span', '', service ? 'Customer conversations' : 'Synthetic contacts only'));
  $('workspace-footnote').textContent = service ? 'Operator workspace · Delivery status is not a read receipt' : 'Personal demonstration project · Not a commercial client case study';
  document.querySelector('.form-actions span').textContent = service ? 'Changes affect approved answers for this service.' : 'Local demo configuration only.';
  if (service && app.state.business?.name) {
    document.querySelector('.page-heading .eyebrow').textContent = app.state.business.name.toUpperCase();
    document.querySelector('.sidebar-foot>div').firstChild.textContent = app.state.business.name;
  }
}
function renderContacts() {
  const select = $('sim-contact'); const previous = app.simPhone;
  select.replaceChildren();
  for (const contact of app.state.contacts || []) {
    const option = node('option', '', `${contact.name || 'Demo customer'}${app.privacy ? '' : ` · ${contact.phone}`}`); option.value = contact.phone; select.append(option);
  }
  const custom = node('option', '', 'New synthetic customer'); custom.value = 'custom'; select.append(custom);
  if ([...select.options].some(o => o.value === previous)) select.value = previous;
  else select.value = 'custom';
  if (select.value !== 'custom') app.simPhone = select.value;
  updatePhoneField();
}
function updatePhoneField() {
  const input = $('sim-phone');
  $('sim-phone-fields').hidden = app.privacy;
  input.readOnly = app.privacy;
  input.type = app.privacy ? 'text' : 'tel';
  input.value = app.privacy ? `[demo number · ${app.simPhone.slice(-4)}]` : app.simPhone;
  input.setAttribute('aria-label', app.privacy ? 'Synthetic phone number hidden for showcase privacy' : 'Synthetic phone number');
}
function renderConversations() {
  const list = $('conversation-items'); list.replaceChildren();
  const search = $('search-conversations').value.trim().toLowerCase();
  const conversations = (app.state.conversations || []).filter(c => `${c.name} ${c.phone}`.toLowerCase().includes(search));
  if (!conversations.length) { empty(list, 'No conversations found', search ? 'Try a different name or number.' : isService() ? 'New customer inquiries will appear here.' : 'Send a simulated message to begin.'); return; }
  for (const conversation of conversations) {
    const messages = conversation.messages || []; const last = messages[messages.length - 1];
    const row = node('button', `conversation-row${conversation.phone === app.phone ? ' selected' : ''}`); row.type = 'button'; row.setAttribute('aria-label', `Open conversation with ${conversation.name || 'demo customer'}`); row.setAttribute('aria-pressed', String(conversation.phone === app.phone));
    const avatar = node('span', 'avatar', initials(conversation.name)); const copy = node('div', 'conversation-copy'); const top = node('div', 'conversation-top');
    top.append(node('span', 'conversation-name', conversation.name || contactName(conversation.phone)), node('span', 'conversation-time', last ? time(last.created_at) : ''));
    copy.append(top, node('div', 'conversation-preview', last?.text || (isService() ? 'Ready for a customer inquiry' : 'Ready for a simulated inquiry')));
    const tags = node('div', 'conversation-tags');
    if (conversation.handoff) tags.append(badge('With team', 'amber'));
    else if (conversation.consent_status === 'revoked') tags.append(badge('Opted out', 'revoked'));
    else if (conversation.consent_status === 'active') tags.append(badge('Consent active', 'active'));
    else tags.append(badge('No follow-up consent', 'none'));
    copy.append(tags); row.append(avatar, copy); row.addEventListener('click', () => { app.phone = conversation.phone; app.simPhone = conversation.phone; renderContacts(); renderConversations(); renderConversation(); }); list.append(row);
  }
}
function renderConversation() {
  const header = $('conversation-header'); const status = $('conversation-status'); const list = $('message-list'); const footer = $('conversation-footer');
  header.replaceChildren(); status.replaceChildren(); list.replaceChildren(); footer.replaceChildren();
  const conversation = app.state.conversations?.find(c => c.phone === app.phone);
  if (!conversation) { empty(list, 'A quieter inbox starts here', isService() ? 'Customer inquiries, requests, and handoffs will appear here.' : 'Run a scenario to see answers, requests, and handoffs in one conversation.'); return; }
  const contact = contactFor(app.phone); const title = node('div', 'contact-title'); const titleText = node('div');
  titleText.append(node('h2', '', conversation.name || contact.name || (isService() ? 'Customer' : 'Demo customer')), node('p', '', app.privacy ? isService() ? 'Private customer · SMS conversation' : 'Synthetic customer · SMS conversation' : `${app.phone} · SMS conversation`)); title.append(node('span', 'avatar', initials(conversation.name)), titleText);
  const handoff = node('button', `button ${contact.handoff ? 'button-teal' : 'button-secondary'}`, contact.handoff ? 'Resume assistant' : 'Take over'); handoff.type = 'button'; handoff.addEventListener('click', () => perform(() => request('/api/handoff', {phone: app.phone, active: !contact.handoff}), contact.handoff ? 'Assistant resumed. Consent and sending restrictions still apply.' : 'Your team has control. Automatic replies are paused.'));
  header.append(title, handoff);
  status.append(badge('SMS', 'blue'), badge(contact.consent_status === 'active' ? 'Consent active' : contact.consent_status === 'revoked' ? 'Opted out' : 'No follow-up consent', contact.consent_status));
  status.append(node('span', 'status-text', contact.handoff ? 'Team handling this conversation' : 'Automatic assistance available'));
  const messages = conversation.messages || [];
  if (!messages.length) empty(list, 'Ready when the customer is', isService() ? 'No messages have been recorded for this customer.' : 'Try a customer inquiry in the simulator.');
  else {
    list.append(node('div', 'message-date', isService() ? 'CUSTOMER CONVERSATION' : 'SYNTHETIC DEMO CONVERSATION'));
    for (const message of messages) {
      if (message.direction === 'system') { list.append(node('div', 'system-note', message.text)); continue; }
      const wrapper = node('article', `message ${message.direction}${['failed', 'undelivered', 'uncertain'].includes(message.status) ? ' failed' : ''}`);
      wrapper.append(node('div', 'message-meta', `${message.direction === 'inbound' ? conversation.name || 'Customer' : message.origin === 'human' || message.purpose === 'human_reply' || String(message.event_id || '').startsWith('operator-') ? 'Your team' : 'Reception assistant'} · ${time(message.created_at)}`), node('div', 'message-bubble', message.text));
      if (message.direction === 'outbound') {
        wrapper.append(node('div', 'message-status', deliveryLabel(message)));
        if (isService() && (message.provider_sid || message.error_code)) wrapper.append(node('div', 'provider-detail', `${message.provider_sid ? `Provider ID: ${app.privacy ? '[private identifier]' : message.provider_sid}` : ''}${message.error_code ? ` · Error: ${pretty(message.error_code)}` : ''}`));
      }
      list.append(wrapper);
    }
  }
  if (contact.handoff) {
    const form = node('form', 'human-form'); const input = node('input'); input.placeholder = isService() ? 'Write a team reply…' : 'Write a simulated team reply…'; input.setAttribute('aria-label', 'Human team reply'); input.required = true;
    const send = node('button', 'button button-primary', 'Team reply'); send.type = 'submit';
    const compose = node('div', 'human-compose'); compose.append(input, send);
    let sourceSelect = null; let inquiryConfirmation = null;
    if (isService()) {
      const sourceLabel = node('label', '', 'Customer inquiry for this reply'); sourceLabel.htmlFor = 'reply-source';
      sourceSelect = node('select'); sourceSelect.id = 'reply-source'; sourceSelect.required = true;
      const sources = Array.isArray(contact.reply_sources) ? contact.reply_sources : [];
      const placeholder = node('option', '', 'Select a verified inquiry'); placeholder.value = ''; sourceSelect.append(placeholder);
      for (const source of sources) {
        const option = node('option', '', `${time(source.occurred_at || source.received_at, true)} · ${String(source.text || 'Customer message').slice(0, 95)}${source.eligible ? '' : ` · ${pretty(source.reason || 'Unavailable')}`}`);
        option.value = source.event_id; option.disabled = source.eligible !== true; sourceSelect.append(option);
      }
      const defaultSource = sources.find(source => source.eligible === true && source.event_id === contact.reply_permission_event_id) || sources.find(source => source.eligible === true);
      sourceSelect.value = defaultSource?.event_id || '';
      const evidence = node('div', 'reply-evidence'); evidence.setAttribute('aria-live', 'polite');
      inquiryConfirmation = node('input'); inquiryConfirmation.type = 'checkbox'; inquiryConfirmation.id = 'confirm-service-inquiry';
      const confirmationLabel = node('label', 'reply-confirmation'); confirmationLabel.htmlFor = inquiryConfirmation.id;
      confirmationLabel.append(inquiryConfirmation, node('span', '', 'I reviewed this message as a service inquiry. My reply relates only to this inquiry.'));
      const updateSource = () => {
        const selected = sources.find(source => source.event_id === sourceSelect.value);
        evidence.replaceChildren(); inquiryConfirmation.checked = false;
        const hasExistingBasis = selected?.event_id === contact.reply_permission_event_id && ['rules', 'operator_review_human_only'].includes(contact.reply_permission_basis);
        inquiryConfirmation.required = !hasExistingBasis; confirmationLabel.hidden = hasExistingBasis;
        const locked = !selected || selected.eligible !== true;
        send.dataset.locked = String(locked); send.disabled = locked;
        if (selected) {
          evidence.append(node('p', 'reply-source-text', selected.text), node('p', 'small-hint', `Verified inbound · Received ${time(selected.received_at, true)} · Reply deadline ${time(selected.reply_until, true)}.`));
          evidence.append(node('p', 'small-hint', hasExistingBasis ? contact.reply_permission_basis === 'rules' ? 'The approved service rules established this reply window.' : 'Team review is already recorded for this inquiry. Its original human-only reply deadline still applies.' : 'Confirming this inquiry allows relevant team replies only. It does not enable automatic replies or marketing.'));
        } else {
          evidence.append(node('p', 'small-hint', 'No verified inquiry is currently eligible. Verification, expiry, STOP and carrier restrictions still apply.'));
        }
      };
      sourceSelect.addEventListener('change', updateSource); updateSource();
      form.append(sourceLabel, sourceSelect, evidence, confirmationLabel);
    }
    form.append(compose);
    form.addEventListener('submit', event => {
      event.preventDefault(); const text = input.value.trim();
      if (text) perform(async () => {
        const replyTo = sourceSelect?.value || ''; const confirmed = inquiryConfirmation?.checked === true;
        const replyKey = JSON.stringify([text, replyTo, confirmed]);
        if (form.dataset.replyKey !== replyKey) { form.dataset.replyKey = replyKey; form.dataset.eventId = `operator-${eventId()}`; }
        const payload = {phone: app.phone, text, event_id: form.dataset.eventId};
        if (isService()) { payload.reply_to_event_id = replyTo; payload.confirm_service_inquiry = confirmed; }
        const result = await request('/api/human-reply', payload);
        if (!isService()) showResult(result);
        toast(result.status === 'blocked' ? `Team reply blocked: ${errorMessage({message: result.reason})}` : isService() ? 'Team reply recorded. Check its status in the delivery queue.' : 'Team reply recorded. Real outbound stays disabled.'); return result;
      });
    });
    footer.append(form, node('p', 'small-hint', isService() ? 'The assistant stays paused until you explicitly resume it. Reply windows cannot extend beyond the verified inquiry deadline. STOP, carrier blocks and all sending limits apply.' : 'The assistant stays paused until you explicitly resume it. Consent and opt-out restrictions also apply to team replies.'));
  } else {
    const box = node('div', 'automated-status'); const text = node('div'); text.append(node('strong', '', contact.consent_status === 'revoked' ? 'Customer has opted out' : 'Assistant is ready to help'), node('span', '', contact.consent_status === 'revoked' ? 'Regular replies and missed-call follow-ups are blocked. Preference commands are handled separately.' : 'Approved facts only. Requests wait for owner confirmation.'));
    box.append(node('span', 'status-ring', contact.consent_status === 'revoked' ? '⊘' : '✓'), text); footer.append(box);
  }
  list.scrollTop = list.scrollHeight;
}
function deliveryLabel(message) {
  const status = message.status || 'queued';
  if (!isService()) return status === 'failed' ? `! Simulated delivery failed${message.error_code ? ` · ${pretty(message.error_code)}` : ''}` : '✓ Simulated reply · Not sent';
  if (message.transport === 'dry_run' || status === 'simulated') return status === 'failed' ? 'Dry-run failure · Not sent' : 'Dry-run reply · Not sent';
  return ({queued: 'Queued · Not yet submitted', accepted: 'API accepted · Delivery pending', sending: 'Sending · Delivery pending', sent: 'Sent · Delivery unconfirmed', delivered: 'Delivered · Read status unknown', undelivered: 'Undelivered · Review the error', failed: 'Failed · Review the error', canceled: 'Canceled · Not submitted', uncertain: 'Needs review · Sending outcome uncertain', retry_wait: 'Waiting before a permitted retry'})[status] || pretty(status);
}
function openContact(phone) { app.phone = phone; app.simPhone = phone; setView('inbox'); renderContacts(); renderConversations(); renderConversation(); }
function renderAppointments() {
  const container = $('appointment-items'); container.replaceChildren();
  if (!app.state.appointments?.length) { empty(container, 'A request is a starting point', isService() ? 'Collected service, area, preferred time, and contact details will appear here for owner review.' : 'Run the full request scenario to collect service, area, preferred time, and a contact method.'); return; }
  for (const request of app.state.appointments) {
    const card = node('article', 'request-card'); const heading = node('div', 'request-card-header'); const contact = node('button', '', request.name || contactName(request.phone)); contact.addEventListener('click', () => openContact(request.phone)); heading.append(contact, badge('Pending owner confirmation', 'amber'));
    const grid = node('div', 'request-grid');
    for (const [title, value] of [['SERVICE', request.service], ['SERVICE AREA', request.area], ['PREFERRED TIME', request.desired_time], ['CONTACT METHOD', app.privacy ? 'Provided · Hidden for showcase' : request.contact]]) {
      const field = node('div', 'request-field'); field.append(node('small', '', title), node('p', '', value || 'Needs clarification')); grid.append(field);
    }
    card.append(heading, grid, node('div', 'request-note', 'This is a request, not a confirmed appointment. The business must review availability and reply.')); container.append(card);
  }
}
function renderConsent() {
  const container = $('consent-items'); container.replaceChildren();
  const records = app.state.consent_history || [];
  if (!records.length) { empty(container, 'Consent records appear here', 'Verified sources, purposes, and preference changes are retained for review.'); return; }
  for (const record of records) {
    const card = node('article', 'consent-card'); const header = node('div', 'consent-card-header'); const contact = node('button', '', contactName(record.phone)); contact.addEventListener('click', () => openContact(record.phone)); header.append(contact, badge(pretty(record.action || record.status), record.status === 'active' ? 'active' : record.status === 'revoked' ? 'revoked' : 'neutral'));
    const facts = node('div', 'consent-facts'); facts.append(node('span', '', time(record.occurred_at || record.created_at, true)), node('span', '', `Source: ${pretty(record.source) || 'Recorded source'}`), node('span', '', record.verified ? isService() ? 'Evidence verified' : 'Evidence verified (synthetic)' : 'Not verified'));
    const evidence = node('p', 'consent-evidence', `Evidence: ${typeof record.evidence === 'string' ? record.evidence : record.evidence ? JSON.stringify(record.evidence) : 'See recorded preference command.'}`);
    card.append(header, facts, evidence);
    const chips = node('div'); for (const purpose of record.purposes || []) chips.append(node('span', 'chip', pretty(purpose))); card.append(chips); container.append(card);
  }
}
function renderActivity() {
  const container = $('activity-items'); container.replaceChildren();
  const records = (app.state.audit || []).slice(0, 12);
  $('activity-summary').textContent = records.length ? `${records.length} latest records · ${clean(pretty(records[0].action))}` : 'No activity yet';
  for (const record of records) {
    const row = node('div', 'activity-row'); row.append(node('span', '', time(record.created_at)), node('span', 'activity-action', pretty(record.action)), node('span', '', `${record.phone ? `${contactName(record.phone)} · ` : ''}${typeof record.details === 'string' ? record.details : record.details ? JSON.stringify(record.details) : ''}`)); container.append(row);
  }
}
function renderOutbox() {
  const queue = app.state.outbox || {paused: false, items: [], metrics: {}};
  const items = queue.items || []; const container = $('outbox-items'); container.replaceChildren();
  const counts = queue.counts || queue.metrics || {};
  const count = status => Number(counts[status] ?? items.filter(item => item.status === status).length) || 0;
  $('nav-queue-count').textContent = count('queued') + count('retry_wait') + count('uncertain');
  $('queue-state').textContent = queue.restore_review_required ? 'Restore review required' : queue.paused ? 'Sending paused' : app.realOutbound ? 'Live sending enabled' : 'Dry run · Real outbound disabled';
  $('queue-state').className = `badge ${queue.paused || queue.restore_review_required ? 'amber' : app.realOutbound ? 'active' : 'neutral'}`;
  $('queue-restore-warning').hidden = !queue.restore_review_required;
  renderInboundProcessing();
  const summary = $('queue-counts'); summary.replaceChildren();
  for (const [label, total] of [['Queued', count('queued') + count('retry_wait')], ['Delivery pending', count('accepted') + count('sending') + count('sent')], ['Delivered', count('delivered')], ['Needs review', count('uncertain') + count('failed') + count('undelivered')]]) {
    const metric = node('div', 'queue-count'); metric.append(node('strong', '', String(total)), node('span', '', label)); summary.append(metric);
  }
  if (!items.length) { empty(container, 'The delivery queue is clear', 'Outgoing messages will appear here with their recorded delivery status.'); return; }
  for (const item of items) {
    const card = node('article', 'outbox-card'); const header = node('div', 'outbox-card-header'); const title = node('button', '', contactName(item.phone)); title.addEventListener('click', () => openContact(item.phone));
    const severity = ['uncertain', 'failed', 'undelivered'].includes(item.status) ? 'amber' : item.status === 'delivered' ? 'active' : 'neutral';
    header.append(title, badge(item.status === 'uncertain' ? 'Needs review' : item.status === 'accepted' ? 'API accepted' : pretty(item.status), severity)); card.append(header);
    card.append(node('p', 'outbox-body', item.body || ''), node('p', 'outbox-status', deliveryLabel(item)));
    const facts = node('div', 'outbox-facts');
    facts.append(node('span', '', `${item.origin === 'human' ? 'Your team' : 'Automatic reply'} · ${pretty(item.purpose)}`), node('span', '', `Created ${time(item.created_at, true)}`));
    if (item.updated_at) facts.append(node('span', '', `Updated ${time(item.updated_at, true)}`));
    if (item.segments != null) facts.append(node('span', '', `Estimated segments: ${item.segments}${item.encoding ? ` · ${item.encoding}` : ''}`));
    card.append(facts);
    const identifiers = node('div', 'outbox-identifiers');
    identifiers.append(node('span', '', `Queue item: ${item.id}`), node('span', '', `Message ID: ${app.privacy ? '[private identifier]' : item.message_id || 'Not available'}`));
    if (item.provider_sid) identifiers.append(node('span', '', `Provider ID: ${app.privacy ? '[private identifier]' : item.provider_sid}`));
    if (item.error_code) identifiers.append(node('span', 'outbox-error', `Error: ${pretty(item.error_code)}`));
    card.append(identifiers);
    const actions = node('div', 'outbox-actions');
    if (['queued', 'retry_wait'].includes(item.status)) {
      const cancel = node('button', 'button button-secondary', 'Cancel queued message'); cancel.type = 'button'; cancel.addEventListener('click', () => perform(() => request('/api/outbox/cancel', {id: item.id}), 'Cancellation recorded. Check the updated queue status.')); actions.append(cancel);
    }
    if (['accepted', 'sending', 'sent', 'uncertain'].includes(item.status)) {
      const reconcile = node('button', 'button button-secondary', 'Check provider status'); reconcile.type = 'button'; reconcile.addEventListener('click', () => perform(() => request('/api/outbox/reconcile', {id: item.id}), 'Provider status checked. This action did not resend the message.')); actions.append(reconcile);
    }
    if (item.status === 'uncertain') actions.append(node('span', 'small-hint', 'No resend action is available for an uncertain outcome.'));
    if (actions.childNodes.length) card.append(actions);
    container.append(card);
  }
}
function renderInboundProcessing() {
  const intake = app.state.inbound_processing || {};
  const counts = intake.counts || {}; const failed = Number(counts.verification_failed || 0); const rejected = Number(counts.rejected || 0); const pending = Number(counts.pending || 0);
  const warning = $('inbound-processing-warning'); warning.hidden = !isService() || failed + rejected === 0;
  warning.textContent = `Inbound processing requires review: ${failed} verification failed, ${rejected} rejected, ${pending} pending. Rejected or unverified intake does not authorize replies.`;
  const errors = intake.errors || []; const panel = $('inbound-processing-panel'); panel.hidden = !isService() || errors.length === 0;
  const container = $('inbound-processing-items'); container.replaceChildren();
  for (const issue of errors) {
    const card = node('article', 'inbound-issue-card'); const heading = node('div', 'outbox-card-header'); heading.append(node('strong', '', app.privacy ? 'Inbound record · Private identifier' : issue.sid_masked || 'Inbound record'), badge(pretty(issue.status), 'amber'));
    card.append(heading, node('p', '', `Error: ${pretty(issue.error_code) || 'Review required'}`), node('p', 'small-hint', `Received ${time(issue.received_at, true)}`)); container.append(card);
  }
}
function makeField(key, label, value, options = {}) {
  const wrapper = node('div', `business-field${options.full ? ' full-width' : ''}`); const labelNode = node('label', '', label); labelNode.htmlFor = `business-${key}`;
  const input = node(options.multiline ? 'textarea' : 'input'); input.id = `business-${key}`; input.dataset.field = key;
  input.value = options.contact && app.privacy ? isService() ? '[private contact · hidden]' : '[demo contact · hidden for showcase]' : value || '';
  if (options.contact && app.privacy) input.readOnly = true;
  if (options.multiline) input.rows = options.rows || 3;
  wrapper.append(labelNode, input); return wrapper;
}
function renderBusiness() {
  if (!app.state?.business) return;
  const b = app.state.business; const fields = $('business-fields'); fields.replaceChildren(); const grid = node('div', 'business-grid');
  grid.append(makeField('name', 'Business name', b.name), makeField('hours', 'Opening hours', b.hours), makeField('service_areas', 'Service areas · One per line', (b.service_areas || []).join('\n'), {multiline: true}), makeField('price_note', 'Price and estimate policy', b.price_note, {multiline: true}));
  for (const [index, service] of (b.services || []).entries()) {
    grid.append(makeField(`service_${index}_name`, `Service ${index + 1} · Name`, service.name), makeField(`service_${index}_pricing`, 'Approved price information', service.pricing), makeField(`service_${index}_description`, 'Service description', service.description, {multiline: true, full: true, rows: 2}));
  }
  grid.append(makeField('contact_email', 'Business contact email', b.contact_email, {contact: true}), makeField('contact_phone', 'Business contact phone', b.contact_phone, {contact: true}));
  fields.append(grid);
  if (isService()) renderApprovedFaqs(fields, b.approved_faqs || []);
  fields.append(node('div', 'business-extra', isService() ? `Understanding uses approved FAQ rules only; no model is called. Reply limit: ${b.max_messages_per_hour || 8} per contact per hour. ${app.realOutbound ? 'Live sending is enabled; delivery readiness is unverified in this dashboard.' : 'Real outbound is disabled in this service dry run.'}` : `Replies are simulated. Real outbound is disabled. Local reply limit: ${b.max_messages_per_hour || 8} per contact per hour. Understanding mode: ${b.model_mode === 'rules' ? 'deterministic rules' : 'mock adapter'}; no live model capability has been verified.`));
}
function renderApprovedFaqs(container, entries) {
  const section = node('section', 'approved-faqs'); section.append(node('h3', '', 'Approved FAQ replies'), node('p', '', 'One answer per topic, up to five. Only explicitly approved answers may be used. Blank or unapproved topics require your team.'));
  for (const topic of ['hours', 'areas', 'services', 'pricing', 'requests']) {
    const entry = entries.find(faq => faq.topic === topic) || {};
    const row = node('div', 'faq-editor-row'); const heading = node('div', 'faq-editor-heading'); heading.append(node('span', '', pretty(topic)));
    const approval = node('label', 'faq-approval'); const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.id = `faq-${topic}-approved`; checkbox.checked = entry.approved === true; approval.append(checkbox, node('span', '', 'Approved to use')); heading.append(approval);
    const answer = node('textarea'); answer.id = `faq-${topic}-answer`; answer.rows = 2; answer.value = entry.answer || ''; answer.setAttribute('aria-label', `Approved answer for ${topic}`); answer.placeholder = `Enter the approved ${topic} answer…`; row.append(heading, answer); section.append(row);
  }
  container.append(section);
}
function makeEvent(kind, phone, extra = {}) {
  return {event_id: eventId(), kind, phone, occurred_at: new Date().toISOString(), ...extra};
}
function eventId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}
function consentEvent(phone) {
  return makeEvent('consent', phone, {consent: {source: 'synthetic_web_form', time: new Date().toISOString(), purposes: [...purposes], evidence: 'Demo form checkbox: customer care, appointment requests, and missed-call follow-up; synthetic evidence only.', verified: true}});
}
function showResult(result) {
  const container = $('simulation-result'); container.replaceChildren(); container.hidden = false;
  const reason = result?.reason || result?.result?.reason || result?.status || 'Recorded';
  const status = result?.status || result?.result?.status || 'processed';
  const descriptions = {
    verified_scoped_consent_required: 'No follow-up sent: verified consent for this purpose is missing.',
    opted_out: 'No routine reply sent: this customer has opted out.',
    human_takeover_active: 'Automatic reply paused while your team handles this conversation.',
    event_already_processed: 'Duplicate event ignored; no additional reply was created.',
    simulated_delivery: 'Reply recorded in the conversation; sending was simulated.',
    simulated_delivery_failure: 'The simulated delivery failed. Your team now handles this conversation.',
    model_unavailable: 'The simulated understanding adapter is unavailable. The conversation was handed to your team.',
    model_error: 'The simulated understanding adapter returned an error. The conversation was handed to your team.',
    model_timeout: 'The simulated understanding adapter timed out. The conversation was handed to your team.',
    outside_approved_service_area: 'This location is outside the approved service area. Your team will review the request.',
    unrecognized_service_area: 'The service area could not be verified. Your team will review the request.',
    unknown_question: 'Approved information does not cover this question. Your team will provide an answer.',
    customer_requested_human: 'The customer requested a person. Automatic replies are paused until your team resumes them.',
    unsafe_instruction_rejected: 'The message could not bypass the rules. The conversation was handed to your team.',
    contact_blocked: 'No reply sent: this customer is restricted from receiving messages.',
    business_sending_disabled: 'No reply sent: simulated sending is paused in the business configuration.',
    hourly_send_limit: 'No reply sent: the hourly message limit has been reached.',
    current_inbound_required: 'No reply sent: a current customer inquiry is required.',
    not_a_supported_missed_dial_result: 'No follow-up created: this dial result does not establish a supported missed call.',
    verified_consent_recorded: 'A verified synthetic consent record was saved for the listed purposes.',
    unverified_consent_not_activated: 'The consent record was saved, but unverified evidence does not permit follow-ups.',
    opt_out_requires_start: 'Consent stays revoked. A verified restoration process is required.',
    no_verified_previous_enrollment: 'START did not restore consent because a verified prior enrollment is missing.',
    start_predates_applied_opt_out: 'This older START event cannot undo the customer’s later opt-out.',
    opt_out_applied_with_simulated_confirmation: 'Opt-out recorded. A single simulated preference confirmation was created.',
    opt_out_applied_no_extra_confirmation: 'Opt-out recorded. No additional confirmation was created.',
    consent_restored_provider_confirmation_only: 'Consent was restored within the verified enrollment scope. Other sending restrictions still apply.'
  };
  const headings = {blocked: 'Sending restriction applied', ignored: 'Event ignored', duplicate: 'Duplicate ignored', handoff: 'Handed to your team', failed: 'Simulation failed', failure: 'Simulation failed', error: 'Simulation failed'};
  container.classList.toggle('blocked', Object.hasOwn(headings, status));
  const description = descriptions[reason] || `${pretty(reason)}.`;
  container.append(node('strong', '', headings[status] || 'Simulation complete'), node('p', '', `${description} No real communication occurred.`));
}
async function simulate(event) {
  if (isService()) throw new Error('The simulator is unavailable in the service workspace.');
  app.lastEvent = structuredClone(event); app.phone = event.phone; app.simPhone = event.phone;
  const result = await request('/api/simulate', event); showResult(result); return result;
}
async function scenario(key) {
  let result;
  if (key === 'faq') result = await simulate(makeEvent('sms', '+12025550101', {text: 'What time are you open?'}));
  if (key === 'booking') result = await simulate(makeEvent('sms', '+12025550102', {text: 'I need a deep cleaning'}));
  if (key === 'complete') {
    const phone = '+12025550108';
    await request('/api/simulate', consentEvent(phone));
    for (const text of ['I need a deep cleaning', 'Riverside', 'Next Tuesday at 10 AM', 'sam@example.test']) result = await simulate(makeEvent('sms', phone, {text}));
    setView('appointments');
  }
  if (key === 'outside') result = await simulate(makeEvent('sms', '+12025550112', {text: 'Can you clean my home in Hillcrest?'}));
  if (key === 'handoff') {
    const phone = '+12025550104'; await request('/api/handoff', {phone, active: true}); result = await simulate(makeEvent('sms', phone, {text: 'What are your hours?'}));
  }
  if (key === 'no-consent') result = await simulate(makeEvent('missed_call', '+12025550105', {dial_status: 'no-answer'}));
  if (key === 'consent') result = await simulate(makeEvent('missed_call', '+12025550106', {dial_status: 'no-answer'}));
  if (key === 'stop') {
    const phone = '+12025550106'; await simulate(makeEvent('sms', phone, {text: 'STOP'})); result = await simulate(makeEvent('missed_call', phone, {dial_status: 'no-answer'}));
  }
  if (key === 'start') result = await simulate(makeEvent('sms', '+12025550107', {text: 'START'}));
  if (key === 'duplicate') {
    if (!app.lastEvent) await simulate(makeEvent('sms', '+12025550101', {text: 'What are your hours?'}));
    result = await simulate(structuredClone(app.lastEvent));
  }
  if (key === 'failure') result = await simulate(makeEvent('sms', '+12025550109', {text: 'What time are you open?', demo: {delivery_failure: true}}));
  if (key === 'unavailable') result = await simulate(makeEvent('sms', '+12025550110', {text: 'Do you remove paint stains from antique silk?', demo: {model: 'unavailable'}}));
  if (key !== 'complete') setView('inbox');
  return result;
}

function setup() {
  app.requestedView = location.hash.slice(1) || 'inbox';
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  $('refresh-button').addEventListener('click', () => perform(async () => { await refresh(); }, 'Workspace refreshed.'));
  $('reset-button').addEventListener('click', () => perform(async () => { await request('/api/reset', {}); app.phone = null; app.lastEvent = null; app.businessDirty = false; $('simulation-result').hidden = true; setView('inbox'); }, 'Demo restored. All synthetic conversations and preferences are back to their starting state.'));
  $('showcase-privacy').addEventListener('change', event => { app.privacy = event.target.checked; app.businessDirty = false; if (app.state) render(); toast(app.privacy ? isService() ? 'Privacy on. Customer contact details and provider identifiers are hidden.' : 'Showcase privacy on. Synthetic phone numbers and contact details are hidden.' : isService() ? 'Privacy off. Customer identifiers are visible in this operator session.' : 'Showcase privacy off. Synthetic identifiers are visible.'); });
  $('search-conversations').addEventListener('input', renderConversations);
  $('sim-contact').addEventListener('change', event => { app.simPhone = event.target.value === 'custom' ? '+12025550111' : event.target.value; updatePhoneField(); });
  $('sim-phone').addEventListener('input', event => { if (!app.privacy) app.simPhone = event.target.value.trim(); });
  $('sim-kind').addEventListener('change', event => { const kind = event.target.value; $('sms-fields').hidden = kind !== 'sms'; $('sim-text').required = kind === 'sms'; $('call-fields').hidden = kind !== 'missed_call'; $('consent-fields').hidden = kind !== 'consent'; });
  $('simulate-form').addEventListener('submit', event => {
    event.preventDefault();
    const kind = $('sim-kind').value; const phone = app.privacy ? app.simPhone : $('sim-phone').value.trim();
    if (!/^\+1\d{10}$/.test(phone)) { showError(new Error('Enter a synthetic number in +1XXXXXXXXXX format. Use the supplied demo contacts for the prepared scenarios.')); return; }
    let simulation;
    if (kind === 'sms') { const text = $('sim-text').value.trim(); if (!text) { showError(new Error('Enter a customer message before running the simulation.')); return; } simulation = makeEvent('sms', phone, {text}); }
    else if (kind === 'missed_call') simulation = makeEvent(kind, phone, {dial_status: $('dial-status').value});
    else simulation = consentEvent(phone);
    perform(async () => { const result = await simulate(simulation); setView('inbox'); return result; });
  });
  const scenarios = $('scenario-buttons');
  for (const [key, title, icon] of scenarioDefinitions) { const button = node('button', 'scenario-button'); button.type = 'button'; button.append(node('span', '', icon), node('span', '', title)); button.addEventListener('click', () => perform(() => scenario(key))); scenarios.append(button); }
  $('business-form').addEventListener('input', () => { app.businessDirty = true; });
  $('business-form').addEventListener('submit', event => {
    event.preventDefault(); const b = structuredClone(app.state.business);
    for (const key of ['name', 'hours', 'price_note']) b[key] = $(`business-${key}`).value.trim();
    b.service_areas = $('business-service_areas').value.split('\n').map(v => v.trim()).filter(Boolean);
    b.services = b.services.map((service, index) => ({...service, name: $(`business-service_${index}_name`).value.trim(), description: $(`business-service_${index}_description`).value.trim(), pricing: $(`business-service_${index}_pricing`).value.trim()}));
    if (!app.privacy) { b.contact_email = $('business-contact_email').value.trim(); b.contact_phone = $('business-contact_phone').value.trim(); }
    if (isService()) {
      b.model_mode = 'rules'; b.approved_faqs = [];
      for (const topic of ['hours', 'areas', 'services', 'pricing', 'requests']) {
        const answer = $(`faq-${topic}-answer`).value.trim(); const approved = $(`faq-${topic}-approved`).checked;
        if (approved && !answer) { showError(new Error(`Enter an answer for ${topic} before approving it.`)); return; }
        if (answer || approved) b.approved_faqs.push({topic, answer, approved});
      }
    }
    perform(async () => { const result = await request('/api/business', b); app.businessDirty = false; return result; }, 'Business profile saved. New replies will use these approved details.');
  });
  $('queue-pause-button').addEventListener('click', () => {
    const paused = app.state?.outbox?.paused === true;
    perform(() => request('/api/outbox/pause', {paused: !paused}), paused ? 'Sending resumed. Existing consent and delivery restrictions still apply.' : 'Sending paused. Messages already submitted to the provider cannot be recalled.');
  });
  $('login-form').addEventListener('submit', async event => {
    event.preventDefault(); const input = $('login-password'); const password = input.value;
    $('login-button').disabled = true; $('login-error').hidden = true;
    try { await request('/api/login', {password}); input.value = ''; await openSession(); }
    catch (error) { input.value = ''; showError(error); }
    finally { $('login-button').disabled = false; }
  });
  $('logout-button').addEventListener('click', async () => {
    try { await request('/api/logout', {}); lockWorkspace(); $('login-error').hidden = true; }
    catch (error) { showError(error); }
  });
  const followHistory = () => setView(location.hash.slice(1) || 'inbox', 'replace');
  window.addEventListener('popstate', followHistory);
  window.addEventListener('hashchange', followHistory);
  setView(app.requestedView, 'none');
}
function lockWorkspace() {
  app.authenticated = false; app.token = null; app.state = null; app.phone = null; app.mode = 'service'; app.businessDirty = false;
  document.body.classList.add('auth-loading'); $('auth-gate').hidden = false;
  document.title = 'Reception desk · Operator sign-in';
  $('auth-title').textContent = 'Sign in to your reception desk'; $('auth-description').textContent = 'An operator session is required to view customer records and sending controls.'; $('login-form').hidden = false; $('login-password').value = '';
  $('action-toast').hidden = true;
}
async function openSession() {
  const session = await request('/api/session');
  if (session.authenticated === false) { lockWorkspace(); return; }
  app.token = session.token; app.mode = session.mode === 'service' ? 'service' : 'demo'; app.realOutbound = session.real_outbound === true; app.authenticated = true;
  try { await refresh(); }
  catch (error) { if (isService()) lockWorkspace(); throw error; }
  $('desktop-exit-button').hidden = isService() || !app.state?.runtime?.desktop_managed;
  if (!isService() && app.view === 'outbox') setView('inbox', 'replace');
  if (isService()) setView(location.hash.slice(1) || app.requestedView || 'inbox', 'replace');
  $('auth-gate').hidden = true; document.body.classList.remove('auth-loading'); $('login-password').value = ''; $('login-error').hidden = true; clearError();
  document.title = isService() ? `${app.state.business?.name || 'Reception desk'} · Operator workspace` : 'Harbor & Home · Reception desk demo';
}
async function boot() {
  $('desktop-exit-button').addEventListener('click', async () => {
    if (isService() || !app.state?.runtime?.desktop_managed || app.busy) return;
    $('desktop-exit-button').disabled = true;
    try {
      await request('/api/exit', {});
      document.body.classList.add('auth-loading'); $('auth-gate').hidden = false;
      $('auth-title').textContent = 'Local demo closed';
      $('auth-description').textContent = 'The local server is stopping. You can close this tab and reopen the desktop shortcut when needed.';
      $('login-form').hidden = true;
    } catch (error) { $('desktop-exit-button').disabled = false; showError(error); }
  });
  setup();
  try { await openSession(); }
  catch (error) {
    if (error.status !== 401) { $('auth-title').textContent = 'Workspace unavailable'; $('auth-description').textContent = 'Check that the service is running, then refresh this page.'; }
    if (error.status === 401 && error.message === 'operator_sign_in_required') $('login-error').hidden = true;
    else showError(error);
    $('last-updated').textContent = 'Workspace is unavailable';
  }
}
boot();
