# Changelog

## 1.8.17 — The TV app stops fighting the television

- **Live TV would not start on the TV, and the whole app was slow.** One cause for both: the player
  preferred hls.js everywhere, so the television was reading each segment of the broadcast and rewriting
  it in JavaScript, continuously, on a processor built for a fraction of that work. A TV decodes HLS in
  hardware. It now plays the stream itself, with hls.js kept as a fallback — if no picture appears within
  twelve seconds, or the set reports an error, the library takes over and that set is not asked again.
  This is also why films played and channels did not: a film can be sent as a plain file, a channel cannot.
- **The remote control felt dead because the set had no time to answer it.** The backdrop on a detail page
  was a full-screen image running a 48-second animation with a blur over it, so the TV re-blurred a
  1920×1080 surface every frame, for ever; there were twenty-six frosted-glass panels besides. On a TV all
  of that is now flat colour (`web/css/tv.css`), nothing moves unless you asked it to, and while something
  is playing the page behind the player is not drawn at all.
- **Fixed: in the Netflix theme on a TV, the menu never showed which item was selected.** The rule meant
  for it was written `html.tvhtml`, which matches nothing at all. On a television the focus ring is the
  cursor, so this looked exactly like a broken remote. A bad selector throws nothing and warns nobody, so
  the test suite now fails on any misspelt `html.tv` rule.
- **Each press of an arrow key asked the engine where every card on the page was, three times over.**
  Nothing moves during a single press, so the answers cannot change: they are now worked out once.
  Measured on an eighty-five card page, 840 layout questions became 400 — one per element.
- **Half a megabyte of hls.js is no longer loaded before the app can draw anything** on a television, since
  it is usually not needed there. It is fetched only if native playback turns out not to work.
- No need to reinstall the app on the TV: it loads the page from the server, so updating the server and
  reopening the app is enough.

## 1.8.16 — Signing with a Samsung certificate, properly this time

- **Fixed: "Invaild password" with a password that was perfectly correct.** Samsung's tools hand the
  certificate password to the desktop keyring — gnome-keyring or KWallet — and a container has neither.
  So adding the signing profile reported success while storing something it could never read back, and
  signing then failed against a password that opens the files by hand. LENTA now writes the signing
  profile itself, with the password in it, which is what Tizen Studio does with secure storage switched
  off. Nothing about the certificate or the password needed changing.
- **The password is checked before any of that**, against the certificate files themselves, so a genuinely
  wrong one is one line of plain English rather than a Java stack trace several minutes later.

## 1.8.15 — Signing with a Samsung certificate works

- **Fixed: the app would not sign with a Samsung certificate.** Adding the certificate as a signing profile
  succeeded, but the packager never used it: Samsung's CLI keeps a separate setting for where profiles
  live, and mentions it only as an aside when adding one. The installer now sets it, which is the whole
  difference between "An error has occurred" and a signed app.
- **The packaging step prints its reason too**, from the same log inside the container that was being
  discarded. Both halves of the signing now say what went wrong rather than pointing at a vanished file.

## 1.8.14 — The TV installer stops hiding the certificate error

- **Fixed: "An error has occurred. See the log file" — and the log was unreachable.** Samsung's tools write
  their reason into a log inside the container the installer runs them in, which is thrown away the moment
  it exits. That log is now printed when making the signing profile fails, along with a plain explanation
  that the usual cause is the certificate password.

## 1.8.13 — The TV installer says what went wrong

- **Fixed: a failed install on the TV reported only an exit code.** Samsung's own message was captured into
  a variable and printed afterwards — but a failure ended the script at the assignment, before anything
  could be printed. The reason the TV gave is the entire point, and it was the one thing thrown away.
  It is now always shown, and a refused install points at the Samsung certificate instructions.
- The Samsung TV guide has the corrected path to Developer Mode on recent firmware: **App Settings is the
  last item at the bottom of the Apps page**, not a gear at the top. It also now covers sets where
  Developer Mode is gone entirely — the TV's own browser needs none of it.

## 1.8.12 — Samsung TVs, 2017 to today

- **Fixed: 42 layout fallbacks for older TVs matched nothing at all.** The build works out which boxes
  need a stand-in for `aspect-ratio` and for `gap` — neither of which a TV engine before 2023 knows — but
  any rule written under a comment had that comment caught up in its selector, which made the fallback
  invalid. It was swallowed silently, so the boxes simply had no size on the TV. Channel tiles and much
  else were affected.
- **Fixed: the old-TV client was a day out of date**, which on a Samsung from 2017 to 2022 meant no
  channel logos, no channel rows on Home, no programme guide — none of it, with nothing to show anything
  was wrong. A new check refuses to let the client be older than the app it is built from.
