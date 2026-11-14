(() => {
  const states = {tracking: 'Следит за лицом', limited: 'Достигнут предел диапазона', stale: 'Ждёт свежие координаты телефона', no_target: 'Лицо не найдено', waiting: 'Ждёт координаты телефона', uncalibrated: 'Требуется настройка', manual: 'Ручное движение', centering: 'Возврат в центр'};
  const degrees = v => Number.isFinite(v) ? `${v.toLocaleString('ru-RU', {maximumFractionDigits: 1})}°` : '—';
  for (const panel of document.querySelectorAll('.servo-panel')) {
    const form = panel.querySelector('form');
    const fields = form.querySelector('fieldset');
    const feedback = panel.querySelector('.servo-feedback');
    const draft = panel.querySelector('.servo-draft');
    const start = panel.querySelector('.servo-start');
    const stop = panel.querySelector('.servo-stop');
    let actual, dirty = false, busy = false, timer, pendingStop = false;
    function fill() {
      if (!actual) return;
      for (const [name, value] of Object.entries(actual)) {
        const input = form.elements.namedItem(name);
        if (!input) continue;
        if (input.type === 'checkbox') input.checked = value;
        else input.value = Number(value.toFixed(3));
        const slider = form.querySelector(`[data-field="${name}"]`);
        if (slider) slider.value = value;
      }
      dirty = false; draft.textContent = '';
    }
    function show(data, force = false) {
      const t = data.tracking;
      if (!t?.settings) throw Error('Обновите прошивку подставки для настройки слежения.');
      actual = t.settings;
      panel.querySelector('.servo-state').textContent = t.outputEnabled ? (states[t.state] || 'Слежение включено') : 'Движение выключено';
      panel.querySelector('.servo-position').textContent = `Поворот ≈ ${degrees(t.panDeg)} · Наклон ≈ ${degrees(t.tiltDeg)}`;
      fields.disabled = false;
      start.disabled = actual.enabled && t.outputEnabled;
      stop.disabled = !actual.enabled && !t.outputEnabled;
      if (!dirty || force) fill();
    }
    async function request(suffix, payload) {
      const response = await fetch(panel.dataset.url + suffix, {
        method: payload === undefined ? 'GET' : 'POST',
        headers: payload === undefined ? {} : {'Content-Type': 'application/json'},
        body: payload === undefined ? undefined : JSON.stringify(payload),
        cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10000)
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw Error(typeof error.detail === 'string' ? error.detail : 'Не удалось применить настройки. Проверьте значения и связь.');
      }
      return response.json();
    }
    function schedule() {
      clearTimeout(timer);
      if (panel.open && !document.hidden) timer = setTimeout(poll, 3000);
    }
    async function poll() {
      if (busy || !panel.open || document.hidden) return;
      busy = true;
      try { show(await request('.json')); }
      catch (error) {
        panel.querySelector('.servo-state').textContent = 'Состояние не подтверждено';
        feedback.textContent = error.message;
        fields.disabled = true; start.disabled = true;
        // A stop remains available after a lost response: it is safe to repeat explicitly.
        stop.disabled = !actual;
      } finally {
        busy = false;
        if (pendingStop) { pendingStop = false; send('/control', {enabled: false}, 'Движение остановлено.'); }
        else schedule();
      }
    }
    async function send(suffix, payload, message) {
      if (busy) {
        if (suffix === '/control' && payload.enabled === false) {
          pendingStop = true; feedback.textContent = 'Останавливаем после текущего запроса…';
        }
        return;
      }
      busy = true; clearTimeout(timer);
      fields.disabled = true; start.disabled = stop.disabled = true;
      feedback.textContent = 'Применяем…';
      try {
        show(await request(suffix, payload), suffix === '/settings');
        feedback.textContent = message;
      } catch (error) {
        feedback.textContent = error.message;
        panel.querySelector('.servo-state').textContent = 'Проверьте состояние подставки';
        // Never claim success or resend a write after an ambiguous network failure.
        start.disabled = true; stop.disabled = !actual;
      } finally { busy = false; schedule(); }
    }
    form.addEventListener('input', event => {
      const input = event.target;
      if (input.dataset.field) form.elements.namedItem(input.dataset.field).value = input.value;
      else {
        const slider = form.querySelector(`[data-field="${input.name}"]`);
        if (slider) slider.value = input.value;
      }
      dirty = true; draft.textContent = 'Есть неприменённые изменения';
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!actual || !form.reportValidity()) return;
      const payload = {...actual};
      for (const input of form.querySelectorAll('input[name]')) payload[input.name] = input.type === 'checkbox' ? input.checked : Number(input.value);
      send('/settings', payload, 'Настройки применены и сохранены в подставке.');
    });
    start.addEventListener('click', () => send('/control', {enabled: true}, 'Слежение включено.'));
    stop.addEventListener('click', () => send('/control', {enabled: false}, 'Движение остановлено.'));
    panel.querySelector('.servo-reset').addEventListener('click', fill);
    panel.querySelector('.servo-refresh').addEventListener('click', poll);
    panel.addEventListener('toggle', () => panel.open ? poll() : clearTimeout(timer));
    document.addEventListener('visibilitychange', () => document.hidden ? clearTimeout(timer) : poll());
  }
})();
