// Playing a film with the television's own player instead of the browser's.
//
// A Samsung set decodes far more than its web engine will admit to: MKV, MPEG-TS, AVI, MOV, WMV,
// MPEG-2, VC-1, AC-3, E-AC-3 — Samsung publish the list per model year. None of it reaches a <video>
// element, so every such file is converted by the server before it can be shown. Handing playback to
// the set instead is what Plex do on these televisions ("Plex for Smart TVs uses playback systems
// provided by the TV manufacturer"), and it is the difference between a film playing untouched and
// the server working hard to make it playable.
//
// The player is webapis.avplay, which belongs to the app's own page — not to this one, which LENTA
// serves. So this module does not call it: it asks the app's page to, over the same postMessage
// bridge the rest of the TV code uses, and presents what comes back as something shaped like a
// <video> element, because that is what player.js already knows how to drive.
//
// Two things about it that are not like a <video> element, and both matter:
//   * The picture is not in the page. The television draws it on its own plane, behind everything,
//     so the page must be transparent where the film should appear — hence `html.av-on`.
//   * It cannot share the screen. Samsung: "A video element and an AVPlay instance cannot play
//     content simultaneously." So this replaces the element for a film; it never runs beside one.
//
// None of this can be tried anywhere but on a television. What can be checked from a desk is that
// every call is real and made in a legal order, which is what tests/test_tizen_bridge.py does against
// a stub of the real API — the same way the Android app is checked without a phone.

import { inTvApp, tvPlatform } from './tv.js';

const host = () => (window.parent !== window ? window.parent : null);

/** Whether the app's page has the television's player to offer. */
export function avplayOffered() {
  const p = tvPlatform();
  return !!(inTvApp() && host() && p && p.avplay);
}

// ---------------------------------------------------------------- what the set can play
// There is no API that reports a television's decoders. What there is, is which television it is:
// the app's page reads the model and panel, and Samsung publish what each model year plays. So these
// are looked up rather than guessed, and rather than asserted for every Samsung set alike — a 2017
// panel takes DTS and stops at level 4.1, a 2018 one does neither.
const ALWAYS = {
  containers: ['mkv', 'mp4', 'ts', 'm2ts', 'avi', 'mov', 'asf', 'wmv', 'flv', 'webm', '3gp', 'vob', 'mpeg'],
  video: ['h264', 'hevc', 'mpeg2video', 'mpeg4', 'vc1', 'vp8', 'vp9', 'mjpeg', 'wmv3'],
  audio: ['aac', 'mp3', 'ac3', 'eac3', 'mp2', 'pcm_s16le', 'pcm_s24le', 'vorbis', 'wmav2', 'flac'],
};

/** The ceilings Samsung publish for this set, and what breaking them does: "Media with higher bit or
 *  frame rate than specifications can cause video or audio playback to stall." A stall, not an error —
 *  which is why they are worth respecting rather than finding out about from the sofa. */
export function tvProfile() {
  const p = tvPlatform();
  if (!p) return null;
  const ver = Number(p.tizenVersion || 0);
  const uhd = p.panel === 'uhd' || p.panel === '8k';
  const caps = {
    containers: ALWAYS.containers.slice(),
    video: ALWAYS.video.slice(),
    audio: ALWAYS.audio.slice(),
    hdr: !!p.hdr,
    // level and bitrate are the two that decide whether a file plays or stalls
    maxLevel: uhd ? 51 : 41,
    maxBitrate: uhd ? 60_000_000 : 20_000_000,
    maxHeight: p.panel === '8k' ? 4320 : uhd ? 2160 : 1080,
    maxStreams: ver && ver >= 6.5 ? 0 : 32,          // 0 = no limit
  };
  // DTS went away with Tizen 4: "DTS audio is not supported by Samsung TV 2018+". A 2017 set has it.
  if (ver && ver < 4) caps.audio.push('dts');
  // AV1 arrived with Tizen 5.5
  if (ver >= 5.5) caps.video.push('av1');
  // Opus only ever plays inside MKV or WebM on these sets, so it is not claimed generally
  return caps;
}

// ---------------------------------------------------------------- the television's player, as an element
let seq = 0;

/** Something shaped enough like a <video> element for player.js to drive, backed by the set's player.
 *
 *  Only the members player.js actually uses are here; anything it does not touch is deliberately
 *  absent rather than faked, so a future use of a missing one fails loudly instead of quietly
 *  returning zero.
 */