- **Channel ▲ and ▼ on the remote change channel** while you are watching live TV, rounding from the last
  channel back to the first. The channel you came from is kept warm, so going back up is instant.
- The old-TV client is checked for syntax no engine of that age can read, and for the fallbacks it needs.

## 1.8.11 — Channels stay ready when you leave them

- **Going back to a channel is now instant.** A channel you leave is no longer torn down after twenty
  seconds: the tuner stays locked and the converter keeps writing for **30 minutes**, so returning joins
  what is already running instead of tuning and waiting all over again.
- **Starting another channel does not disturb the first.** Each kept channel holds its own tuner input,
  so they sit side by side for as long as there are inputs to hold.
- **Up to five are kept, stalest out.** When there are more than five, or when a channel you want now
  needs an input, the one left longest ago gives way — never one somebody is watching, and never a more
  recent one. A channel being watched is never interrupted for any of this.
- **A kept channel that has stopped producing is let go** rather than holding an input you would find
  dead on return.
- Settings › Live TV marks which channels are being kept ready and for how long they have been idle.

## 1.8.10 — No name under a picture that already carries it

- **The HBO Max theme no longer repeats the title under the tile.** It was the only theme without that
  rule: a card showing the title's own artwork had its name written underneath as well, which made a
  shelf of films read like a list. A text-free picture now gets the name written across it instead, as
  the Netflix theme already did.
- **Live TV keeps its captions**, in every theme — a station logo names the channel, not what is on it,
  so the programme underneath is the only thing saying what you would be watching.

## 1.8.9 — Making sure the guide cannot spoil a channel

- **Fixed a race that could have taken a tuner out from under a picture.** The sweep chose a spare input
  and then said it had taken it, and between those two steps a channel starting at that moment could be
  handed the same input — after which the sweep would retune the frontend and the channel would stop.
  Choosing and claiming now happen together, under the same lock a channel start uses.
- **Fixed: a guide filter could outlive the channel it rode in on.** It was only asked to stand down when
  a table arrived, so on a quiet multiplex it could sit open for up to three minutes after the channel
  ended — on a frontend that may since have been retuned. It is now asked between every read.
- **Collecting the guide can be switched off, each way separately** (Settings › Live TV): while watching,
  and tuning spare inputs. They carry different risks, so they can be ruled out one at a time.
- **The stream's own log now says when the guide is being read**, so if a channel does misbehave it is
  plain whether collecting was going on at the time rather than a matter of opinion.

## 1.8.8 — A guide page, and the rest of the channels

- **A proper guide.** Live TV › *What's on*: channels down the side, time across the top, Earlier / Now /
  Later to move about, and a line showing where you are. Programmes are drawn at their real length, so a
  two-hour film is twice the width of an hour of news rather than everything being the same box. Click
  one for its description, or the channel to start watching it.
- **The multiplexes nobody watches now fill themselves in.** Watching only ever collects the guide for
  the multiplex you are on — about nine of your channels. LENTA now also tunes the others whenever a
  tuner input is spare, taking the one whose guide is stalest first.
- **A viewer always outranks the sweep.** It holds an input only while nothing else wants one, declares
  which input it has so nothing hands it out twice, and gives it up within a fraction of a second when
  somebody asks for a channel — before any channel that is merely being kept warm is disturbed.

## 1.8.7 — The programme guide

- **LENTA now knows what is on.** The guide is not fetched from a website and needs no subscription or
  XMLTV file: a DVB multiplex carries its own, describing every service on it. Watch any channel for a
  minute or two and the guide for every channel on that multiplex fills itself in. A few evenings of
  ordinary viewing and the whole line-up has one.
- **Collecting costs nothing you would notice.** It is a filter of its own on the tuner carrying a few
  tens of kbit/s of text, while the picture arrives through the stream the converter reads. If it fails
  it says so in the log and the channel plays on regardless.
- **What is on appears under every channel** — on the Live TV page and under the channel tiles on Home,
  with a line showing how far through the programme you are, and the start and end times in your own
  clock rather than the broadcast's UTC.
- **The guide survives a rescan.** Programmes are filed against the channel's identity in the network,
  not its row in the database, so rebuilding the channel list does not throw the guide away.
- Settings › Live TV reports how many programmes are held, for how many channels, and how far ahead.

## 1.8.6 — Two bits of tidying

- **Fixed: a line under the menu in the HBO Max theme.** The bar carries a border that is invisible until
  you scroll, when it becomes the divider under the black bar — but the fade behind the bar was being
  painted underneath that border and left a dark seam along it. The fade now stops short of the border.
  The divider still appears when you scroll, as it was meant to.
