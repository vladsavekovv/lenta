// Settings › Streaming services: which services this household pays for, so Home can show what they
// are showing. LENTA holds none of it — the rows are a shop window, and every title opens the service
// in a new tab.
//
// The list of services is not written down anywhere in LENTA. It comes from TMDB for the country set in
// Settings › Metadata, so what appears here is what somebody in that country could actually subscribe
// to: Nova Play in Bulgaria, Hulu in the United States, and neither in the other.
import { api } from './api.js';
import { $, $$, html, mount, toast } from './ui.js';

// The language codes TMDB actually returns, named. Anything not here shows as its code, which is
// honest: inventing a name for a code we do not know would be worse than showing the code.
const LANGS = {
  en: 'English', bg: 'Bulgarian', ru: 'Russian', de: 'German', fr: 'French', es: 'Spanish', it: 'Italian',
  pt: 'Portuguese', nl: 'Dutch', pl: 'Polish', sv: 'Swedish', no: 'Norwegian', da: 'Danish', fi: 'Finnish',
  cs: 'Czech', sk: 'Slovak', hu: 'Hungarian', ro: 'Romanian', el: 'Greek', tr: 'Turkish', uk: 'Ukrainian',
  sr: 'Serbian', hr: 'Croatian', he: 'Hebrew', ar: 'Arabic', fa: 'Persian', hi: 'Hindi', ta: 'Tamil',
  te: 'Telugu', ml: 'Malayalam', kn: 'Kannada', mr: 'Marathi', bn: 'Bengali', pa: 'Punjabi', ur: 'Urdu',
  ja: 'Japanese', ko: 'Korean', zh: 'Chinese', cn: 'Chinese', th: 'Thai', vi: 'Vietnamese', id: 'Indonesian',
  tl: 'Tagalog', ms: 'Malay', is: 'Icelandic', et: 'Estonian', lv: 'Latvian', lt: 'Lithuanian', ca: 'Catalan',
};

