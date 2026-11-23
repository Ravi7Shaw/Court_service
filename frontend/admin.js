// Administration: venues, venue coordinates and court status.
// Loaded after app.js. Reuses its globals: $, api, toast, loadCourts and the `updates` channel.
// Rendering uses textContent only; server strings are never parsed as HTML.
(() => {
  const COORD_LIMITS = { latitude: 90, longitude: 180 };
  let lastAction = null;

  function el(tag, className = '', text = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function setStatus(message = '', isError = false) {
    const node = $('admin-status');
    node.textContent = message;
    node.className = message ? `alert${isError ? '' : ' success'}` : 'alert hidden';
    if (message) node.setAttribute('role', isError ? 'alert' : 'status');
  }

  function formatCoordinate(value) {
    return Number(value).toFixed(4);
  }

  function parseCoordinate(raw, limit) {
    if (raw.trim() === '') return { error: 'Enter a number.' };
    const value = Number(raw);
    if (!Number.isFinite(value) || Math.abs(value) > limit) return { error: `Use a value between -${limit} and ${limit}.` };
    return { value };
  }

  function statusBadge(venue) {
    if (venue.location_set) {
      return el('span', 'status-pill ok',
        `Location set · ${formatCoordinate(venue.latitude)}, ${formatCoordinate(venue.longitude)}`);
    }
    return el('span', 'status-pill warn', 'No location · courts hidden from nearby search');
  }

  function courtRow(venue, court) {
    const row = el('div', 'court-row');
    row.append(el('span', 'court-row-name', court.name));
    const toggle = el('button', `button ${court.active ? 'button-secondary' : 'button-ghost'} court-toggle`,
      court.active ? 'Active' : 'Inactive');
    toggle.type = 'button';
    toggle.dataset.action = 'toggle-court';
    toggle.dataset.courtId = court.id;
    toggle.dataset.venueId = venue.id;
    toggle.dataset.next = String(!court.active);
    toggle.setAttribute('aria-pressed', String(court.active));
    toggle.title = court.active ? 'Hide this court from members' : 'Show this court to members';
    row.append(toggle);
    return row;
  }

  function coordinateForm(venue) {
    const form = el('form', 'coords-form');
    form.dataset.venueId = venue.id;
    form.noValidate = true;

    const fields = el('div', 'coords-fields');
    [['latitude', 'Latitude', venue.latitude], ['longitude', 'Longitude', venue.longitude]].forEach(([name, label, value]) => {
      const wrap = el('div');
      const id = `venue-${name}-${venue.id}`;
      const input = el('input');
      input.id = id;
      input.name = name;
      input.type = 'number';
      input.step = 'any';
      input.inputMode = 'decimal';
      input.required = true;
      input.placeholder = name === 'latitude' ? 'e.g. 12.9716' : 'e.g. 77.5946';
      if (value !== null && value !== undefined) input.value = value;
      const labelNode = el('label', '', label);
      labelNode.htmlFor = id;
      wrap.append(labelNode, input);
      fields.append(wrap);
    });

    const actions = el('div', 'coords-actions');
    const save = el('button', 'button button-primary', venue.location_set ? 'Update location' : 'Save location');
    save.type = 'submit';
    save.dataset.action = 'save-coords';
    actions.append(save);
    if (venue.location_set) {
      const clear = el('button', 'text-button', 'Remove location');
      clear.type = 'button';
      clear.dataset.action = 'clear-coords';
      clear.dataset.venueId = venue.id;
      actions.append(clear);
    }
    const error = el('p', 'field-error');
    error.id = `coords-error-${venue.id}`;
    error.setAttribute('role', 'alert');
    error.hidden = true;

    form.append(fields, actions, error);
    return form;
  }

  function venueCard(venue) {
    const card = el('article', 'admin-venue');
    card.dataset.venueId = venue.id;

    const head = el('div', 'admin-venue-head');
    const title = el('div');
    title.append(el('h3', '', venue.name), el('p', 'fine-print', venue.timezone));
    head.append(title, statusBadge(venue));

    const courts = el('div', 'admin-courts');
    courts.append(el('p', 'eyebrow', 'COURTS'));
    if (!venue.courts.length) courts.append(el('p', 'fine-print', 'No courts yet. Add one below.'));
    venue.courts.forEach((court) => courts.append(courtRow(venue, court)));

    card.append(head, courts, el('p', 'eyebrow', 'LOCATION'), coordinateForm(venue));
    return card;
  }

  function renderSummary(venues) {
    const courtCount = venues.reduce((sum, venue) => sum + venue.courts.length, 0);
    const located = venues.filter((venue) => venue.location_set).length;
    $('admin-summary').textContent =
      `${venues.length} venue${venues.length === 1 ? '' : 's'} · ${courtCount} court${courtCount === 1 ? '' : 's'} · ${located} with a location`;
  }

  function renderCourtVenueOptions(venues) {
    const select = $('admin-court-venue');
    const previous = select.value;
    select.replaceChildren();
    venues.forEach((venue) => select.append(new Option(venue.name, venue.id)));
    if (venues.some((venue) => venue.id === previous)) select.value = previous;
  }

  async function loadAdminPanel() {
    $('admin-panel').classList.remove('hidden');
    const venues = await api('/api/admin/venues');
    const root = $('admin-venues');
    root.replaceChildren();
    renderSummary(venues);
    renderCourtVenueOptions(venues);
    if (!venues.length) {
      root.append(el('p', 'empty-state', 'No venues yet. Create a venue with the seed script, then add courts here.'));
    } else {
      venues.forEach((venue) => root.append(venueCard(venue)));
    }
    restoreFocus();
  }

  function restoreFocus() {
    if (!lastAction) return;
    const { selector } = lastAction;
    lastAction = null;
    const target = document.querySelector(selector);
    if (target) target.focus();
  }

  async function afterChange(message, focusSelector) {
    setStatus('');
    toast(message);
    lastAction = focusSelector ? { selector: focusSelector } : null;
    updates?.postMessage('changed');
    await loadAdminPanel();
    await loadCourts();
  }

  async function withBusy(button, pendingLabel, task) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = pendingLabel;
    try {
      await task();
    } catch (error) {
      setStatus(error.message, true);
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function saveCoordinates(form, button) {
    const venueId = form.dataset.venueId;
    const errorNode = $(`coords-error-${venueId}`);
    const latitude = parseCoordinate(form.elements.latitude.value, COORD_LIMITS.latitude);
    const longitude = parseCoordinate(form.elements.longitude.value, COORD_LIMITS.longitude);
    const problems = [];
    if (latitude.error) problems.push(`Latitude: ${latitude.error}`);
    if (longitude.error) problems.push(`Longitude: ${longitude.error}`);
    errorNode.hidden = problems.length === 0;
    errorNode.textContent = problems.join(' ');
    if (problems.length) return;

    await withBusy(button, 'Saving…', async () => {
      await api(`/api/admin/venues/${venueId}/coordinates`, {
        method: 'PUT',
        body: JSON.stringify({ latitude: latitude.value, longitude: longitude.value }),
      });
      await afterChange('Venue location saved. Its courts can now appear in nearby search.',
        `[data-venue-id="${venueId}"] .coords-form button[type="submit"]`);
    });
  }

  async function handleClick(event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const { action, venueId, courtId } = button.dataset;

    if (action === 'toggle-court') {
      const next = button.dataset.next === 'true';
      await withBusy(button, 'Updating…', async () => {
        await api(`/api/admin/courts/${courtId}`, { method: 'PATCH', body: JSON.stringify({ active: next }) });
        await afterChange(next ? 'Court is now visible to members.' : 'Court hidden from members.',
          `[data-court-id="${courtId}"]`);
      });
    }

    if (action === 'clear-coords') {
      if (!confirm('Remove this venue’s location? Its courts will stop appearing in nearby search.')) return;
      await withBusy(button, 'Removing…', async () => {
        await api(`/api/admin/venues/${venueId}/coordinates`, { method: 'DELETE' });
        await afterChange('Venue location removed.', `[data-venue-id="${venueId}"] .coords-form input`);
      });
    }
  }

  function init() {
    const panel = $('admin-venues');
    panel.addEventListener('click', handleClick);
    panel.addEventListener('submit', (event) => {
      if (!event.target.matches('.coords-form')) return;
      event.preventDefault();
      saveCoordinates(event.target, event.submitter || event.target.querySelector('button[type="submit"]'));
    });

    $('create-court-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const name = $('admin-court-name').value.trim();
      if (name.length < 2) {
        setStatus('Court name must be at least 2 characters.', true);
        return;
      }
      await withBusy(button, 'Creating…', async () => {
        const court = await api('/api/admin/courts', {
          method: 'POST',
          body: JSON.stringify({ venue_id: $('admin-court-venue').value, name }),
        });
        $('admin-court-name').value = '';
        await afterChange(`Created ${court.name}.`, null);
      });
    });
  }

  window.loadAdminPanel = loadAdminPanel;
  window.clearAdminPanel = () => {
    $('admin-venues').replaceChildren();
    $('admin-panel').classList.add('hidden');
    setStatus('');
  };
  init();
})();