- **Channel tiles on Home no longer show the channel number.** The Live TV page still numbers its
  channels, because that grid is your provider's line-up; a row you picked by hand is not, and numbers
  out of sequence there only looked like gaps. Every theme.

## 1.8.5 — Channel logos sit properly in every theme

- **Fixed: a tall station logo stretched its tile.** In the Netflix theme a channel tile grew to fit a
  tall logo — Viasat History, for one — leaving one card taller than its neighbours and its name out of
  line with the rest of the row.
- **Logos now keep their own shape.** They were being given a fixed box to fill, which squashed a wide
  wordmark and cropped a tall crest. Each logo is bounded on both sides instead and sits at its own
  proportions, a little larger than before, so a row of them reads evenly whatever shape they are.
- The Live TV page and the Home rows now size artwork identically, so a channel looks the same in both.

## 1.8.4 — Channels start faster

- **A channel appears in about half the time.** Nothing can play until a few whole segments exist, and
  segments were two seconds each — so three of them cost six seconds before the first picture, whatever
  the tuner did. Segments are now a second and two is enough to start.
- **Every diagnostic line is now timed.** *Show what the stream is doing* puts a stopwatch against each
  step, so "slow to start" can be told apart: a tuner slow to lock, tables slow to come round, or the
  converter simply waiting for picture.
- **Fixed: flipping channels could run out of tuner inputs with nobody watching.** A channel is kept warm
  for twenty seconds after you leave, so flipping back is instant — but those warm channels still held an
  input, and flipping through more of them than the stick has inputs ended in "every tuner input is in
  use" while nothing was actually playing. A channel someone is asking for now takes the input back from
  whichever has been empty longest.

## 1.8.3 — A Home section of your own channels

- **Home sections can now be live channels.** Settings › Home screen › *New section*: tick **Live TV**
  under *Fill it from* and **Channels** appears in the *Shows* list. Choose it and a **Choose channels**
  button opens the whole line-up, each with a tick — ticked is in the section, unticked is out. Search
  the list, or use *All* and *None* on whatever the search is showing. The section shows them in your
  provider's own numbering, not the order you ticked them, and clicking one starts watching.
- **A channel that disappears takes care of itself.** Rescan and a channel that no longer exists simply
  drops out of the section rather than leaving a dead tile.
- **Fixed: a monogram could outlive the logo that replaced it.** Channel artwork was cached for an hour,
  so after finding logos the old monograms stayed put and no amount of reloading shifted them — only
  clearing the browser's cache did. Artwork is now checked on each visit and changes as soon as it changes.
- **Fixed: Escape closed every open dialog, not just the top one.** Backing out of a picker no longer
  throws away the section you were in the middle of editing.

## 1.8.2 — Find channel logos actually does something

- **Fixed: *Find logos* did nothing at all.** The dialog took your answer and threw it away, so pressing
  the button looked like pressing a dead key. It now runs.
- **Logos arrive in seconds rather than a minute.** A hundred channels meant a hundred small files fetched
  one after another; they come a few at a time now.
- **When a collection cannot be read, LENTA says why.** "It did not answer" sent you looking in the wrong
  place — the message now repeats what the other end actually said, whether that is a rate limit, a moved
  repository or a name that does not resolve.

## 1.8.1 — Channel artwork

- **Every channel has artwork now.** A broadcast carries no logo, so one is found by matching the channel's
  name against a collection of them — *Find channel logos* in Settings › Live TV. Name your country and
  press the button; a collection is set up by default, and a different one can be used instead. The
  collection is asked what it holds and the names are matched against that, rather than guessing at
  addresses, so a source organised differently matches fewer channels instead of filling the page with
  broken images.
- **Your country decides which version of a channel you get.** Plenty of names exist in a dozen countries;
  naming yours picks the local one, so Bloomberg TV is the Bulgarian one rather than the Turkish.
- **Names are matched forgivingly**: *Star Channel HD* and *Star Channel* are one channel, so are *24Kitchen*
  and *24 Kitchen*, and Cyrillic is transliterated — *СКАТ* finds a file named *skat*, *Наше Кино* finds
  *nashe kino*. Country-tagged filenames like *btv-bg.png* are allowed for. Channels that are genuinely
  different stay apart: *bTV* never matches *bTV Action*.
- **Whatever is unmatched gets a monogram** drawn from its own name — initials on a coloured tile, the same
  colours for the same channel every time, in the name's own alphabet. A wall of those looks deliberate; a
  wall of blank boxes looks broken.
- A logo you set by hand, or one a playlist supplied, is never overwritten.
- Groundwork for the programme guide: the broadcast's own event tables (EIT) are now parsed — titles,
  descriptions, times and genres, in any of the alphabets DVB uses. Nothing shows it yet.

