/* The hub renders the same escaped partials for initial and live battery states. */
(() => {
  const phones = [...document.querySelectorAll('[data-phone-battery]')];
  const stands = [...document.querySelectorAll('[data-stand-battery]')];
  if (!phones.length) return;
  let busy = false;
  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    try {
      const reply = await fetch('/devices/battery.json', {
        cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(8000)
      });
      if (!reply.ok || !reply.headers.get('content-type')?.includes('application/json')) throw Error('unavailable');
      const data = await reply.json();
      for (const [nodes, key, attr] of [[phones, 'phones', 'phoneBattery'], [stands, 'stands', 'standBattery']]) {
        for (const node of nodes) {
          const html = data[key]?.[node.dataset[attr]];
          if (typeof html === 'string') node.innerHTML = html;
          else node.textContent = 'Состояние недоступно. Обновите карточку устройства.';
        }
      }
    } catch (_) {
      for (const node of [...phones, ...stands]) {
        const hint = node.querySelector('.battery-freshness');
        if (hint) hint.textContent = 'Нет свежего ответа хаба · показаны последние данные.';
      }
    } finally { busy = false; }
  }
  setInterval(refresh, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh();
})();
