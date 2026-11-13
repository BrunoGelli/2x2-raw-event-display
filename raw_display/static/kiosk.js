/* Browser-local display presentation controls; no DAQ or stream changes. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let kiosk = false, autoRotate = false, theme = 'dark', fullscreenOwned = false, idleTimer = null;
  const in3D = () => $('module').value === '3d';

  function setTheme(next) {
    theme = next === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    window.rawCanvasTheme?.(theme);
    window.rawTriggerUI?.setTheme?.(theme);
    window.rawDisplay3D?.()?.setTheme?.(theme);
    const label = theme === 'light' ? 'Dark mode' : 'Light mode';
    for (const id of ['theme-toggle', 'kiosk-theme']) {
      $(id).textContent = label;
      $(id).setAttribute('aria-pressed', String(theme === 'light'));
    }
    try { localStorage.setItem('rawDisplayTheme', theme); } catch (_) { /* storage optional */ }
  }

  function setAutoRotate(enabled) {
    autoRotate = !!enabled;
    const label = autoRotate ? 'Auto-rotate: On' : 'Auto-rotate: Off';
    $('spin-toggle').textContent = label;
    $('kiosk-spin').textContent = autoRotate ? 'Stop rotation' : 'Start rotation';
    for (const id of ['spin-toggle', 'kiosk-spin']) {
      $(id).setAttribute('aria-pressed', String(autoRotate));
    }
    window.rawDisplay3D?.()?.setAutoRotate?.(autoRotate && in3D());
  }

  function refreshView() {
    const showRotation = in3D();
    $('spin-toggle').hidden = !showRotation;
    $('kiosk-spin').hidden = !showRotation;
    window.rawDisplay3D?.()?.setAutoRotate?.(autoRotate && showRotation);
  }

  function wakeControls() {
    if (!kiosk) return;
    document.body.classList.add('kiosk-awake');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => document.body.classList.remove('kiosk-awake'), 2800);
  }

  function exitKiosk() {
    if (!kiosk) return;
    kiosk = false;
    clearTimeout(idleTimer);
    document.body.classList.remove('kiosk-active', 'kiosk-awake');
    $('kiosk-toolbar').hidden = true;
    $('kiosk-enter').setAttribute('aria-pressed', 'false');
    if (fullscreenOwned && document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {});
    }
    fullscreenOwned = false;
    $('kiosk-enter').focus();
  }

  function enterKiosk() {
    if (kiosk) return;
    kiosk = true;
    // Respect an existing fullscreen session instead of taking ownership of it.
    fullscreenOwned = !document.fullscreenElement;
    document.body.classList.add('kiosk-active');
    $('kiosk-toolbar').hidden = false;
    $('kiosk-enter').setAttribute('aria-pressed', 'true');
    refreshView();
    wakeControls();
    if (fullscreenOwned) {
      // CSS-only kiosk still works when the browser declines the Fullscreen API.
      try {
        const request = document.documentElement.requestFullscreen?.();
        if (!request) fullscreenOwned = false;
        else request.catch(() => { fullscreenOwned = false; });
      } catch (_) { fullscreenOwned = false; }
    }
  }

  $('kiosk-enter').addEventListener('click', enterKiosk);
  $('kiosk-exit').addEventListener('click', exitKiosk);
  $('theme-toggle').addEventListener('click', () => setTheme(theme === 'dark' ? 'light' : 'dark'));
  $('kiosk-theme').addEventListener('click', () => setTheme(theme === 'dark' ? 'light' : 'dark'));
  $('spin-toggle').addEventListener('click', () => setAutoRotate(!autoRotate));
  $('kiosk-spin').addEventListener('click', () => setAutoRotate(!autoRotate));
  $('module').addEventListener('change', refreshView);
  document.addEventListener('pointermove', wakeControls, {passive: true});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && kiosk) exitKiosk();
  });
  document.addEventListener('fullscreenchange', () => {
    // Esc is normally handled by the browser before the keydown reaches the page.
    if (kiosk && !document.fullscreenElement) exitKiosk();
  });

  window.rawKioskUI = {
    get state() { return {kiosk, autoRotate, theme}; },
    setTheme, setAutoRotate, refreshView,
    on3DReady(view) { view.setTheme(theme); view.setAutoRotate(autoRotate && in3D()); }
  };
  let stored = 'dark';
  try { stored = localStorage.getItem('rawDisplayTheme') || 'dark'; } catch (_) { /* storage optional */ }
  setTheme(stored);
  setAutoRotate(false);
  refreshView();
})();