## 1.7.4

- **Opening Settings › Live TV no longer disturbs the channel you are watching.** That page asks every tuner
  input what it is, which means opening it and sending it control commands — and on a USB stick, more so one
  passed through to a virtual machine, that traffic competes with the stream the same stick is carrying. An
  input that is delivering a channel is now reported from what is already known, and left alone.
- **A live channel carries on in the mini player when you browse away**, instead of being closed. A film can
  be resumed; a broadcast cannot, and closing it hands the tuner back. Going to Settings to read the log no
  longer ends the thing you are reading about.
- **Returning to a channel that is already playing brings it back up** rather than starting it again. The
  round trip through Settings and back was tearing the stream down and re-tuning for no reason.

## 1.7.3

- **One bad audio frame no longer takes the channel down.** Broadcast audio arrives with the occasional
  corrupt frame, and the muxer was waiting for the troubled stream — so it stopped writing segments while
  FFmpeg was still running happily, and the channel starved. The two streams are now written independently,
  and re-encoded audio is resampled so a dropped frame is filled rather than passed on.
- **A converter that is running but producing nothing is restarted.** Until now only one that had exited was
  picked up; one that was stuck — which is what a stream it cannot get past looks like — was merely noted in
  the log while the channel died. Twenty seconds without a segment and it is started again, costing a short
  gap instead of the channel.

## 1.7.2

- **Fixed "the tuner stopped responding (Invalid argument)" in 1.7.0 and 1.7.1.** Taking one programme's pids
  was done with a single filter plus DMX_ADD_PID for the rest, which the kernel rejects: extra pids are only
  allowed on a filter that delivers to the demux handle itself, not to the dvr device. It now sets one filter
  per pid, which the kernel merges onto the one dvr device, and the saving is the same.
- **And if a driver refuses per-pid filtering for any other reason, the channel still plays.** It falls back
  to the whole multiplex — wasteful, but far better than refusing to show anything — and says so in the log.

## 1.7.1

- **A channel no longer ends because the converter fell over.** The tuner is still locked and still ours; only
  FFmpeg stopped, and broadcast streams glitch — a moment of interference is enough. LENTA now starts the
  converter again, up to five times, carrying on the segment numbering where it left off so the player sees a
  short gap rather than a stream that rewound. Only after five goes does it admit the channel is broken.

## 1.7.0

- **A channel now takes only its own packets off the tuner, not the whole multiplex.** This is why a channel
  could play smoothly for a few seconds and then freeze — and why two channels on the *same* frequency, with
  identical signal, behaved differently. LENTA was carrying every channel on the multiplex (around fifty
  megabits a second on cable) down a USB link that only ever needed to deliver one (under ten). The kernel's
  buffer holds barely a second of a full multiplex, so any hiccup overran it, and an overrun is not reported —
  it quietly corrupts the stream. Taking the programme's own pids instead cuts the traffic by about 85% and
  gives the same buffer several seconds of headroom rather than one.
  It matters most where the tuner is passed through to a virtual machine, where every megabit is dearer.
- The second audio track and the subtitle streams are left behind too: bandwidth that was carried across and
  then discarded.

## 1.6.9

- The *Would not start* panel no longer promises FFmpeg's output for a channel that never reached FFmpeg.
  A channel is tuned first and converted second, so when the tuner cannot lock there is nothing for FFmpeg
  to say — the tuning attempts are the record, and the panel now says so.

## 1.6.8

- **A silent first pass is no longer taken as proof.** A frontend handed over moments earlier — by a scan
  that has just finished, or by the previous channel — can need a beat before it hears anything at all.
  Version 1.6.6 dropped the second attempt that 1.6.3 had, so a tuner that was merely still settling was
  reported as receiving nothing. It now waits two seconds and tries the whole ladder again.
- **The failure record shows the signal reading**, so "nothing arriving" can be told apart from "the tuner
  was not listening yet" without guesswork.

## 1.6.7

- **The check tool no longer calls a deliberate unplug "USB trouble".** Pulling the stick out logs a plain
  "USB disconnect", which was being reported alongside genuine faults — sending you to hunt a port problem
  you had caused yourself a minute earlier. Resets, enumeration errors and over-current are still flagged.
- When nothing at all arrives on a frequency, the advice now includes power-cycling the stick, which is what
  an em28xx that has wedged needs.

## 1.6.6