export function avplayElement(layer) {
  const parent = host();
  if (!parent) return null;
  const id = `av${++seq}`;
  const listeners = new Map();
  const state = {
    currentTime: 0, duration: 0, paused: true, ended: false, readyState: 0,
    volume: 1, muted: false, playbackRate: 1, videoWidth: 0, videoHeight: 0,
    error: null, buffered: 0, src: '',
  };

  const fire = (name) => {
    const list = listeners.get(name);
    if (list) list.slice().forEach((fn) => { try { fn({ type: name, target: el }); } catch { /* a listener threw */ } });
  };
  const send = (what, extra) => { try { parent.postMessage({ lenta: what, id, ...extra }, '*'); } catch { /* gone */ } };

  const ranges = (end) => ({ length: end > 0 ? 1 : 0, start: () => 0, end: () => end });

  const onMessage = (e) => {
    // The sender must be the window hosting this page. `id` is a counter (`av1`, `av2`…), so it is a
    // label rather than a secret and was never an access check — this path is dormant today, but it
    // would be reachable the moment the set's own player is switched on.
    if (window.parent === window || e.source !== window.parent) return;
    const d = e.data;
    if (!d || !d.lenta || d.id !== id) return;
    switch (d.lenta) {
      case 'av-ready':
        state.duration = d.duration || 0;
        state.videoWidth = d.width || 0;
        state.videoHeight = d.height || 0;
        state.readyState = 1;                                   // HAVE_METADATA
        fire('loadedmetadata');
        break;
      case 'av-playing':
        state.paused = false; state.ended = false; state.readyState = 4;
        fire('playing'); fire('play');
        break;
      case 'av-paused':
        state.paused = true;
        fire('pause');
        break;
      case 'av-time':
        state.currentTime = d.t || 0;
        if (state.readyState < 2) state.readyState = 2;
        fire('timeupdate');
        break;
      case 'av-buffering':
        state.buffered = d.ahead || 0;
        if (d.start) fire('waiting'); else fire('canplay');
        break;
      case 'av-seeked':
        state.currentTime = d.t || state.currentTime;
        fire('seeked');
        break;
      case 'av-ended':
        state.paused = true; state.ended = true;
        fire('ended');
        break;
      case 'av-error':
        // Anything the set could not do with this file. player.js already knows what to do with an
        // error on the element: ask the server to repackage it.
        state.error = { code: 4, message: d.message || 'The television could not play this' };
        fire('error');
        break;
      default:
        break;
    }
  };
  window.addEventListener('message', onMessage);

  const el = {
    avplay: true,
    get currentTime() { return state.currentTime; },
    set currentTime(t) { state.currentTime = t; fire('seeking'); send('av-seek', { t }); },
    get duration() { return state.duration; },
    get paused() { return state.paused; },
    get ended() { return state.ended; },
    get readyState() { return state.readyState; },
    get videoWidth() { return state.videoWidth; },
    get videoHeight() { return state.videoHeight; },
    get error() { return state.error; },
    get buffered() { return ranges(state.currentTime + state.buffered); },
    get seekable() { return ranges(state.duration); },
    get volume() { return state.volume; },
    set volume(v) { state.volume = v; },                   // the set owns its own volume; the remote does it
    get muted() { return state.muted; },
    set muted(m) { state.muted = m; send('av-mute', { on: !!m }); },
    get playbackRate() { return state.playbackRate; },
    set playbackRate(r) { state.playbackRate = r; send('av-rate', { r }); },
    defaultPlaybackRate: 1,
    get src() { return state.src; },
    set src(url) {
      state.src = url;
      state.currentTime = 0; state.duration = 0; state.readyState = 0; state.error = null; state.ended = false;
      if (url) send('av-open', { url });
    },
    play() { send('av-play'); return Promise.resolve(); },
    pause() { send('av-pause'); },
    load() { /* opening is what src does; there is nothing else to prepare */ },
    removeAttribute(name) { if (name === 'src') { state.src = ''; send('av-stop'); } },
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(fn);
    },
    removeEventListener(name, fn) {
      const list = listeners.get(name);
      if (list) listeners.set(name, list.filter((f) => f !== fn));
    },
    canPlayType() { return ''; },      // never asked to decide: tvProfile() is what speaks for this set
    /** Where on the screen the television should draw. Its plane is always a 1920×1080 space whatever
     *  the page's own size, so the rectangle is scaled into it. */
    setRect(r) {
      const sx = 1920 / (window.innerWidth || 1920), sy = 1080 / (window.innerHeight || 1080);
      send('av-rect', { x: Math.round(r.x * sx), y: Math.round(r.y * sy),
                        w: Math.round(r.w * sx), h: Math.round(r.h * sy) });
    },
    /** An embedded audio track, chosen in the set's own player — which a <video> element cannot do at
     *  all, and which is why a change of language costs a conversion today. */
    selectAudio(index) { send('av-track', { kind: 'AUDIO', index }); },
    selectSubtitle(index) { send('av-track', { kind: 'TEXT', index }); },
    destroy() {
      window.removeEventListener('message', onMessage);
      send('av-stop');
      document.documentElement.classList.remove('av-on');
      listeners.clear();
    },
  };
  if (layer) layer.classList.add('av-layer');
  document.documentElement.classList.add('av-on');        // the page goes transparent: see tv.css
  return el;
}
