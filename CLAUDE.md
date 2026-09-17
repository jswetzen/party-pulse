# Party Pulse — notes for Claude Code

See `README.md` for the general dev/deploy commands and `PLAN.md` for the actual design doc
(status, data model, decisions, backlog). This file is just operational notes that don't belong
in either of those.

## Local podman container on `dev`

Rootless `podman build`/`podman run` fails on this machine (`dev`, NixOS LXC CT124) with
`newuidmap: write to uid_map failed: Operation not permitted` — `/run` here is a `nosuid`
tmpfs and `newuidmap` lives on it, so the kernel silently ignores its setuid bit no matter how
correct `/etc/subuid` looks. Every `podman` command on this host needs `sudo` in front of it
(rootful, sidesteps user namespaces entirely). This is a `dev`-host quirk, not a project one —
see `~/.claude/CLAUDE.md`'s host facts for the full root-cause writeup.

There's a standing local container for manual testing: `party-pulse`, built from
`localhost/party-pulse:latest`, backed by the `party-pulse-data` named volume, published on
`:8000` (`http://192.168.1.59:8000/`). It is *not* the real deployment (see PLAN.md "Hosting" —
that's CT 123, pulled from `ghcr.io/jswetzen/party-pulse` via CI on push to `main`); this one
just runs whatever is currently checked out in this working tree, uncommitted changes included.
To refresh it after code changes (last done 2026-08-29):

```sh
sudo podman build -t party-pulse .
sudo podman stop party-pulse && sudo podman rm party-pulse
sudo podman run -d --name party-pulse -p 8000:8000 -v party-pulse-data:/data \
  -e DJANGO_DEBUG=1 -e DJANGO_ALLOWED_HOSTS=192.168.1.59,localhost,127.0.0.1 \
  party-pulse
```

The `-v party-pulse-data:/data` mount is what makes this safe to repeat — `db.sqlite3` lives on
the named volume, not in the container, so stop/rm/run doesn't lose guest data (confirmed via
`sudo podman logs party-pulse` showing "No migrations to apply" after a rebuild, not a fresh
`migrate` from zero). The env vars above match what the container was actually launched with —
reconfirm with `sudo podman inspect party-pulse --format '{{json .Config.Env}}'` if they're ever
in doubt rather than assuming this snippet stays accurate forever.

## Screenshotting the live screen from `dev`

`google-chrome-stable` (currently v149) is installed on this host via dotfiles-hm (added
2026-08-29 for the separate "Claude in Chrome" extension effort — see the knowitall task for
that), and works fine as a plain one-shot headless screenshotter, no chromedriver/Selenium/
chromium-cli needed for this. Confirmed working 2026-09-15 against the standing local
container above:

```sh
google-chrome-stable --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1920,1080 --screenshot=/tmp/screen.png http://192.168.1.59:8000/screen/
```

`--no-sandbox` is required here — Chrome's sandbox leans on the same user-namespace
machinery that makes rootless `podman` fail on this host (see the `newuidmap`/nosuid-`/run`
writeup above); this wasn't independently root-caused for Chrome specifically, but the
symptom (silent failure to launch without it) and the mechanism match closely enough that
it's very likely the same LXC quirk, not a Chrome bug. `--headless=new` (not the older
`--headless`) waits for the page's `load` event before writing the screenshot, which is
enough for this app's htmx `hx-trigger="load"` fetch (`/screen/state/`) to have resolved and
painted — no extra `sleep`/wait step needed for a single static capture. For anything beyond
one screenshot (clicking through `screen_control.html`, waiting on a specific element,
reading console errors) you'd want real CDP/Selenium driving instead of the bare CLI flags;
that hasn't been set up on this host, only the one-shot `--screenshot` flag has been
confirmed to work.