- **A combination that finds the carrier is now given time to finish locking.** 2.5 seconds is a scanning
  budget, not a locking one: a weak or dirty multiplex can take several seconds to pull in. When one of the
  attempts reaches the carrier but runs out of time — and nothing else sees the multiplex at all, so it is
  the right one almost by definition — it is retried with twelve seconds. This applies to scanning and to
  watching alike.
- **The check tool prints the signal after each attempt**, so a feed that has weakened since the scan is
  visible rather than inferred, and it now distinguishes the two failures that look identical: nothing
  arriving at all, versus a carrier that is there but too poor to decode.

## 1.6.5

- **Fixed `tv-check --freq`, which only ever tried the aerial band.** Asking it about a frequency on a cable
  feed produced "DVB-T: nothing | DVB-T2: nothing" and told you nothing at all — which is the one moment
  anybody reaches for that option. It now tries the frequency every way the tuner could carry it: both
  bands, every symbol rate, the stated modulation and automatic, printing how far the demodulator got on
  each, and reads the channel table when it locks.

## 1.6.4

- **The modulation is no longer taken on trust.** A cable network's own table states the modulation of each
  multiplex, and when it is wrong — QAM-256 where the mux is really QAM-64 — the tuner never locks, however
  right everything else is. The saved value is still tried first, then the demodulator is asked to work it
  out for itself, across every symbol rate. Six combinations instead of three.
- **A failure to lock now shows its working**: every combination tried and how far the demodulator got on
  each ("signal, carrier" means the mux is there but will not decode; "nothing" means nothing is arriving).
  The message says which of those it was and what to do about it, and the whole list is kept under
  *Settings › Live TV › Would not start*.
- A channel that fails before FFmpeg ever runs is now recorded there too — previously those left an empty log.

## 1.6.3

- **Fixed channels that scanned perfectly and then would not play.** A cable network picks its own symbol
  rate and a terrestrial frequency may be DVB-T or DVB-T2, so a scan tries each in turn — but it saved the
  parameters it *started* with rather than the ones that actually locked. Any channel found on the second or
  third attempt was therefore stored with tuning that cannot lock, and watching it failed with "the tuner
  could not lock onto 530 MHz". The scan now records what worked.
- **Channels already scanned are repaired as you watch them**, without a re-scan: the player climbs the same
  ladder of possibilities, and when it finds the one that locks it writes it back to the channel, so the
  next time is immediate.
- **An input is no longer offered before it is really free.** Stopping a stream announced the input as
  available while its frontend was still being closed, so the next channel asked the tuner to lock while the
  driver was still settling — an intermittent, baffling failure to lock on a frequency that works.

## 1.6.2

- **A channel that will not start now says why.** "The channel did not start in time" was all you got, even
  when FFmpeg had already explained itself: the stream was torn down — taking its account with it — before
  anyone could read it, and a race meant the reason often had not been collected yet either. FFmpeg's own
  words are now in the message, at once rather than after a half-minute wait.
- **Settings › Live TV keeps the last few failures**, each with what FFmpeg said, since the stream itself is
  gone by the time you go looking.
- **Re-encoding is given longer to get going** than passing a stream through, because a standard-definition
  broadcast converted in software takes a while to produce its first seconds.

## 1.6.1

- **A channel no longer starts on an empty tank.** Playback began as soon as the first segment existed, so
  the player showed a second of picture and then sat buffering while it waited for the next one. It now waits
  for a few segments and starts behind the live edge, which is how live streaming is meant to work.
- **Segments are kept a little after they leave the playlist**, so a player that asks for one a moment late
  gets it instead of a 404 that looks like the stream breaking.
- **The player recovers from a stall** by stepping back to where the stream actually is, and retries a
  dropped segment several times before giving up. Giving up now offers *Try again*.
- **Settings › Live TV has a "Playing now" panel**: what is on, which input it holds, what the channel is
  made of, whether it is being passed through or re-encoded, how many people are watching, and how many
  segments have been produced in how long. *Show what the stream is doing* prints FFmpeg's own account,
  which is what to send when a channel stutters.

## 1.6.0 — Live TV plays

- **Channels can be watched.** Click one in the Live TV library and it plays: LENTA tunes a free input,
  takes the whole multiplex off the adapter, picks that channel out of it and sends it to the player.
- **A channel already in H.264 and AAC is passed through untouched.** What the channel is made of is read
  from the stream's own programme table before anything starts, so only the channels that need it — the
  MPEG-2 and MPEG-audio ones, of which Bulgarian cable has plenty — are re-encoded, on the GPU when there
  is one.
- **Everyone watching the same channel shares one tuner.** A second channel takes the stick's other input;
  a third says which channels are already on rather than failing quietly. A tuner is handed back about
  twenty seconds after the last viewer leaves, so flipping between channels does not pay to retune.
