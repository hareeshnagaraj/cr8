# cr8

**A private room for unreleased music.**

Self-hosted. Open source. Not a SaaS. Built so you and your friends can keep
original work close, hear it fast, tag it while it plays, and send a link that
actually works — without dropping the archive into somebody else’s cloud.

cr8 started as a hard drive full of demos, bounces, and half-finished ideas.
The files were stored. The good ones were still missing when they mattered.
Dropbox, Samply, SoundCloud, and a folder tree all fail in different ways
because they treat the *file*, the *finished song*, or the *share product* as
the center. **cr8 makes listening and deciding the center.** Everything else
is infrastructure.

This is a project, not a business. Most personal circles don’t need a million
listeners. They need something fast, reliable, and theirs.

### Who built this

cr8 started as a passion project between **Hareesh** and **Henry** — two friends
who’ve made music together for over a decade (William & Mary) and wanted a
better way to share unfinished work than another SaaS upload box. Our music
project is **[all vars](https://www.instagram.com/allvarsmusic/)**
(@allvarsmusic on Instagram, Spotify, etc.). When the tool started working for
us, we pushed it further and opened it so anyone can run a crate and build with
us. Open source on purpose.

---

## The full story (formatted)

The README is the install path. The designed explanation lives on GitHub Pages,
in the same brief format as the long-form notes:

- **[Landing](https://hareeshnagaraj.github.io/cr8/)** — what cr8 is
- **[Vision](https://hareeshnagaraj.github.io/cr8/vision.html)** — why unreleased music needs a room
- **[Architecture](https://hareeshnagaraj.github.io/cr8/architecture.html)** — corpus → catalog → mirror → app

---

## Why it exists

- We didn’t like the sample-share tools. Everything’s a SaaS with the wrong
  incentives.
- We wanted something **we run**, that friends can join, that stays fast on a
  phone across the world.
- Original music stays **yours**. The corpus is read-only to the whole system.
  Opinions live in a catalog. Disposable mirrors serve streams.
- Tagging, hearts, collections, homework, presence, uploads, public links, and
  stems grow around *listening* — not around an admin screen.

---

## What you get

| | |
|---|---|
| **Library** | Every bounce in a table you can actually read. Sort, filter, dig, shuffle. |
| **Tag while it plays** | Status, keeper, key, vibe, instruments, collab, use — judgment in the moment. |
| **Send a link** | Mint a share. Friends listen without you hunting a folder. |
| **Invite friends** | Join links, members, presence — a small closed circle. |
| **Stems** | Local separation when you need vocals / drums / bass. Nothing has to leave the machine. |
| **Corpus never touched** | Scan → catalog → mirror. Rebuild the mirror anytime. |

Playback survives navigation. A music app that stops the music when you click
something is not a music app.

---

## Architecture (short)

```
corpus (read-only authority)
    → cr8 scan / resolve / enrich / build
    → catalog.db (opinions: tags, hearts, users, shares…)
    → mirror (mp3-320, peaks, art — disposable)
    → FastAPI (:8080) + Next.js (:3100)
```

The thesis is not “one Mac can serve music.” It’s that **unreleased creative
work needs a different product shape** from released music. Latency-sensitive
bits go to the edge; the durable archive stays home.

Operator tools (deploy, tunnel, launchd) live under [`ops/`](ops/README.md).
Contributor checks live under `scripts/gate.sh`.

---

## Quick start (you or a friend)

You need: Python 3.12+, Node/pnpm, ffmpeg + ffprobe.

```sh
git clone https://github.com/hareeshnagaraj/cr8.git
cd cr8

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e '.[dev]'

cr8 init                           # config, secrets, dirs, DB schema
# or: cp config.example.toml config.toml  # then edit corpus root

cd web && pnpm install && cd ..

# terminal 1 — API
./.venv/bin/uvicorn cr8.web.owner.app:create_app --factory \
  --host 127.0.0.1 --port 8080

# terminal 2 — web
cd web && pnpm dev                 # http://127.0.0.1:3100
```

Open **http://127.0.0.1:3100/setup** and create the first user.

Then point the catalog at your music and scan:

```sh
# config.toml → [corpus] root = "/path/to/your/music"
cr8 scan
# later: cr8 build   # mirror for streaming (when you’re ready)
```

`cr8 init` preflights ffmpeg/ffprobe, can seed `curated_dirs` from real folders,
writes a session secret if missing, and prints the next steps.

### Optional tools

- **keyfinder-cli** / **aubio** — automatic key/BPM enrichment if installed on
  `PATH`. Not required; missing tools are skipped cleanly.
- **ops/env** — only if *you* deploy to a server. Copy `ops/env.example` →
  `ops/env` (gitignored). Never commit hostnames or passwords.

---

## For collaborators (Henry and friends)

1. Clone this repo (public).
2. `cr8 init` with **your** corpus root (or empty curated lists and scan what you choose).
3. Run API + web, hit `/setup`, make **your** owner account.
4. Invite others with join links from the app — no shared “admin SaaS” account.

You’re not joining our catalog. You’re running **your own crate** with the same
software. Shares and invites are how circles meet.

---

## License

MIT. Use it. Fork it. Run it for your band. We’re not selling you a seat.

---

## Name

**cr8** (crate). Working name; may change. The thing is the room, not the logo.
