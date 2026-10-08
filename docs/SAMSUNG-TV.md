# LENTA on a Samsung TV

There are two ways to watch LENTA on a Samsung TV. Both use the same LENTA server and need a TV from
**2017 or newer** (Tizen 3.0 and later). TVs up to 2022 have an older web engine; LENTA notices that and sends them
a version of its pages rewritten for older engines (the same app, it may look a little simpler in places). TVs from
2016 and earlier (Tizen 2.x) are too old.

| | The TV's web browser | The LENTA TV app |
|---|---|---|
| Setup | None: type an address | About 10 minutes, once |
| Starts from | The Internet app | Its own icon under Apps |
| Remote control | Arrows, OK, Back | Arrows, OK, Back, ▶ ❚❚ ⏩ ⏪ ■ |
| Full screen | Mostly (the browser shows its bar now and then) | Always |

## Option A: the TV's web browser (no installation)

1. On the TV, open the **Internet** app.
2. Type your LENTA address, for example `http://192.168.1.20:8484`, and open it.
3. Sign in. LENTA notices it is on a TV and switches to remote-control mode.
4. Add it to the browser's bookmarks so next time it's one click.

## Option B: the LENTA TV app

Samsung TVs only install apps from Samsung's store, or from a computer when the TV is in **Developer Mode**.
LENTA uses Developer Mode: your LENTA server sends the app to the TV over your network. You do this once;
afterwards LENTA is under **Apps** like any other app.

You need:

* The TV and the LENTA server on the same home network.
* The TV's IP address (step 1).
* The LENTA server's IP address: the one you use in the browser, e.g. `192.168.1.20`.
* Internet on the server for the first run. The installer downloads Samsung's TV tools (about 1 GB, as a
  Docker container) and installs Docker if the server doesn't have it. Nothing from Samsung is installed on
  the server itself, and no video drivers are touched.

### 1. Find the TV's IP address

On the TV: **Settings › All Settings › General & Privacy › Network › Network Status › IP Settings**
(on some models: **Settings › General › Network › Network Status › IP Settings**). Write down the **IP address**,
e.g. `192.168.1.50`.

Tip: in your router, give the TV a fixed IP address (a "DHCP reservation"). That way the address stays the same.

### 2. Switch on Developer Mode

1. On the remote press **Home** and open **Apps**.
2. While on the Apps screen, type **1 2 3 4 5** on the remote.
   * If your remote has no number keys, press the **123** button (or the **⋯** button) to show a number
     pad on the screen and enter 1 2 3 4 5 there.
3. In the **Developer Mode** window, switch Developer mode **On**.
4. In **Host PC IP** enter the **LENTA server's** IP address (e.g. `192.168.1.20`) and choose **OK**.
5. **Restart the TV**: hold the power button on the remote until the TV turns off and on again
   (about 5 seconds). Simply switching it off and on is not enough.

After the restart, the Apps screen shows **Developer Mode** at the top. That means it worked.

### 3. Install LENTA on the TV

