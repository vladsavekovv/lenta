// Installing LENTA as an app on phones, tablets and computers.
// Chrome / Edge / Android: the browser's install prompt, offered in the account menu and in Settings.
// iPhone / iPad: Safari › Share › Add to Home Screen (Safari has no install prompt to call).
let deferred = null;
const listeners = new Set();

/** Inside the LENTA Android app (its WebView exposes window.LentaApp). */
export const inAndroidApp = () => !!window.LentaApp;
export const isAndroid = () => /Android/i.test(navigator.userAgent);

export const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
export const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
export const installAvailable = () => !!deferred && !isStandalone() && !inAndroidApp();
export const onInstallChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

export async function promptInstall() {
  if (!deferred) return false;
  const ev = deferred;
  deferred = null;
  ev.prompt();
  const { outcome } = await ev.userChoice.catch(() => ({ outcome: 'dismissed' }));
  listeners.forEach(fn => fn());
  return outcome === 'accepted';
}

/** What to tell someone who wants the app but has no install prompt. */
export function installHelp() {
  if (isStandalone()) return 'LENTA is running as an installed app.';
  if (isIOS()) return 'In Safari, tap Share, then "Add to Home Screen". LENTA then opens full screen like an app.';
  if (!window.isSecureContext) return 'Browsers only install apps from secure (https) addresses. On this address, use your browser menu › "Add to Home screen" for a shortcut, or open LENTA through https (see the install guide, "Access from outside your home") to install it as a full app.';
  return 'Use your browser menu › "Install app" (or "Add to Home screen").';
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferred = e;
  listeners.forEach(fn => fn());
});
window.addEventListener('appinstalled', () => { deferred = null; listeners.forEach(fn => fn()); });

if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
if (isStandalone() || inAndroidApp()) document.documentElement.classList.add('standalone');
if (inAndroidApp()) document.documentElement.classList.add('android-app');
