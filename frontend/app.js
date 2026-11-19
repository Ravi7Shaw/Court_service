const API = '';
const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem('court_token') || '';
let currentUser = null;
let selectedCourt = null;
let pendingReservation = null;
let courts = [];
let availabilityRequest = 0;
let modalTrigger = null;

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const requestToken = token;
  const response = await fetch(`${API}${path}`, { cache: 'no-store', ...options, headers });
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (requestToken !== token) throw new Error('Your session changed. Please try again.');
  if (response.status === 401 && token) {
    signOut();
    authAlert('Your session expired. Please sign in again.');
  }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}). Check your entries and try again.`);
  return data;
}
function toast(message, error = false) {
  const node = document.createElement('div'); node.className = `toast${error ? ' error' : ''}`; node.textContent = message;
  $('toast-root').append(node); setTimeout(() => node.remove(), 4800);
}
function authAlert(message, success = false) {
  const node = $('auth-alert'); node.textContent = message; node.className = `alert${success ? ' success' : ''}`;
}
function setAuthTab(name) {
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $('login-form').classList.toggle('hidden', name !== 'login'); $('register-form').classList.toggle('hidden', name !== 'register');
  $('auth-alert').classList.add('hidden');
}
document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => setAuthTab(b.dataset.tab)));

async function verifyEmail(tokenValue) {
  await api('/api/auth/verify-email', { method: 'POST', body: JSON.stringify({ token: tokenValue }) });
  toast('Email verified. You can sign in now.');
}

$('register-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  const button = event.currentTarget.querySelector('button'); button.disabled = true; button.textContent = 'Creating account…';
  try {
    const result = await api('/api/auth/register', { method: 'POST', body: JSON.stringify({ email: form.get('email'), password: form.get('password') }) });
    if (result.requires_admin_verification) {
      setAuthTab('login'); $('login-form').elements.email.value = form.get('email');
      authAlert('Account created. An admin must verify your demo account before you sign in.', true);
    } else if (result.debug_verification_token) {
      await verifyEmail(result.debug_verification_token);
      setAuthTab('login'); $('login-form').elements.email.value = form.get('email');
      authAlert('Your demo email was verified. Sign in with your new account.', true);
    } else {
      setAuthTab('login');
      authAlert('Account created. Check your inbox for the verification link.', true); $('login-form').elements.email.value = form.get('email');
    }
  } catch (error) { authAlert(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Create account <span>→</span>'; }
});

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const button = event.currentTarget.querySelector('button');
  button.disabled = true; button.textContent = 'Signing in…';
  try {
    const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email: form.get('email'), password: form.get('password') }) });
    token = result.access_token; sessionStorage.setItem('court_token', token); currentUser = result.user; await showDashboard();
  } catch (error) { authAlert(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Sign in <span>→</span>'; }
});

async function showDashboard() {
  $('auth-view').classList.add('hidden'); $('app-view').classList.remove('hidden'); $('logout').classList.remove('hidden');
  $('user-email').textContent = currentUser.email; $('avatar').textContent = currentUser.email[0].toUpperCase();
  $('welcome-user').textContent = `Signed in as ${currentUser.role} · Your next game is one booking away.`;
  await Promise.all([loadCourts(), loadBookings(), loadNotifications(), loadLocationConsent()]);
  if (currentUser.role === 'admin') { await loadAdminVenues(); await loadReview(); } else { $('admin-panel').classList.add('hidden'); $('review-panel').classList.add('hidden'); }
  lastActivity = Date.now(); scheduleRefresh();
  $('connection').classList.add('online'); $('connection').innerHTML = '<i></i> API connected';
}
function showAuth() {
  $('auth-view').classList.remove('hidden'); $('app-view').classList.add('hidden'); $('logout').classList.add('hidden');
}
function signOut() {
  clearTimeout(refreshTimer); refreshTimer = null;
  token = ''; currentUser = null; selectedCourt = null; pendingReservation = null;
  availabilityRequest += 1;
  sessionStorage.removeItem('court_token');
  closePayment();
  ['court-list', 'bookings-list', 'notifications', 'nearby-list'].forEach((id) => $(id).replaceChildren());
  $('admin-panel').classList.add('hidden');
  $('review-panel').classList.add('hidden');
  ['approval-list', 'pending-users', 'outbox-list'].forEach((id) => $(id).replaceChildren());
  $('remove-location').classList.add('hidden');
  $('book-button').disabled = true;
  $('book-button').textContent = 'Select a court to continue →';
  showAuth();
}
$('logout').addEventListener('click', () => { signOut(); toast('You have signed out.'); });

function money(minor, currency = 'INR') { return new Intl.NumberFormat('en-IN', { style: 'currency', currency }).format(minor / 100); }
async function loadCourts() {
  courts = await api('/api/courts');
  const previous = selectedCourt?.id; const root = $('court-list'); root.innerHTML = '';
  selectedCourt = null; $('book-button').disabled = true;
  courts.forEach((court) => {
    const card = document.createElement('button'); card.type = 'button'; card.className = 'court-card';
    card.innerHTML = `<span class="court-symbol">⌂</span><strong></strong><span></span>`;
    card.querySelector('strong').textContent = court.name; card.querySelector('span:last-child').textContent = court.venue;
    card.setAttribute('aria-pressed', 'false');
    card.addEventListener('click', () => {
      selectedCourt = court; document.querySelectorAll('.court-card').forEach((item) => item.classList.remove('selected'));
      card.classList.add('selected');
      document.querySelectorAll('.court-card').forEach((item) => item.setAttribute('aria-pressed', String(item === card)));
      $('booking-timezone').textContent = `Times shown in ${court.timezone}.`;
      $('book-button').textContent = `Reserve ${court.name} →`;
      loadTimes();
    });
    root.append(card);
  });
  const restored = courts.findIndex((court) => court.id === previous);
  if (restored >= 0) root.children[restored].click();
  if (!courts.length) root.innerHTML = '<p class="empty-state">No active courts yet. An admin can add courts from the API.</p>';
}
function dateInZone(date, timeZone) {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
}
$('booking-date').min = dateInZone(new Date(), 'UTC');
$('booking-date').value = dateInZone(new Date(Date.now() + 86400000), 'UTC');
async function loadTimes() {
  const requestId = ++availabilityRequest;
  const court = selectedCourt;
  const select = $('booking-hour'); const previousTime = select.value; select.replaceChildren(new Option('Loading times…', ''));
  $('book-button').disabled = true;
  if (!court || !$('booking-date').value) return;
  const date = $('booking-date').value;
  $('booking-date').min = dateInZone(new Date(), court.timezone);
  const middle = new Date(`${date}T12:00:00Z`).getTime();
  try {
    const slots = (await api(`/courts/${court.id}/availability?from=${encodeURIComponent(new Date(middle - 36 * 3600000).toISOString())}&to=${encodeURIComponent(new Date(middle + 36 * 3600000).toISOString())}`)).slots;
    if (requestId !== availabilityRequest) return;
    select.replaceChildren(new Option('Select time', ''));
    const duration = Number($('booking-duration').value);
    slots.filter((slot) => dateInZone(new Date(slot.starts_at), court.timezone) === date).forEach((slot) => {
      const start = new Date(slot.starts_at).getTime();
      const available = Array.from({ length: duration }, (_, i) => slots.find((candidate) =>
        new Date(candidate.starts_at).getTime() === start + i * 3600000 && candidate.available &&
        dateInZone(new Date(candidate.starts_at), court.timezone) === date)).every(Boolean);
      const label = new Date(slot.starts_at).toLocaleTimeString([], { timeZone: court.timezone, hour: '2-digit', minute: '2-digit' });
      const option = new Option(`${label}${available ? '' : ' · Unavailable'}`, slot.starts_at);
      option.disabled = !available; select.append(option);
    });
    if ([...select.options].some((option) => option.value === previousTime && !option.disabled)) select.value = previousTime;
    if (![...select.options].some((option) => option.value && !option.disabled)) select.options[0].textContent = 'No available times';
    $('book-button').disabled = false;
  } catch (error) {
    if (requestId !== availabilityRequest) return;
    select.replaceChildren(new Option('Could not load times', ''));
    toast(error.message, true);
  }
}
$('booking-date').addEventListener('change', loadTimes);
$('booking-duration').addEventListener('change', loadTimes);

function openPayment(reservation, summary, trigger) {
  pendingReservation = reservation; modalTrigger = trigger || document.activeElement;
  $('payment-summary').textContent = summary;
  $('payment-total').textContent = money(reservation.price_minor, reservation.currency);
  $('payment-modal').classList.remove('hidden');
  $('close-modal').focus();
}
function closePayment() {
  $('payment-modal').classList.add('hidden');
  if (modalTrigger?.isConnected) modalTrigger.focus();
  modalTrigger = null;
}
$('payment-modal').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closePayment();
  if (event.key !== 'Tab') return;
  const buttons = [...$('payment-modal').querySelectorAll('button:not(:disabled)')];
  if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
  if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
});
$('payment-modal').addEventListener('click', (event) => { if (event.target === $('payment-modal')) closePayment(); });

$('booking-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!selectedCourt) return;
  const date = $('booking-date').value; const time = $('booking-hour').value; const duration = Number($('booking-duration').value);
  if (!date || !time) return toast('Choose a date and start time.', true);
  const start = new Date(time);
  const end = new Date(start.getTime() + duration * 3600000);
  if (start <= new Date()) return toast('Choose a future time.', true);
  const button = $('book-button'); button.disabled = true; button.textContent = 'Checking availability…';
  try {
    pendingReservation = await api('/holds', { method: 'POST', headers: { 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : `key-${Date.now()}-${Math.random().toString(16).slice(2)}`) },
      body: JSON.stringify({ court_id: selectedCourt.id, starts_at: start.toISOString(), ends_at: end.toISOString() }) });
    openPayment(pendingReservation, `${selectedCourt.name} · ${start.toLocaleString([], { timeZone: selectedCourt.timezone })} (${selectedCourt.timezone}) · ${duration} hour(s)`, button);
    await loadBookings();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = selectedCourt ? `Reserve ${selectedCourt.name} →` : 'Select a court to continue →'; }
});
$('close-modal').addEventListener('click', closePayment);
$('confirm-payment').addEventListener('click', async () => {
  if (!pendingReservation) return; const button = $('confirm-payment'); button.disabled = true; button.textContent = 'Submitting report…';
  try {
    const payment = await api(`/api/payments/${pendingReservation.id}/report`, { method: 'POST', body: JSON.stringify({ message: $('payment-message').value }) });
    closePayment(); toast(`Payment report ${payment.status}. An admin must approve your booking. No money moved.`);
    updates?.postMessage('changed');
    pendingReservation = null; await Promise.all([loadBookings(), loadNotifications()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Submit for admin review <span>→</span>'; }
});

async function loadBookings() {
  const rows = await api('/api/bookings/me'); const root = $('bookings-list'); root.innerHTML = '';
  if (!rows.length) { root.innerHTML = '<p class="empty-state">No bookings yet. Choose a court above to reserve your first game.</p>'; return; }
  rows.forEach((booking) => {
    const row = document.createElement('div'); row.className = 'booking-row';
    const date = new Date(booking.starts_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short', timeZone: booking.timezone });
    const status = booking.status;
    row.innerHTML = `<div><strong></strong><small></small></div><div><strong></strong><small></small></div><div><span class="status"></span></div><div><strong></strong><small>Booking total</small></div><div class="row-actions"></div>`;
    row.children[0].querySelector('strong').textContent = booking.court;
    row.children[0].querySelector('small').textContent = booking.venue;
    row.children[1].querySelector('strong').textContent = date;
    row.children[1].querySelector('small').textContent = `${new Date(booking.ends_at).toLocaleTimeString([], { timeStyle: 'short', timeZone: booking.timezone })} · ${booking.timezone}`;
    const statusEl = row.querySelector('.status'); statusEl.textContent = status === 'payment_pending' ? 'Awaiting admin' : status.replaceAll('_', ' '); statusEl.classList.toggle('cancelled', status === 'cancelled'); statusEl.classList.toggle('expired', status === 'expired');
    row.children[3].querySelector('strong').textContent = money(booking.price_minor, booking.currency);
    if (booking.payment_status === 'refunded') row.children[3].querySelector('small').textContent = 'Demo refund approved';
    const actions = row.querySelector('.row-actions');
    if (status === 'held') {
      const resume = document.createElement('button'); resume.className = 'button button-secondary'; resume.textContent = 'Complete payment';
      resume.addEventListener('click', () => openPayment(booking, `${booking.court} · ${date}`, resume)); actions.append(resume);
    }
    if (status === 'confirmed') {
      const cancel = document.createElement('button'); cancel.className = 'button button-ghost'; cancel.textContent = 'Cancel';
      cancel.addEventListener('click', async () => { if (!confirm('Cancel this booking?')) return; try { const result = await api(`/bookings/${booking.id}/cancel`, { method: 'POST', body: '{}' }); toast(result.refund_minor > 0 ? 'Booking cancelled. Refund is eligible.' : 'Booking cancelled. No refund is due.'); await Promise.all([loadBookings(), loadNotifications()]); } catch (e) { toast(e.message, true); } }); actions.append(cancel);
    }
    if (status === 'cancelled' && booking.payment_status !== 'refunded' && Number(booking.refund_minor || 0) > 0) {
      const refund = document.createElement('button'); refund.className = 'button button-secondary'; refund.textContent = booking.refund_review ? `Refund ${booking.refund_review}` : 'Request refund';
      refund.disabled = Boolean(booking.refund_review);
      refund.addEventListener('click', async () => { try { const result = await api(`/api/payments/${booking.id}/refund/request`, { method: 'POST', body: '{}' }); toast(`Refund request ${result.status}. Admin review required.`); updates?.postMessage('changed'); await Promise.all([loadBookings(), loadNotifications()]); } catch (e) { toast(e.message, true); } }); actions.append(refund);
    }
    root.append(row);
  });
}
async function loadNotifications() {
  const rows = await api('/api/notifications'); const root = $('notifications'); root.innerHTML = '';
  $('unread-count').textContent = rows.filter((n) => !n.is_read).length;
  if (!rows.length) { root.innerHTML = '<p class="empty-state">No notifications yet. Payment and booking updates appear here.</p>'; return; }
  rows.slice(0, 12).forEach((n) => {
    const item = document.createElement('div'); item.className = 'notification';
    const title = document.createElement('strong'); title.textContent = n.title;
    const message = document.createElement('p'); message.textContent = n.message;
    const time = document.createElement('small'); time.textContent = new Date(n.created_at).toLocaleString();
    item.append(title, message, time); root.append(item);
    if (!n.is_read) {
      item.tabIndex = 0; item.setAttribute('role', 'button'); item.setAttribute('aria-label', `Mark as read: ${n.title}`);
      const markRead = async () => { try { await api(`/api/notifications/${n.id}/read`, { method: 'POST', body: '{}' }); await loadNotifications(); } catch (e) { toast(e.message, true); } };
      item.addEventListener('click', markRead);
      item.addEventListener('keydown', (event) => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); markRead(); } });
    }
  });
}
$('refresh').addEventListener('click', () => refreshData());

async function loadAdminVenues() {
  $('admin-panel').classList.remove('hidden');
  const venues = await api('/api/admin/venues');
  $('admin-venue').innerHTML = '';
  venues.forEach((venue) => {
    const option = document.createElement('option'); option.value = venue.id; option.textContent = venue.name;
    $('admin-venue').append(option);
  });
}
$('create-court-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const result = await api('/api/admin/courts', { method: 'POST', body: JSON.stringify({
      venue_id: $('admin-venue').value, name: $('admin-court-name').value.trim()
    }) });
    $('admin-court-name').value = ''; toast(`Created ${result.name}.`); await loadCourts();
  } catch (error) { toast(error.message, true); }
});

async function loadLocationConsent() {
  const result = await api('/api/me/location');
  $('remove-location').classList.toggle('hidden', !result.shared);
}
$('share-location').addEventListener('click', () => {
  if (!navigator.geolocation) return toast('Geolocation is not supported by this browser.', true);
  navigator.geolocation.getCurrentPosition(async (position) => {
    try { await api('/api/me/location', { method: 'POST', body: JSON.stringify({ latitude: position.coords.latitude, longitude: position.coords.longitude, consent: true }) });
      $('remove-location').classList.remove('hidden'); const nearby = await api('/api/courts/nearby?radius_km=25');
      $('nearby-list').innerHTML = ''; nearby.forEach((court) => { const row = document.createElement('div'); row.className = 'nearby-item'; const name = document.createElement('span'); name.textContent = court.name; const distance = document.createElement('span'); distance.textContent = `${court.distance_km} km`; row.append(name, distance); $('nearby-list').append(row); });
      toast('Location saved with your consent.');
    } catch (e) { toast(e.message, true); }
  }, (error) => toast(error.message || 'Location permission was not granted.', true), { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
});
$('remove-location').addEventListener('click', async () => { try { await api('/api/me/location', { method: 'DELETE' }); $('remove-location').classList.add('hidden'); $('nearby-list').innerHTML = ''; toast('Saved location removed.'); } catch (e) { toast(e.message, true); } });

async function boot() {
  try { const health = await fetch('/readyz'); if (!health.ok) throw new Error('unavailable'); $('connection').classList.add('online'); $('connection').innerHTML = '<i></i> API online'; }
  catch { $('connection').textContent = 'API unavailable'; }
  const query = new URLSearchParams(location.search); const verification = query.get('verify');
  if (verification) { history.replaceState({}, '', location.pathname); try { await verifyEmail(verification); } catch (e) { authAlert(e.message); } }
  if (token) {
    try { currentUser = await api('/api/auth/me'); await showDashboard(); }
    catch { token = ''; sessionStorage.removeItem('court_token'); showAuth(); }
  }
  scheduleRefresh();
}
// Durable data lives on the server. Idle/hidden tabs keep no polling connection open.
let refreshTimer = null;
let refreshPromise = null;
let lastActivity = Date.now();
let lastRefresh = 0;
const updates = typeof BroadcastChannel === 'function' ? new BroadcastChannel('courtly-updates') : null;
async function refreshData() {
  if (!token || document.hidden || Date.now() - lastActivity >= 60000) return;
  if (refreshPromise) return refreshPromise;
  lastRefresh = Date.now();
  refreshPromise = Promise.all([loadBookings(), loadNotifications(), loadCourts(), loadLocationConsent(),
    ...(currentUser?.role === 'admin' ? [loadReview()] : [])])
    .then(() => { $('connection').textContent = 'Data up to date'; })
    .catch((error) => { $('connection').textContent = 'Refresh failed · retrying'; toast(error.message, true); })
    .finally(() => { refreshPromise = null; scheduleRefresh(); });
  return refreshPromise;
}
function scheduleRefresh() {
  if (!token || document.hidden || Date.now() - lastActivity >= 60000) {
    clearTimeout(refreshTimer); refreshTimer = null; return;
  }
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = null; refreshData(); }, 15000 + Math.random() * 5000);
}
function resumeActivity() {
  const wasIdle = Date.now() - lastActivity >= 60000;
  lastActivity = Date.now();
  if (wasIdle && Date.now() - lastRefresh > 1000) refreshData();
  scheduleRefresh();
}
['pointerdown', 'keydown', 'wheel'].forEach((event) => window.addEventListener(event, resumeActivity, { passive: true }));
document.addEventListener('visibilitychange', () => {
  clearTimeout(refreshTimer); refreshTimer = null;
  if (!document.hidden) { lastActivity = Date.now(); refreshData(); }
});
window.addEventListener('focus', () => { if (Date.now() - lastRefresh > 1000) { lastActivity = Date.now(); refreshData(); } });
if (updates) updates.onmessage = () => { if (!document.hidden && Date.now() - lastActivity < 60000) refreshData(); };

function reviewRow(root, label, actions = []) {
  const row = document.createElement('div'); row.className = 'review-row';
  const text = document.createElement('p'); text.textContent = label; row.append(text);
  for (const action of actions) {
    const button = document.createElement('button'); button.className = 'button button-secondary'; button.textContent = action.label;
    button.addEventListener('click', async () => { button.disabled = true; try { await action.run(); updates?.postMessage('changed'); await refreshData(); } catch (error) { toast(error.message, true); } finally { button.disabled = false; } });
    row.append(button);
  }
  root.append(row);
}
async function loadReview() {
  $('review-panel').classList.remove('hidden');
  const [requests, users, messages] = await Promise.all([api('/api/admin/approvals'), api('/api/admin/users/pending'), api('/api/admin/outbox')]);
  ['approval-list', 'pending-users', 'outbox-list'].forEach((id) => $(id).replaceChildren());
  if (!requests.length) reviewRow($('approval-list'), 'No pending payment or refund requests.');
  requests.forEach((request) => reviewRow($('approval-list'),
    `${request.email} · ${request.court} · ${request.kind} · ${money(request.amount_minor, request.currency)} · ${request.member_message}${request.expires_at ? ` · Expires ${new Date(request.expires_at).toLocaleTimeString()}` : ''}`,
    (request.actionable ? [true, false] : [false]).map((approve) => ({ label: approve ? 'Approve' : 'Reject / close', run: async () => {
      const result = await api(`/api/admin/approvals/${request.id}/decision`, { method: 'POST', body: JSON.stringify({ approve }) });
      toast(`Request ${result.status}.`);
    } }))));
  if (!users.length) reviewRow($('pending-users'), 'No accounts awaiting verification.');
  users.forEach((user) => reviewRow($('pending-users'), user.email, [{ label: 'Verify demo account', run: () => api(`/api/admin/users/${user.id}/verify`, { method: 'POST' }) }]));
  if (!messages.length) reviewRow($('outbox-list'), 'No queued messages.');
  messages.forEach((message) => reviewRow($('outbox-list'), `${message.subject} · ${message.status} · ${message.attempts} attempt(s)${message.last_error ? ` · ${message.last_error}` : ''}`,
    message.status === 'dead' ? [{ label: 'Retry delivery', run: () => api(`/api/admin/outbox/${message.id}/retry`, { method: 'POST' }) }] : []));
}
boot();