- **A scan will not pull a channel out from under a viewer.** It uses the stick's other input if one is
  free, and refuses by name if not. Watching is refused while that stick is mid-scan, for the same reason.
- The player drops what a broadcast has no use for: no seek bar, no skipping, no next episode, no resume
  position. A **LIVE** badge sits where the clock would be, and the mini player works as it does for a film.

## 1.5.2

- **Fixed the scan missing every cable channel.** The aerial UHF channels and the European cable raster
  share exactly the same 40 frequencies, 474 to 786 MHz. The scan remembered which frequencies it had
  already tried without remembering which band it tried them on, so once the aerial pass had been through
  474–786 MHz the cable pass skipped all of them — which on a cable feed is precisely where the channels
  are. A scan on cable would sweep the two ends of the band, find only the operator's data carriers, and
  report nothing found. The check tool never had this fault, which is why the two disagreed.

## 1.5.1

- **A scan now says what it saw.** When a scan finds nothing and `tv-check` finds a full line-up, there was
  no way to tell which part disagreed. Every frequency tried, and what the demodulator reached on it, is now
  recorded and shown behind *Show what the scan saw* on the Live TV page, as well as written to the log. Every
  way a scan can give up early leaves a line too, so "gave up before tuning anything" no longer looks exactly
  like "swept the whole band and heard nothing".
- **The time of the last scan is shown**, so a result from this morning is no longer mistaken for the scan you
  just ran.
- **A tuner that reports no standards is no longer scanned with an empty plan.** It falls back to what the
  input said when it was first found, and failing that tries aerial and cable both — and if there is still
  nothing to try, it says so instead of reporting "found 0 channels".
- The scan and the check tool now share one piece of code for reading the demodulator's flags, so they can
  never again describe the same state in different words.

## 1.5.0

- **Channel numbers are now the ones the network asks for, across the whole line-up.** A cable network
  numbers every channel it carries in a table that only appears on some frequencies, so a channel found
  before that table turned up had no number at all. Numbers are now pooled as the scan goes and filled in
  backwards, which is the difference between a handful of numbered channels and a properly ordered list.
- **A channel carried twice is listed once.** Operators repeat channels on a second frequency; LENTA keeps
  the copy the network gave a number to, preferring an unscrambled one. SD and HD stay separate.
- **Scrambled channels arrive hidden.** They need the operator's card in a CAM, which a USB stick has no
  slot for, so they no longer clutter the grid — unhide them if you do have a way in.

## 1.4.10

- **The socket reading is now exact.** Naming the controller was not enough: plenty of boards wire their plain
  black sockets to the xHCI controller too, and those are not noisy. The check now reads the kernel's port
  pairing instead — a USB 3 receptacle carries a USB 2 port and a SuperSpeed port in one hole, and the kernel
  links the pair — so a black socket on an xHCI controller is correctly called a USB 2 socket and left alone.
- Inside a virtual machine the note now carries the one-liner to run on the host, which lists every socket on
  the machine and says which of them are USB 3 ones.

## 1.4.9

- **The tuner check knows when it is inside a virtual machine.** Under Proxmox, KVM, VMware or Hyper-V the USB
  controllers a guest sees are ones the hypervisor invented, so the socket reading added in 1.4.8 described
  emulated hardware and could send you chasing a socket that does not exist. It now says so plainly and tells
  you to read `lsusb -t` on the host instead, where the physical socket actually lives.

## 1.4.8

- **The tuner check names the socket the stick is in.** A USB 3 socket is driven by xhci_hcd, which offers one
  root hub at 480 Mbit/s and another at 5000, so a tuner negotiating 480 under an xhci controller is still
  sitting in a USB 3 socket — something an earlier note wrongly said could not be read from the system. It
  matters: USB 3 sockets and their leads radiate broadband noise straight across the band a tuner listens to.
  When the tuner is in one, the check says so and names the machine's plain USB 2 root hubs to move it to.

## 1.4.7

- The tuner check now reports **how the stick is attached**: the speed it negotiated, what it draws, whether it
  hangs off a port on the machine or an external hub, and any USB resets or enumeration errors the kernel logged.

## 1.4.6

- **The tuner check sweeps every band the tuner supports**, aerial and cable, instead of only the aerial.
  `--band aerial` or `--band cable` still narrows it. (LENTA's own scan always did both; only the check tool
  was one-sided.) The scan's progress now says which band it is in: "aerial UHF 29", "cable 514 MHz".
- **Signal readings are real now.** They were asked for through an interface today's drivers leave
  unimplemented — the Si2168 among them — so everything read 0%. LENTA now asks the way modern drivers
  answer, in dBm and dB, and says plainly when a driver reports nothing rather than printing a false zero.
