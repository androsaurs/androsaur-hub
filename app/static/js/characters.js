document.querySelectorAll('[data-preview]').forEach(button => {
  button.addEventListener('click', () => {
    const preview = button.parentElement.querySelector('object');
    const doc = preview.contentDocument;
    if (!doc) return;
    doc.querySelectorAll('.mouth').forEach(mouth => {
      mouth.style.animationPlayState = 'running';
      setTimeout(() => { mouth.style.animationPlayState = 'paused'; }, 1800);
    });
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      preview.animate([{transform:'rotate(0)'},{transform:'rotate(-6deg)'},{transform:'rotate(6deg)'},{transform:'rotate(0)'}], {duration:700});
    }
  });
});
