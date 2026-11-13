/* Diagnostic camera coordinates only. Pausing a graph never pauses the assistant or motors. */
(() => {
  const time = at => new Date(at).toLocaleTimeString('ru-RU', {hour12: false});
  const signed = value => (value >= 0 ? '+' : '') + value.toFixed(3);
  for (const panel of document.querySelectorAll('.tracking-diagnostics')) {
    const status = panel.querySelector('.tracking-status');
    const pause = panel.querySelector('.tracking-pause');
    const charts = [...panel.querySelectorAll('.tracking-chart')];
    let paused = false, timer, request, history;
    function draw(data) {
      history = data;
      const start = data.now - data.seconds * 1000;
      const px = at => 40 + 304 * (at - start) / (data.seconds * 1000);
      for (const chart of charts) {
        for (const axis of ['x', 'y']) {
        let path = '', previous;
        for (const point of data.points) {
          const value = point[axis];
          if (value === null || !Number.isFinite(value)) { previous = undefined; continue; }
          const connected = previous && point.at - previous.at <= 750 && point.session === previous.session;
          path += `${connected ? 'L' : 'M'}${px(point.at).toFixed(2)},${(80 - 62 * value).toFixed(2)}${connected ? '' : 'l0,0'} `;
          previous = point;
        }
        chart.querySelector(`.tracking-line[data-axis=${axis}]`).setAttribute('d', path);
        chart.querySelector('.tracking-from').textContent = time(start);
        chart.querySelector('.tracking-to').textContent = time(data.now);
        const last = data.points.at(-1);
        chart.querySelector(`output[data-axis=${axis}]`).textContent = last && data.now - last.at <= 5000 && last[axis] !== null ? signed(last[axis]) : '—';
        }
      }
      const last = data.points.at(-1);
      status.textContent = !last ? 'Пока нет наблюдений за последнюю минуту' :
        data.now - last.at > 5000 ? 'Нет свежих наблюдений' : last.x === null ? 'Лицо не найдено' : 'Свежие наблюдения · обновление каждые 2 с';
    }
    async function poll() {
      clearTimeout(timer);
      if (!panel.open || paused || document.hidden || request) return;
      request = new AbortController();
      try {
        const response = await fetch(panel.dataset.url, {signal: request.signal, cache: 'no-store', redirect: 'error'});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw Error('unavailable');
        const data = await response.json();
        if (panel.open && !paused) draw(data);
      } catch (error) {
        if (error.name !== 'AbortError') status.textContent = 'Не удалось обновить график';
      } finally {
        request = undefined;
        if (panel.open && !paused && !document.hidden) timer = setTimeout(poll, 2000);
      }
    }
    function stop() { clearTimeout(timer); request?.abort(); }
    panel.addEventListener('toggle', () => panel.open ? poll() : stop());
    pause.addEventListener('click', () => {
      paused = !paused;
      pause.setAttribute('aria-pressed', String(paused));
      pause.textContent = paused ? 'Продолжить график' : 'Пауза графика';
      if (paused) { stop(); status.textContent = 'Пауза просмотра'; } else poll();
    });
    document.addEventListener('visibilitychange', () => document.hidden ? stop() : poll());
    for (const chart of charts) {
      const svg = chart.querySelector('svg');
      let cursorIndex = 0;
      function inspect(index) {
        if (!history?.points.length) return;
        cursorIndex = Math.max(0, Math.min(history.points.length - 1, index));
        const point = history.points[cursorIndex];
        const x = 40 + 304 * (point.at - (history.now - history.seconds * 1000)) / (history.seconds * 1000);
        const cursor = chart.querySelector('.tracking-cursor');
        cursor.setAttribute('x1', x); cursor.setAttribute('x2', x); cursor.setAttribute('visibility', 'visible');
        chart.querySelector('.tracking-reading').textContent = point.x === null ? `${time(point.at)} · нет лица` :
          `${time(point.at)} · X: ${signed(point.x)} · Y: ${signed(point.y)}`;
      }
      svg.addEventListener('pointermove', event => {
        if (!history?.points.length) return;
        const rect = svg.getBoundingClientRect();
        const unit = ((event.clientX - rect.left) / rect.width * 360 - 40) / 304;
        const at = history.now - (1 - unit) * history.seconds * 1000;
        let best = 0;
        history.points.forEach((p, i) => { if (Math.abs(p.at - at) < Math.abs(history.points[best].at - at)) best = i; });
        inspect(best);
      });
      svg.addEventListener('keydown', event => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault(); inspect(cursorIndex + (event.key === 'ArrowRight' ? 1 : -1));
        }
      });
      svg.addEventListener('pointerleave', () => chart.querySelector('.tracking-cursor').setAttribute('visibility', 'hidden'));
    }
  }
})();