- **A lock with nothing behind it is no longer called success.** Demodulators claim a lock on noise now and
  then; the check tool only counts a frequency that also carried a channel table, and says so when it doesn't.

## 1.4.5

- Fixed an empty Settings › Live TV page. The scan's internal stop switch was being sent to the browser along
  with the progress, and it cannot be written as JSON — so once a scan had run, the page's request failed and
  the page drew nothing. Only plain values reach the browser now.
- A failed request no longer leaves a blank page: it says what the server answered, with a *Try again* button.

## 1.4.4

- **A scan can be stopped.** While one runs there is now a **Stop** button; it ends at once, keeps what it found
  and lets go of the tuner.
- **Automatic scanning can be switched off** — *Scan a newly found tuner by itself*, in Settings › Live TV.
  Worth switching off while testing the tuner from the command line, since a scan holds the tuner.
- **A failed scan is no longer retried for ever.** Every attempt is written down, success or not, so the
  background check tries a tuner once instead of grabbing it again every minute.
- The tuner check steps past an input that is in use and tries the stick's other one, and when everything is
  busy it says so plainly (it used to blame the cable).

## 1.4.3

- The tuner check (`deploy/tv-check.py`) no longer says "it locks" after `--no-sweep`, when it never tried;
  it now says what it did find and how to run the real sweep.
- It reads the driver and its firmware from the system (sysfs, lsmod, /lib/firmware) instead of relying on
  kernel messages, which have usually scrolled away on a machine that has been up for a while — and it says
  how to bring those messages back (unplug the tuner, plug it in, `dmesg -T | tail`).

## 1.4.2

- **Live TV: one entry per tuner stick, not per input.** A twin tuner such as the WinTV-dualHD shows the kernel
  two frontends; they are one thing on the desk with one aerial lead, so LENTA now keeps one entry that lists
  both inputs and scans once. Entries left behind by the earlier way of counting are adopted or, when empty,
  removed — nothing to clean up by hand. A tuner that is unplugged is kept and marked, not deleted.
- Cable scanning used the wrong value for "work the modulation out yourself" (the kernel's QAM_AUTO is 6; LENTA
  sent 11, which means APSK-32), so a cable scan could never lock. The constants are now the kernel's own.
- A DVB-T2 lock is given 2.5 seconds instead of 1.6 — some demodulators need more than two — and the usual
  stream (PLP 0) is stated outright, which some chips want.

## 1.4.0

**Live TV — first step: finding tuners and channels** (Settings › Live TV)
- USB TV tuners are found by LENTA itself, every minute, and identified: the stick's own name over USB and the
  standards the chip reports (DVB-T, DVB-T2, DVB-C, DVB-S/S2, ATSC). A tuner that has never been scanned is
  scanned once, quietly, in the background. No driver is installed — Ubuntu's own are used.
- Aerial and cable scanning: the legal frequencies are tried (UHF 21–60, VHF 5–12, the European 8 MHz cable
  raster), DVB-T2 wherever DVB-T doesn't lock, and the usual cable symbol rates. The first lock normally ends
  the sweep — the stream's network table lists the rest, so LENTA follows the network instead of the band.
- From the broadcast itself: channel names, who provides each one, **the provider's own name** (the network
  name, e.g. *NURTS Digital*), **the channel numbers the provider asks for**, and radio and scrambled marks.
- Playlists in the same list: an m3u address or an Xtream Codes login, with their names, logos, groups and
  channel numbers.
- A new library type, **Live TV**, which asks for no folders and shows the channels found.
- Satellite tuners are detected and reported; their scan waits for the dish settings (LNB, DiSEqC), which come next.
- Talking to the tuner is done in pure Python through the kernel's DVB ioctls, so LENTA still installs no extra
  packages. The service account already belongs to the `video` group, which is what owns `/dev/dvb`.
- A check tool for when a scan finds nothing: `sudo /opt/lenta/deploy/tv-check.py` looks at the system, the
  driver's own messages (a missing firmware file shows up there), what each tuner reports, and then every
  frequency with its lock, strength and quality — using LENTA's own code, so what it sees is what LENTA sees.
- **Playing a channel is not in this version** — that is the next step.

## 1.3.1

- The **Redline** theme is now called **Netflix** (earlier entries below still call it Redline). Nothing about
  the look changed. Your saved choice and the server's default theme are renamed for you when LENTA starts,
  so nobody has to pick it again, and a page still open from before the update keeps working.
- **HBO Max** now has its menu as a bar along the top, like the Netflix theme, instead of down the left side.
  Its own colours stay: the bar shows the page through it until you scroll and then turns black, and the page
  you are on is white with a blue line under it. On phones and tablets both themes keep the bar at the bottom.