const when = (t) => {
  if (!t) return 'not yet';
  const mins = Math.round((Date.now() / 1000 - t) / 60);
  if (mins < 2) return 'just now';
  if (mins < 90) return `${mins} minutes ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 36 ? `${hrs} hours ago` : `${Math.round(hrs / 24)} days ago`;
};

export async function renderStreaming(body) {
  const draw = async (refresh = false) => {
    let o;
    try {
      o = await api.get(`/api/admin/streaming${refresh ? '?refresh=1' : ''}`);
    } catch (ex) {
      mount(body, html`<section class="panel"><h3>Streaming services</h3>
        <p class="lead">The list could not be fetched: ${ex.message}</p>
        <p class="lead">This needs the TMDB key from <a href="#/settings/metadata-sources">Settings › TMDB and language</a>.
          <button class="btn small" id="sv-retry">Try again</button></p></section>`);
      $('#sv-retry')?.addEventListener('click', () => draw(true));
      return;
    }
    if (!body.isConnected) return;
    const picked = new Set(o.services || []);
    // Chosen first, then the order JustWatch gives for this country, which puts the services people
    // here actually use at the top instead of alphabetically or by whoever is biggest worldwide.
    const list = [...(o.available || [])].sort((a, b) => (picked.has(b.id) ? 1 : 0) - (picked.has(a.id) ? 1 : 0));

    mount(body, html`
      <section class="panel"><h3>Streaming services</h3>
        <p class="lead">Tick the services you subscribe to and Home gains a row of what each is showing in
          <b>${o.region}</b>. Nothing plays here: a title opens the service in a new tab, the same as
          clicking it on their own site.</p>
        ${!o.available?.length ? html`<p class="lead">No services came back for ${o.region}. Check the TMDB key
          and the country in <a href="#/settings/metadata-sources">Settings › TMDB and language</a>.</p>` : ''}
        <div class="sv-grid">
          ${list.map(p => html`
            <label class="sv-item${picked.has(p.id) ? ' on' : ''}">
              <input type="checkbox" data-sv="${p.id}" ${picked.has(p.id) ? 'checked' : ''}>
              ${p.logo ? html`<img loading="lazy" src="${p.logo}" alt="">` : html`<span class="sv-blank"></span>`}
              <span class="sv-title">${p.name}</span>
            </label>`)}
        </div>
      </section>

      <section class="panel"><h3>What is shown</h3>
        <label class="check"><input type="checkbox" id="sv-hide" ${o.hide_owned ? 'checked' : ''}>
          <span>Leave out anything already on this server</span></label>
        <p class="lead">On by default, and worth leaving on: being sent to Netflix for a film sitting on your
          own disk is the one thing a media server should never do. Matched by TMDB number, so it works
          whatever your file is called.</p>
        <p class="lead">A row is named <i>Popular on …</i> rather than <i>Top 10</i> because that is what the
          data is: TMDB's own popularity, narrowed to that service in your country. The services' own
          charts are not something TMDB can see, so LENTA does not print a rank it would be inventing.</p>
        <h4 style="margin:22px 0 2px">Genres</h4>
        <p class="lead">Leave all of these unticked and the rows show whatever is most popular on the
          service. Tick some and the rows are narrowed to them — any of them, not all at once.</p>
        <div class="sv-langs">
          ${(o.genres?.all || []).map(g => html`
            <label class="sv-lang${g.on ? ' on' : ''}">
              <input type="checkbox" data-genre="${g.name}" ${g.on ? 'checked' : ''}>
              <span>${g.name}</span>${g.films_only ? html`<i>films only</i>` : ''}
            </label>`)}
        </div>
        <p class="lead" style="margin-top:8px">The numbers behind these come from TMDB itself, so they
          match what it holds. TMDB files series differently from films: it has one <i>Sci-Fi &amp;
          Fantasy</i> where films have two, one <i>Action &amp; Adventure</i> likewise, and no Horror,
          Romance or Thriller for series at all. Those three are marked <i>films only</i> — picking one
          narrows your films and leaves your series as they were.</p>

        <h4 style="margin:22px 0 2px">Languages</h4>
        <p class="lead">What a service carries in your country is not all for you. These are the languages
          that actually turned up in your rows — tick any you would rather not be offered.</p>
        ${!o.languages?.length ? html`<p class="lead">Nothing fetched yet, so there is nothing to list.</p>` : html`
        <div class="sv-langs">
          ${o.languages.map(l => html`
            <label class="sv-lang${l.off ? ' off' : ''}">
              <input type="checkbox" data-lang="${l.code}" ${l.off ? 'checked' : ''}>
              <span>${LANGS[l.code] || l.code.toUpperCase()}</span><i>${l.count}</i>
            </label>`)}
        </div>
        <p class="lead" style="margin-top:8px">A ticked language is left out. LENTA fetches a second page
          from each service while any are ticked, so the rows still fill up rather than going short.</p>`}

        <p class="lead">Rows can be renamed, reordered and hidden like any other in
          <a href="#/settings/home">Settings › Home screen</a>. They are left off televisions, where there
          is no second tab to open them in.</p>
      </section>

      <section class="panel"><h3>Updating</h3>
        <p class="lead">${o.titles} titles, last fetched ${when(o.updated)}. LENTA refreshes once a day in the
          background, so Home never waits on anything.${o.error ? html` Last attempt failed: ${o.error}` : ''}</p>
        <button class="btn" id="sv-now" ${o.running ? 'disabled' : ''}>${o.running ? 'Fetching…' : 'Fetch now'}</button>
        <p class="lead" style="margin-top:14px">${o.attribution}. LENTA may show this data only while that
          credit is displayed, so please keep it if you change how these rows look.</p>
      </section>`);

    const save = async (patch) => {
      try {
        await api.post('/api/admin/streaming', patch);
      } catch (ex) {
        toast(`Not saved: ${ex.message}`);
        return;
      }
      await draw();
    };
    $$('[data-sv]').forEach(el => el.addEventListener('change', () => {
      save({ services: $$('[data-sv]').filter(x => x.checked).map(x => Number(x.dataset.sv)) });
    }));
    $('#sv-hide')?.addEventListener('change', (e) => save({ hide_owned: e.target.checked }));
    $$('[data-genre]').forEach(el => el.addEventListener('change', () => {
      save({ genres: $$('[data-genre]').filter(x => x.checked).map(x => x.dataset.genre) });
    }));
    $$('[data-lang]').forEach(el => el.addEventListener('change', () => {
      save({ languages_off: $$('[data-lang]').filter(x => x.checked).map(x => x.dataset.lang) });
    }));
    $('#sv-now')?.addEventListener('click', async () => {
      try {
        await api.post('/api/admin/streaming/refresh', {});
        toast('Fetching what the services are showing…');
        setTimeout(draw, 2500);
      } catch (ex) {
        toast(ex.message);
      }
    });
  };
  await draw();
}
