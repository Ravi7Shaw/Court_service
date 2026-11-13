const API = '';
const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem('court_token') || '';
let currentUser = null;
let selectedCourt = null;
let pendingReservation = null;
let courts = [];

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API}${path}`, { ...options, headers });
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `Request failed (${response.status})`);
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
  history.replaceState({}, '', '/');
  toast('Email verified. You can sign in now.');
}

$('register-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  const button = event.currentTarget.querySelector('button'); button.disabled = true; button.textContent = 'Creating account…';
  try {
    const result = await api('/api/auth/register', { method: 'POST', body: JSON.stringify({ email: form.get('email'), password: form.get('password') }) });
    if (result.debug_verification_token) {
      await verifyEmail(result.debug_verification_token);
      setAuthTab('login'); $('login-form').elements.email.value = form.get('email');
      authAlert('Your demo email was verified. Sign in with your new account.', true);
    } else {
      authAlert('Account created. Check your inbox for the verification link.', true);
      setAuthTab('login'); $('login-form').elements.email.value = form.get('email');
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
  await Promise.all([loadCourts(), loadBookings(), loadNotifications()]);
  if (currentUser.role === 'admin') await loadAdminVenues(); else $('admin-panel').classList.add('hidden');
  $('connection').classList.add('online'); $('connection').innerHTML = '<i></i> API connected';
}
function showAuth() {
  $('auth-view').classList.remove('hidden'); $('app-view').classList.add('hidden'); $('logout').classList.add('hidden');
}
$('logout').addEventListener('click', () => { token = ''; currentUser = null; sessionStorage.removeItem('court_token'); showAuth(); toast('You have signed out.'); });

function money(minor, currency = 'INR') { return new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 0 }).format(minor / 100); }
async function loadCourts() {
  courts = await api('/api/courts'); const root = $('court-list'); root.innerHTML = '';
  courts.forEach((court, index) => {
    const card = document.createElement('button'); card.type = 'button'; card.className = 'court-card';
    card.innerHTML = `<span class="court-symbol">⌂</span><strong></strong><span></span>`;
    card.querySelector('strong').textContent = court.name; card.querySelector('span:last-child').textContent = court.venue;
    card.addEventListener('click', () => {
      selectedCourt = court; document.querySelectorAll('.court-card').forEach((item) => item.classList.remove('selected'));
      card.classList.add('selected'); $('book-button').disabled = false; $('book-button').innerHTML = `Reserve ${court.name} <span>→</span>`;
    });
    root.append(card);
  });
  if (!courts.length) root.innerHTML = '<p class="empty-state">No active courts yet. An admin can add courts from the API.</p>';
}
const today = new Date(); $('booking-date').min = today.toISOString().slice(0, 10);
$('booking-date').value = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

$('booking-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!selectedCourt) return;
  const date = $('booking-date').value; const hour = Number($('booking-hour').value); const duration = Number($('booking-duration').value);
  if (!date || !hour) return toast('Choose a date and start time.', true);
  if (hour + duration > 22) return toast('The venue closes at 22:00. Choose an earlier start or shorter duration.', true);
  const start = new Date(`${date}T${String(hour).padStart(2, '0')}:00:00`);
  const end = new Date(start.getTime() + duration * 3600000);
  if (start <= new Date()) return toast('Choose a future time.', true);
  const button = $('book-button'); button.disabled = true; button.textContent = 'Checking availability…';
  try {
    pendingReservation = await api('/holds', { method: 'POST', headers: { 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : `key-${Date.now()}-${Math.random().toString(16).slice(2)}`) },
      body: JSON.stringify({ court_id: selectedCourt.id, starts_at: start.toISOString(), ends_at: end.toISOString() }) });
    $('payment-summary').textContent = `${selectedCourt.name} · ${start.toLocaleString()} · ${duration} hour(s)`;
    $('payment-total').textContent = money(pendingReservation.price_minor, pendingReservation.currency);
    $('payment-modal').classList.remove('hidden');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = `Reserve ${selectedCourt.name} <span>→</span>`; }
});
$('close-modal').addEventListener('click', () => $('payment-modal').classList.add('hidden'));
$('confirm-payment').addEventListener('click', async () => {
  if (!pendingReservation) return; const button = $('confirm-payment'); button.disabled = true; button.textContent = 'Processing simulated payment…';
  try {
    const payment = await api(`/api/payments/${pendingReservation.id}/simulate`, { method: 'POST', body: '{}' });
    $('payment-modal').classList.add('hidden'); toast(`Payment confirmed. Email notification queued — ${money(payment.amount_minor, payment.currency)}.`);
    pendingReservation = null; await Promise.all([loadBookings(), loadNotifications()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = 'Confirm simulated payment <span>→</span>'; }
});

async function loadBookings() {
  const rows = await api('/api/bookings/me'); const root = $('bookings-list'); root.innerHTML = '';
  if (!rows.length) { root.innerHTML = '<p class="empty-state">No bookings yet. Choose a court above to reserve your first game.</p>'; return; }
  rows.forEach((booking) => {
    const row = document.createElement('div'); row.className = 'booking-row';
    const date = new Date(booking.starts_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    const status = booking.status;
    row.innerHTML = `<div><strong></strong><small></small></div><div><strong></strong><small></small></div><div><span class="status"></span></div><div><strong></strong><small>Booking total</small></div><div class="row-actions"></div>`;
    row.children[0].querySelector('strong').textContent = booking.court;
    row.children[0].querySelector('small').textContent = booking.venue;
    row.children[1].querySelector('strong').textContent = date;
    row.children[1].querySelector('small').textContent = new Date(booking.ends_at).toLocaleTimeString([], { timeStyle: 'short' });
    const statusEl = row.querySelector('.status'); statusEl.textContent = status.replace('_', ' '); statusEl.classList.toggle('cancelled', status === 'cancelled'); statusEl.classList.toggle('expired', status === 'expired');
    row.children[3].querySelector('strong').textContent = money(booking.price_minor, booking.currency);
    const actions = row.querySelector('.row-actions');
    if (status === 'confirmed') {
      const cancel = document.createElement('button'); cancel.className = 'button button-ghost'; cancel.textContent = 'Cancel';
      cancel.addEventListener('click', async () => { if (!confirm('Cancel this booking?')) return; try { const result = await api(`/bookings/${booking.id}/cancel`, { method: 'POST', body: '{}' }); toast(result.refund_minor > 0 ? 'Booking cancelled. Refund is eligible.' : 'Booking cancelled. No refund is due.'); await Promise.all([loadBookings(), loadNotifications()]); } catch (e) { toast(e.message, true); } }); actions.append(cancel);
    }
    if (status === 'cancelled' && Number(booking.refund_minor || 0) > 0) {
      const refund = document.createElement('button'); refund.className = 'button button-secondary'; refund.textContent = 'Simulate refund';
      refund.addEventListener('click', async () => { try { const result = await api(`/api/payments/${booking.id}/refund/simulate`, { method: 'POST', body: '{}' }); toast(`Refund processed: ${money(result.refund_minor, result.currency)}. Email queued.`); await loadNotifications(); } catch (e) { toast(e.message, true); } }); actions.append(refund);
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
    if (!n.is_read) item.addEventListener('click', async () => { await api(`/api/notifications/${n.id}/read`, { method: 'POST', body: '{}' }); loadNotifications(); });
  });
}
$('refresh').addEventListener('click', () => Promise.all([loadBookings(), loadNotifications()]));

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
  try { await fetch('/healthz'); $('connection').classList.add('online'); $('connection').innerHTML = '<i></i> API online'; }
  catch { $('connection').textContent = 'API unavailable'; }
  const query = new URLSearchParams(location.search); const verification = query.get('verify');
  if (verification) { try { await verifyEmail(verification); } catch (e) { authAlert(e.message); } }
  if (token) {
    try { currentUser = await api('/api/auth/me'); await showDashboard(); }
    catch { token = ''; sessionStorage.removeItem('court_token'); showAuth(); }
  }
  if (token) setInterval(() => { loadNotifications().catch(() => {}); }, 12000);
}
boot();
