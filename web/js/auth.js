// First-run setup, the "who's watching" sign-in screen, and password changes.
import { api } from './api.js';
import { $, html, modal, mount, toast } from './ui.js';

export function renderAuth(server, onSignedIn) {
  const view = $('#view');
  $('#rail').innerHTML = '';
  if (server.setup_required) return renderSetup(view, onSignedIn);
  renderPicker(view, server, onSignedIn);
}

function renderSetup(view, onSignedIn) {
  mount(view, html`<div class="auth"><div class="auth-inner">
    <img class="auth-logo" src="img/lenta-logo.png" alt="LENTA">
    <h1>Set up your media server</h1>
    <form class="auth-form" id="setup">
      <div class="field"><label for="sn">Server name</label><input class="input" id="sn" value="LENTA" maxlength="40">
        <span class="hint">Shown in the sidebar and browser tab.</span></div>
      <div class="field"><label for="su">Administrator username</label><input class="input" id="su" autocomplete="username" required autofocus></div>
      <div class="field"><label for="sp">Password</label><input class="input" id="sp" type="password" autocomplete="new-password" required minlength="6"></div>
      <div class="field"><label for="sp2">Repeat password</label><input class="input" id="sp2" type="password" autocomplete="new-password" required></div>
      <p class="error-text" id="err"></p>
      <button class="btn primary">Create administrator</button>
    </form></div></div>`);
  $('#su').focus();
  $('#setup').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('#err');
    if ($('#sp').value !== $('#sp2').value) { err.textContent = 'The two passwords are different.'; return; }
    try {
      await api.post('/api/setup', { server_name: $('#sn').value, username: $('#su').value, password: $('#sp').value });
      location.hash = '#/admin/libraries';
      await onSignedIn();
      toast('Server ready. Add a library to start scanning your media.');
    } catch (ex) { err.textContent = ex.message; }
  });
}

const corner = () => html`<img class="auth-corner" src="img/lenta-wordmark.png" alt="">`;

function renderPicker(view, server, onSignedIn) {
  const users = server.users || [];
  const showForm = (username) => {
    mount(view, html`<div class="auth auth-signin">${corner()}<div class="auth-inner">
      <img class="auth-logo" src="img/lenta-logo.png" alt="LENTA">${server.name && server.name.toUpperCase() !== 'LENTA' ? html`<div class="auth-server">${server.name}</div>` : ''}
      ${username ? html`<div class="profiles" style="margin-bottom:18px"><div class="profile" style="cursor:default">
          <span class="avatar" style="background:${users.find(u => u.username === username)?.color || '#F2B33D'}">${username[0].toUpperCase()}</span>${username}</div></div>`
        : html`<h1>Sign in</h1>`}
      <form class="auth-form" id="login">
        <div class="field" ${username ? 'hidden' : ''}><label for="lu">Username</label><input class="input" id="lu" autocomplete="username" value="${username || ''}" required></div>
        <div class="field"><label for="lp">Password</label><input class="input" id="lp" type="password" autocomplete="current-password" required></div>
        <p class="error-text" id="err"></p>
        <button class="btn primary">Sign in</button>
        ${users.length ? html`<p style="text-align:center;margin-top:20px"><button type="button" class="linkish" id="switch">Choose a different profile</button></p>` : ''}
      </form></div></div>`);
    (username ? $('#lp') : $('#lu')).focus();
    $('#switch')?.addEventListener('click', () => renderPicker(view, server, onSignedIn));
    $('#login').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        await api.post('/api/auth/login', { username: $('#lu').value, password: $('#lp').value });
        await onSignedIn();
      } catch (ex) { $('#err').textContent = ex.message; $('#lp').select(); }
    });
  };
  if (!users.length) return showForm('');
  mount(view, html`<div class="auth auth-who">${corner()}<div class="auth-inner">
    <img class="auth-logo" src="img/lenta-logo.png" alt="LENTA">${server.name && server.name.toUpperCase() !== 'LENTA' ? html`<div class="auth-server">${server.name}</div>` : ''}
    <h1>Who's watching?</h1>
    <div class="profiles">${users.map(u => html`<button class="profile" data-user="${u.username}">
      <span class="avatar" style="background:${u.color || '#F2B33D'}">${u.username[0].toUpperCase()}</span><span class="profile-name">${u.username}</span></button>`)}</div>
    <button class="linkish" id="other">Sign in with another account</button>
  </div></div>`);
  view.querySelectorAll('[data-user]').forEach(b => b.addEventListener('click', () => showForm(b.dataset.user)));
  $('#other').addEventListener('click', () => showForm(''));
  view.querySelector('.profile')?.focus();
}

export function changePasswordDialog() {
  const m = modal(html`<h2>Change password</h2>
    <form id="pw">
      <div class="field"><label for="cur">Current password</label><input class="input" id="cur" type="password" autocomplete="current-password" required autofocus></div>
      <div class="field"><label for="np">New password</label><input class="input" id="np" type="password" autocomplete="new-password" required minlength="6"></div>
      <p class="error-text" id="pwerr"></p>
      <div class="foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">Change password</button></div>
    </form>`);
  m.box.querySelector('#pw').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api.post('/api/auth/password', { current: m.box.querySelector('#cur').value, new: m.box.querySelector('#np').value });
      m.close();
      toast('Password changed');
    } catch (ex) { m.box.querySelector('#pwerr').textContent = ex.message; }
  });
}
