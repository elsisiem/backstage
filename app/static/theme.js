/* Shared light/dark switch for the landing and tour pages. The console has its own copy in app.js. */
(() => {
  const root = document.documentElement;
  const get = () => root.getAttribute('data-theme') || 'light';
  const paint = () => document.querySelectorAll('[data-theme-seg] button').forEach((b) => b.classList.toggle('on', b.dataset.theme === get()));
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-theme-seg] button');
    if (!b) return;
    root.setAttribute('data-theme', b.dataset.theme);
    try { localStorage.setItem('bs-theme', b.dataset.theme); } catch { /* private mode */ }
    paint();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', paint); else paint();
})();
