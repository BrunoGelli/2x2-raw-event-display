'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class FakeElement {
  constructor() {
    this.value = 'all'; this.hidden = false; this.textContent = '';
    this.events = {}; this.attrs = {}; this.dataset = {}; this.focused = false;
    const members = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => members.add(name)),
      remove: (...names) => names.forEach(name => members.delete(name)),
      contains: name => members.has(name)
    };
  }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  dispatch(name, event = {}) { for (const callback of this.events[name] || []) callback(event); }
  setAttribute(name, value) { this.attrs[name] = value; }
  focus() { this.focused = true; }
}

async function main() {
  const elements = Object.fromEntries([
    'module', 'theme-toggle', 'kiosk-theme', 'spin-toggle', 'kiosk-spin',
    'kiosk-enter', 'kiosk-exit', 'kiosk-toolbar'
  ].map(key => [key, new FakeElement()]));
  const events = {}, html = new FakeElement(), body = new FakeElement();
  const document = {
    body, documentElement: html, fullscreenElement: null,
    getElementById: key => elements[key],
    addEventListener(name, callback) { (this.events[name] ||= []).push(callback); },
    dispatch(name, event = {}) { for (const cb of events[name] || []) cb(event); }
  };
  let fullscreenRequests = 0, fullscreenExits = 0;
  html.requestFullscreen = () => {
    fullscreenRequests++;
    document.fullscreenElement = html;
    document.dispatch('fullscreenchange');
    return Promise.resolve();
  };
  document.exitFullscreen = () => {
    fullscreenExits++;
    document.fullscreenElement = null;
    document.dispatch('fullscreenchange');
    return Promise.resolve();
  };
  const pref = new Map(), themes = [], triggerThemes = [], cameraThemes = [], rotations = [];
  const camera = {setTheme: value => cameraThemes.push(value), setAutoRotate: value => rotations.push(value)};
  const window = {
    rawCanvasTheme: value => themes.push(value),
    rawTriggerUI: {setTheme: value => triggerThemes.push(value)},
    rawDisplay3D: () => camera
  };
  const context = {
    document, window, localStorage: {getItem: k => pref.get(k), setItem: (k,v) => pref.set(k,v)},
    clearTimeout() {}, setTimeout() { return 1; }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../raw_display/static/kiosk.js'),'utf8'), context);
  const ui = window.rawKioskUI;
  assert.equal(ui.state.theme, 'dark');
  assert.equal(elements['spin-toggle'].hidden, true);
  elements['theme-toggle'].dispatch('click');
  assert.equal(ui.state.theme, 'light');
  assert.equal(html.dataset.theme, 'light');
  assert.equal(pref.get('rawDisplayTheme'), 'light');
  assert.equal(themes.at(-1), 'light');
  assert.equal(triggerThemes.at(-1), 'light');
  assert.equal(cameraThemes.at(-1), 'light');
  elements.module.value = '3d'; elements.module.dispatch('change');
  assert.equal(elements['spin-toggle'].hidden, false);
  elements['spin-toggle'].dispatch('click');
  assert.equal(ui.state.autoRotate, true);
  assert.equal(rotations.at(-1), true);
  elements.module.value = 'all'; elements.module.dispatch('change');
  assert.equal(rotations.at(-1), false);
  elements.module.value = '3d'; elements.module.dispatch('change');
  assert.equal(rotations.at(-1), true);
  elements['kiosk-enter'].dispatch('click');
  assert.equal(ui.state.kiosk, true);
  assert.equal(body.classList.contains('kiosk-active'), true);
  assert.equal(elements['kiosk-toolbar'].hidden, false);
  assert.equal(fullscreenRequests, 1);
  assert.equal(elements.module.value, '3d', 'enter kiosk preserves the selected view');
  elements['kiosk-theme'].dispatch('click');
  assert.equal(ui.state.theme, 'dark');
  document.fullscreenElement = null;
  document.dispatch('fullscreenchange');
  assert.equal(ui.state.kiosk, false, 'browser Escape/fullscreen exit restores normal layout');
  assert.equal(elements['kiosk-toolbar'].hidden, true);
  assert.equal(elements['kiosk-enter'].focused, true);
  elements['kiosk-enter'].dispatch('click');
  elements['kiosk-exit'].dispatch('click');
  assert.equal(ui.state.kiosk, false);
  assert.equal(fullscreenExits, 1);
  // A browser that denies fullscreen must still permit CSS-only kiosk and Esc exit.
  html.requestFullscreen = () => Promise.reject(new Error('Fullscreen denied'));
  elements['kiosk-enter'].dispatch('click');
  await Promise.resolve();
  assert.equal(ui.state.kiosk, true);
  document.dispatch('keydown', {key: 'Escape'});
  assert.equal(ui.state.kiosk, false);
  ui.on3DReady(camera);
  assert.equal(cameraThemes.at(-1), 'dark');
  assert.equal(rotations.at(-1), true);
  console.log('Kiosk fullscreen/fallback, Escape, view preservation, light theme and camera rotation passed.');
}
main().catch(error => {console.error(error);process.exitCode=1;});