- The menu-on-top layout is now written once and shared by both themes (`html.topnav`), so they can't drift apart.

## 1.3.0

**New theme: HBO Max** (Settings › Appearance › Look)
- A pure black page with the brand's dark indigo glow, light rather than bold lettering, one blue accent
  (Glacier Beam #71B8F2 over Max Blue #002BE7), 8px corners and no shadows or frosted glass.
- The side menu stays a narrow strip of icons and slides open over the page when you point at it, the way
  hbomax.com behaves. On a keyboard it opens when the focus moves into it.
- The billboard fades into black with a blue beam behind the title; *Play* is white, the rest are outlined.
- Pictures get 8px corners and a thin white edge when pointed at; *Recently added* is a quiet outlined label.
- Phones and tablets get a flat black bar at the bottom with a blue mark on the page you are on. The Android
  app's status and navigation bars follow the theme as usual.
- LENTA's own Golos Text stands in for Max Sans, which isn't ours to ship, so Latin and Cyrillic both work.

## 1.2.5

**Android app 1.1.4**
- A video sideways really fills the screen: the picture now uses the strip beside the camera as well, so there is
  no black band down one side. The strip is given back when you leave the player.

**Web**
- Phones (Redline): the ⓘ and ⋮ buttons under the *Continue Watching* posters are gone. Hold a poster to open
  its menu instead.

## 1.2.4

**Redline on phones looks like the Netflix app**
- Header with the LENTA mark, the page name (Home, a library, Search, My List) and your
  libraries as chips underneath. It is see-through at the top, turns black as you scroll, and the chips slide
  away while you scroll down.
- The billboard is a framed poster card with the title's logo, a "Movie • genre" line, a white **Play** and a
  grey **My List** button. The page takes on the poster's colour at the top.
- Posters in every row and library. *Continue Watching for <you>* has a play circle on the poster and the red
  progress line. A long press on one of those posters opens its menu: details, play from the beginning,
  My List, mark as watched.
- A floating, rounded tab bar: Home, Libraries, Search, My LENTA (My List, Settings, sign out).
- Computers, tablets, sideways phones and the other themes are unchanged.

**Player (touch screens)**
- Spread two fingers on the video to zoom it to fill the screen, pinch to see the whole picture again (like Netflix).
  LENTA remembers your choice on that device.

## 1.2.3

**Android app 1.1.3**
- A video page always takes the whole screen, sideways with the bars hidden, and gives it back when you leave it.
  The app does this itself from the page address, so it no longer depends on the page asking for it.
- Full screen uses Android 11+'s own method too (swipe from the edge to see the bars for a moment).

**Web**
- Phones and tablets that report a mouse (several Samsung Galaxy models do) are recognised as touch screens. That
  phone was getting the computer layout: the player's ⋮ menu didn't open, the bar repeated the big buttons,
  and the hover preview popped up on taps.

## 1.2.2

**Android app 1.1.2** (`web/download/lenta.apk`; installs over earlier versions)
- Videos now really open in full screen, sideways, straight away. The app switches to full screen itself instead of
  asking the page to, which Android's web view refused unless you tapped first.
- The player's ⋮ menu, Playback settings and Speed now show over the video in full screen.
- No grey "play" picture before a video starts, whatever the server version.

**Web**
- The player uses the app's own full screen when it runs in Android app 1.1.2 or later.

## 1.2.1

**Android app 1.1.1** (`web/download/lenta.apk`; installs over 1.0.x and 1.1.0)
- Videos open in full screen, sideways. The player's ⋮ menu, Playback settings and other menus work in full screen.
- Home while a video plays: it goes on in a small picture-in-picture window. Closing that window stops the video.
- No more grey "play" picture before a video starts.
- HTTPS: LENTA's own certificate is accepted for your server, and `https://` addresses default to port 8443. Any other certificate problem is explained instead of showing a blank page.
- Follows LENTA when it moves to another port or to https on the same machine, and remembers the new address.
- Music and audiobooks keep playing with the screen off.
- The status and navigation bars take the colour of your theme.
- Everything new in LENTA 1.2 works in the app: the player bar, Playback settings, the Play queue, audiobooks and the Settings page.
- The build checks the app's code the way Android's verifier does before writing the APK.

**Web**
- One **Save** per settings page (top right, and bottom right with **Discard** while there are changes). **Reorder** appears only where there's more than one block.
- Phones and tablets: the player opens in full screen; menus, sheets and messages show in full screen too.
- Touch screens: the full-screen player's bar no longer repeats the big play and ±10 s buttons shown on the picture.

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
