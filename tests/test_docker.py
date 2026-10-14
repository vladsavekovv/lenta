"""The container image: does it say the same things the rest of LENTA says?

An image is a second installer, and a second installer is a second place for the rules to be broken.
Three of them matter enough to be checked by something other than memory:

  * **The signing key never goes in.** A build context is a copy of this folder handed to the Docker
    daemon, and a published image is a public thing. `android/lenta-release.pem` is excluded from every
    release tarball by the packaging command; it has to be excluded here too, by .dockerignore, and the
    workflow greps the built image for it as well.
  * **No graphics drivers.** The bare-metal installer promises it installs, removes and changes no
    NVIDIA, Mesa or VA-API package, and uses the ones already on the machine. The image makes the same
    promise, so it must not quietly apt-get one.
  * **The environment variables have to be real.** A container is configured almost entirely through
    them, and a typo in one is invisible: the server starts, ignores it and uses a default. Every
    LENTA_* name the image or the compose file sets is checked against the ones config.py actually
    reads.

The image's own health check is not simulated here. It is run for real in .github/workflows/docker.yml,
which builds the image, starts it, waits for the server to answer for itself and looks for the key —
on a machine that can reach a registry, which the box this test runs on may not be able to.
"""
import os, re, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def text(rel):
    try:
        return (ROOT / rel).read_text()
    except OSError:
        check(f"{rel} exists", False)
        return ""


dockerfile = text("Dockerfile")
ignore = text(".dockerignore")
compose = text("compose.yaml")
entry = text("docker/entrypoint.sh")
flow = text(".github/workflows/docker.yml")
config = text("server/lenta/config.py")

print("1. the signing key cannot reach a layer")
ignore_lines = [ln.strip() for ln in ignore.splitlines() if ln.strip() and not ln.strip().startswith("#")]
check("the whole android/ folder is out of the build context", "android/" in ignore_lines)
check("and *.pem with it, wherever one turns up", "*.pem" in ignore_lines)
check("the Dockerfile never copies android/", not re.search(r"^\s*COPY\s+[^\n]*android", dockerfile, re.M))
check("the workflow looks for the key in the image it just built", "lenta-release" in flow)
# The tarball rule, restated: if someone ever adds android/ to the image, this is the line that shouts.
check("node_modules and the build's own scratch file stay out too",
      "tools/node_modules/" in ignore_lines and "tools/.fills.js" in ignore_lines)

print("\n2. no graphics drivers, here either")
apt = " ".join(re.findall(r"apt-get install[^\n&]*", dockerfile))
for banned in ("nvidia", "mesa", "libva", "vainfo", "intel-media", "vulkan", "libgl1"):
    check(f"the image installs no {banned} package", banned not in apt.lower())
check("FFmpeg is the self-contained build, as on a host",
      "BtbN/FFmpeg-Builds" in dockerfile and "/opt/ffmpeg" in dockerfile)
check("and the hardware comes from the host, through the compose file",
      "/dev/dri" in compose and "nvidia" in compose.lower())

print("\n3. the settings it hands the server are ones the server reads")
known = set(re.findall(r'environ\.get\("(LENTA_[A-Z_]+)"', config)) | set(re.findall(r'environ\["(LENTA_[A-Z_]+)"\]', config))
known |= {"LENTA_ALLOW_ROOT"}                        # read in __main__.py, not config.py
used = set(re.findall(r"(LENTA_[A-Z_]+)", dockerfile + compose + entry))
unknown = sorted(used - known)
check(f"every LENTA_* the image sets is one config.py reads{'' if not unknown else ': ' + ', '.join(unknown)}",
      not unknown)
check("the data folder is a volume, so it outlives the container",
      'VOLUME ["/data"]' in dockerfile and "LENTA_DATA_DIR=/data" in dockerfile)
port = re.search(r'LENTA_PORT", "(\d+)"', config)
port = port.group(1) if port else "8484"
check(f"the exposed port is the server's own default ({port})",
      f"EXPOSE {port}" in dockerfile and f'"{port}:{port}"' in compose)
check("and the HTTPS port is mentioned, commented out until it is switched on", "8443" in compose)

print("\n4. it does not run as root")
# There is no USER line on purpose, and the reason is worth keeping written down: the entrypoint has to
# be able to give the data folder to whichever account owns the media on the host (PUID/PGID — on a
# Synology or an Unraid box that is rarely 1000), and it cannot do that without starting with the
# privilege to. It drops immediately, and because it execs, the server ends up as PID 1 itself.
check("there is a service account in the image", "useradd -u 1000" in dockerfile)
check("no USER line, so PUID and PGID are not a promise the image cannot keep",
      re.search(r"^USER ", dockerfile, re.M) is None)
check("and the reason is written down rather than left to be rediscovered",
      "No USER line, and that is deliberate" in dockerfile)
check("the entrypoint hands straight over when it is already not root",
      re.search(r'id -u.*!= *"0".*\n\s*exec "\$@"', entry) is not None)
check("and drops from root with setpriv when it is", "setpriv --reuid" in entry)
check("util-linux is installed for it", "util-linux" in dockerfile)
check("media is never chowned — only LENTA's own folder is",
      "chown -R" in entry and not re.search(r"chown[^\n]*media", entry))
check("the server's own refusal to run as root is left in place", "LENTA_ALLOW_ROOT" not in dockerfile)

print("\n5. the workflow builds the thing and proves it starts")
check("it builds on a version tag", "tags: ['v*.*.*']" in flow or 'tags: ["v*.*.*"]' in flow)
check("and on demand, so the first build needs no tag", "workflow_dispatch" in flow)
check("a pull request is built but never published", "github.event_name == 'push'" in flow)
check("it starts the image and waits for the server to answer",
      "/api/server" in flow and "docker run" in flow)
check("it checks the server itself is not running as root",
      "/proc/1/status" in flow and '= "1000"' in flow)
check("it publishes to this repository's own registry", "ghcr.io" in flow)
check("the compose file points at that same image",
      "ghcr.io/vladsavekovv/lenta" in compose)
check("one architecture, stated plainly rather than guessed at",
      "platforms: linux/amd64" in flow)

print("\n6. the file the newcomer reads")
docs = text("docs/DOCKER.md")
for want in ("docker compose", "PUID", "/dev/dvb", "--gpus", "8484"):
    check(f"docs/DOCKER.md covers {want}", want in docs)
readme = text("README.md")
check("the README's quick start offers the container as well as the script",
      "docker" in readme.lower())

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("the container packaging ok")