On the LENTA server, run this (with your TV's IP address):

```bash
sudo /opt/lenta/deploy/install-tv.sh 192.168.1.50
```

The installer:

1. installs Docker if it is missing,
2. downloads Samsung's TV tools in a container (only the first time),
3. connects to the TV, signs the LENTA app and installs it,
4. opens LENTA on the TV.

The app opens your LENTA server automatically (the installer gives it the server's address). Sign in once with
the remote, and you're done.

From another Linux computer you can also fetch the installer from the server. In that case, enter that
computer's IP as the Host PC IP in step 2:

```bash
curl -fsSL http://192.168.1.20:8484/download/install-tv.sh | sudo bash -s -- 192.168.1.50 --server http://192.168.1.20:8484
```

### 4. Afterwards

* LENTA is under **Apps**. To pin it: select it, press and hold **OK** (or press ▼), then choose **Add to Home**.
* LENTA updates itself. The TV app shows the pages of your LENTA server, so when you update the server, the TV
  gets the new version too. Run `install-tv.sh` again only if the release notes ask you to.
* You can switch Developer Mode off again (same 1 2 3 4 5 window), but there's no need.

## Using LENTA with the remote

| Button | Does |
|---|---|
| Arrows | Move between pictures, buttons and menu items |
| ◀ at the left of a row | Opens the menu on the left |
| OK | Open / press. In the player: play and pause |
| Back | Close a menu or dialog, leave the player, go back a page. On Home: leave LENTA |
| ▶ ❚❚ ⏯ | Play, pause, play/pause (in the player) |
| ⏩ ⏪ | Forward and back 10 seconds |
| ■ | Stop and close the player |
| In the player: ◀ ▶ | Back / forward 10 seconds |
| In the player: ▲ ▼ | Show the controls (subtitles, audio, next episode…) and move onto them |

To change the server the app uses, open your profile menu in LENTA and choose **Change server…**. You can also
start the app and press ▼ while it says "Connecting…".

## Samsung certificate (only if the TV refuses the app)

The installer signs LENTA with the developer certificate that comes with Samsung's tools. Many TVs accept it. If
the installer says **"The TV refused the app"**, your TV wants a certificate made for that TV. This is free, but it
needs Samsung's Tizen Studio on a PC (Windows, Mac or Linux) and a Samsung account:

1. Install **Tizen Studio** from <https://developer.tizen.org/development/tizen-studio/download>.
2. In Tizen Studio's **Package Manager › Extension SDK**, install **Samsung Certificate Extension**.
3. With the TV in Developer Mode (Host PC IP = **this PC's** IP for now), open **Tools › Device Manager**, choose
   **Remote Device Manager › +**, add the TV's IP and connect. This lets Tizen Studio read the TV's ID (DUID).
4. Open **Tools › Certificate Manager › +** and choose **Samsung**, **TV**, then **Create a new certificate
   profile**. Give it any name.
5. **Create a new author certificate.** Choose a name and a password, then sign in with your Samsung account.
6. **Create a new distributor certificate.** Leave privilege at **Public**. Check that the TV's DUID is in the
   list, then sign in again if asked. Finish.
7. The files are in your home folder under `SamsungCertificate/<profile name>/`: `author.p12` and
   `distributor.p12`. Copy both to the LENTA server, e.g. into `/root/lenta-tv-cert/`.
8. On the TV, set Developer Mode's **Host PC IP back to the LENTA server**, restart the TV, and run:

```bash
sudo /opt/lenta/deploy/install-tv.sh 192.168.1.50 --samsung-cert /root/lenta-tv-cert
```

The script asks for the certificate's password. You can also set it with `CERT_PASSWORD=... sudo -E ...`.

## Troubleshooting

**"The TV did not let this computer in"**
: Developer Mode is off, the Host PC IP isn't the server's IP, or the TV wasn't restarted (hold the power button)
  after you changed it. Check the TV's IP address too. The installer prints which Host PC IP it expects.

**"The TV refused the app"**
: See [Samsung certificate](#samsung-certificate-only-if-the-tv-refuses-the-app).

**The app shows "This TV's web engine is too old"**
: The TV is from 2016 or earlier. The screen shows the TV's engine version underneath; send it along if you
  think the TV is newer. Use the TV's web browser if it opens LENTA, or a streaming stick (Android TV,
  Fire TV with the LENTA Android app).

**The app says it can't reach the server**
: Is LENTA running (`sudo systemctl status lenta`)? Is the TV on the same network (not a guest Wi-Fi)? Choose
  Connect again or type the address with the port, e.g. `192.168.1.20:8484`.

**A film plays without sound or doesn't start**
: LENTA converts what the TV can't play. If one film still fails, open Server admin › Activity while it plays
  and look at what the server reports.

**Docker can't download the tools**
: The server needs internet access to Docker Hub for the first run (`docker pull vitalets/tizen-webos-sdk`).

## What the installer installs where

* On the server: Docker (if missing) and the `vitalets/tizen-webos-sdk` container image with Samsung's `tizen`
  and `sdb` tools. Remove them with `sudo docker rmi vitalets/tizen-webos-sdk` (and `sudo apt remove docker.io`).
* On the TV: one small app, **LENTA** (`LentaTV001.LENTA`). It contains only a start page that opens your
  server. Remove it like any app: **Apps › LENTA › ⚙ › Delete**.
