# Changelog

## 1.2.0

**New**
- **Audiobooks** library type.
  - Books are found in any folder layout: one file per book in a series folder, a folder of chapter files, `Author/Book` folders, or a single `.m4b`.
  - Book pages show the author, narrator, series, chapters and length.
  - Covers and descriptions come from Google Books and Open Library, with no key needed. Fix match and Edit metadata work for books, with Author, Narrator, Series and Number in series fields.
  - Playback offers speed, back 15 s and forward 30 s, and chapter jumps. Your place is saved on the server and books appear in *Continue watching*.
  - Book pictures have a Play button and a ⋮ menu. A Music library can be switched to Audiobooks and back.
- **Plex-style player bar**, the same for minimised videos, music, audiobooks and the full-screen player.
  - A time bar across the top with a preview picture.
  - Previous, back, play/pause, forward, next and stop.
  - ⋮, repeat, shuffle, settings, play queue and an upright volume bar.
  - The minimised video plays in the bar's picture. On desktop the page's scroll bar ends above the bar.
- **Playback settings** panel: quality, playback speed, audio stream, subtitles, subtitle colour, position and size, subtitle offset and auto play.
- **Play queue** for TV episodes: the episode playing and the ones after it.
- **HTTP and HTTPS side by side**, set up from *Settings › Network*. The certificate can be LENTA's own, an uploaded one (PEM, CRT+KEY or PFX) or files on the server (e.g. certbot). Each protocol has its own sign-in. `deploy/reset-network.sh` is the way back if a setting locks you out.
- **One Settings page** (the gear) with a sidebar for your settings and the server's; old *Server admin* links still work. Every page has its own *Reorder* layout.

**Improved**
- Season tabs on every show page instead of a dropdown for long shows.
- Music: repeat (all / one), shuffle and a play queue.
- While something plays in the player bar, the Home banner trailer and hover-preview trailers keep playing, but muted. Hover previews no longer disappear.
- In *Continue watching*, clicking a hover preview's picture resumes that title, even while something else plays.
- The main side menu folds while you are in Settings (not in the Redline theme).
- The library page has a ⋮ menu (scan, edit, change type) in every theme.

**Fixed**
- The Home banner trailer could start playing during a film.
- The Settings sidebar jumped back to the top after each click.
- New settings blocks were placed at the bottom of a saved layout and were easy to miss.
- Audio files in music and audiobook libraries are no longer reported as "No video files found".
