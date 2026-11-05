# Stories and Videos

[![CI](https://github.com/jim-edwards/stories-and-videos/actions/workflows/ci.yml/badge.svg)](https://github.com/jim-edwards/stories-and-videos/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Docker image](https://img.shields.io/docker/v/jimbo333/stories-and-videos?label=docker&sort=semver)](https://hub.docker.com/r/jimbo333/stories-and-videos)

A very simple, large-print video library for people who just want to press **Play**. It is
self-hosted: one small container, your own video files, no cloud account.

* Each **story** (playlist) is a folder. New videos dropped into it show up automatically.
* Each person signs in once and the site remembers them. It tracks what they have **seen**.
* The home page shows only the videos they **haven't seen yet, in order**. Seen videos are tucked
  under a collapsed **Already seen** heading. A story that is completely watched is greyed out
  and says "You have seen all of these."
* Videos play in the browser, resume where they were left off, and offer a big
  **Next** button at the end.
* A text-size switch (A A **A**) in the header makes everything bigger, and an **Auto / Light / Dark**
  switch changes the colors. **Auto** (the starting point) follows the device's own light or dark setting;
  picking Light or Dark is remembered on that device until someone picks Auto again.
* Optionally, each person can be given a **daily amount** of new video (say 30 minutes), so they only see
  that much at a time, and a **choice of stories**, so they only see the ones meant for them.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) (or Podman) and a folder of videos.

```bash
mkdir -p stories/videos stories/data          # put your story folders inside stories/videos
docker run -d --name stories-and-videos --restart unless-stopped \
  --user "$(id -u):$(id -g)" -p 8080:8080 \
  -v "$PWD/stories/videos:/videos:ro" \
  -v "$PWD/stories/data:/data" \
  jimbo333/stories-and-videos:1
docker logs stories-and-videos                # the first administrator password is printed here, once
```

Open <http://localhost:8080> and sign in as `admin`. Use **Manage** (top right) to change that
password and add the other people. Prefer Compose? See [`compose.yaml`](compose.yaml).

**Image tags** (Linux `amd64` and `arm64`): `1.2.3` is one exact release, `1.2` and `1` follow the newest
release in that line, and `latest` is the newest of all. Use `1` to get fixes automatically each time you
pull.

**Updating:** `docker pull jimbo333/stories-and-videos:1`, then recreate the container (with Compose:
`docker compose pull && docker compose up -d`). Everything lives in the `/data` folder, and the database is
upgraded automatically.

## How your videos are organised

Mount one folder at `/videos`. **Each top-level folder is one story.**

```
/videos
├── The Farm Years/
│   ├── 01 - The Barn Cat.mp4
│   ├── 02 - Harvest Time.mp4
│   └── 03 - The Big Storm.mp4
├── Grandma's Kitchen/
│   ├── Apple Pie.mp4
│   └── Sunday Soup.mp4
└── 05 - Short Tales/          <- a number prefix just controls the order of stories
    └── ...                       and is not shown on screen
```

* **Title and description** come from the metadata inside each video file (the `title` tag and the
  `description` tag; `synopsis`, `summary` or `comment` are used if there is no description).
  If a file has no title, its file name is used (a leading `01 - ` is dropped).
* **Order inside a story**: by the track/episode number in the metadata if the files have one,
  otherwise by file name with numbers sorted properly (`Part 2` before `Part 10`).
  New videos that sort later simply appear at the end.
* Videos in sub-folders belong to the top-level folder's story. Loose files directly in `/videos`
  go in a story called "Other Videos".
* **Pictures (thumbnails)**: save a `.jpg`, `.jpeg` or `.png` **next to the video with exactly the same
  name**, e.g. `Apple Pie.mp4` and `Apple Pie.jpg`. It is shown beside the video in the list, in the
  *Already seen* list, and behind the Play button on the video page. If both `.jpg` and `.png` exist the
  `.jpg` is used. A video with no picture still works, it just shows text only. New or changed pictures are
  picked up at the next check (the Manage page shows how many videos have one). Pictures of any shape
  work, wide, square or tall: each is shown whole (scaled to fit, never cropped), and a tall one is
  simply shown taller. Pictures about 640 pixels wide load fastest on a tablet.
* The app **never changes** your videos. Mount the folder read-only (`:ro`).
* The folder is re-checked every 5 minutes (and when you press *Check for new videos now* on the
  Manage page). Metadata is cached in a small SQLite database, and a file is only re-read if it changes.
  Files still being copied are skipped until they settle (30 seconds), so half-copied videos don't appear.
* If you **rename or move** a video, it keeps its watched status (matched by size and timestamp).
  If a file disappears (say a network drive is offline) it is hidden, not forgotten, and returns with its history.

**Formats:** browsers play **MP4 (H.264 video + AAC audio)** everywhere, so use that if you can.
`.m4v`, `.webm`, `.mov` and `.mkv` are also picked up but depend on the codecs inside and the
browser. If one won't play, the page tells the viewer to ask you. Convert with
`ffmpeg -i in.mkv -c:v libx264 -c:a aac -movflags +faststart out.mp4`.

A video counts as **watched** when it reaches the end or 90% of the way through.
Replaying never un-sees a video. In *Already seen*, **Mark as not seen** puts it back in the list.

### Making the pictures

If you don't already have pictures, `make-thumbnails` saves one next to every video that lacks one (a
frame from a tenth of the way in, 640 px wide, as `.jpg`). It never replaces an existing picture, so run
it again whenever you add videos. It needs a **writable** copy of the folder. The image includes it, so you
don't need ffmpeg installed:

```bash
docker run --rm --user root -v /path/to/your/videos:/videos \
  --entrypoint make-thumbnails jimbo333/stories-and-videos:1 /videos
```

(Running as `root` just means the pictures can be written wherever the folder's permissions are. The
container you actually use for the site keeps the folder read-only.) With ffmpeg installed locally you can
instead run `scripts/make-thumbnails.sh /path/to/videos`. To use your own artwork, just save it next to the
video with the matching name.

## Choosing what each person sees

Open **Manage**, then **Stories & daily amount** next to a person.

**Which stories.** By default everyone sees every story, including ones added later. Choose *Only the
stories ticked below* to give someone a subset. New stories are not added to such a person automatically,
so tick them when they appear. A story they can't see can't be played either, even by typing its address.

**A daily amount of new video.** By default people see every unwatched video at once. Give someone a number
of minutes a day and the site *releases* new videos to them a little at a time, so there is never more than
about that much new video waiting, and nobody is tempted to watch the lot in one sitting:

* Videos are released in each story's **playing order**. A story's next video is never skipped, so if it is
  too long to fit today, that story waits.
* **Stories take turns**, one video each, then a second from each, and so on while there is room. The story
  that has waited longest goes first the next day.
* The amount is **per day, not per sitting**. What isn't watched yesterday is still there and counts towards
  today, so the waiting list never grows past the daily amount. A half-watched video counts for what is left
  of it. Watching more doesn't release more until the day turns over.
* A video **longer than the whole daily amount** is released on its own, when nothing else is waiting, so it
  can never get stuck.
* Videos with no readable length count as 10 minutes.
* **Videos already seen can always be watched again**, and putting one back with *Mark as not seen* doesn't
  use up any of the day's new ones.
* The day turns over at **1:00 AM** unless you change it under *Daily release* on the Manage page. The new
  videos appear the first time the person visits after that, and also straight away when you change their
  settings or new videos arrive on a day that still has room.
* Choose *Show every video at once* to remove the limit again.

Set the container's time zone with `TZ` (for example `TZ=America/Chicago`) so "1:00 AM" is your 1 AM. Without
it the site uses UTC.

## Running it for family: HTTPS and a reverse proxy

The site is only as private as its login, so if it can be reached from the internet, serve it over **HTTPS**
through a reverse proxy, and keep `TRUST_PROXY: "1"` set (it is in `compose.yaml`) so the site knows it is
being served over HTTPS and can mark its sign-in cookie `Secure`. Leave it at `0` if people connect to the
container directly. A signed-in person stays signed in as long as they use the site at least once a year,
so they normally won't see the sign-in page again.

For nginx:

```nginx
server {
    server_name stories.example.com;
    # listen 443 ssl; ssl_certificate ...;   (your TLS setup)

    location / {
        proxy_pass http://127.0.0.1:8080;     # wherever the container is listening
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;                  # stream big videos instead of buffering them to disk
    }
}
```

Caddy: `stories.example.com { reverse_proxy 127.0.0.1:8080 }`. Nginx Proxy Manager, Traefik and friends:
a normal proxy host for that name is enough.

## Managing people

The **Manage** page (shown to admins only) adds people, changes passwords, removes people, chooses what each can see, and
re-checks the video folder. From a shell you can do the same:

```bash
docker exec -it stories-and-videos python -m app.cli add-user grandma --name "Grandma"
docker exec -it stories-and-videos python -m app.cli set-password grandma   # e.g. a forgotten password
docker exec -it stories-and-videos python -m app.cli list-users
docker exec -it stories-and-videos python -m app.cli scan
```

If you ever lock yourself out of the admin account, `set-password` fixes it. A short sign-in name and a
password of a few easy words works well for older relatives.

## Settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `VIDEO_DIR` | `/videos` | Where the videos are mounted in the container |
| `DATA_DIR` | `/data` | Where the database lives. **Back this folder up** (watch history, accounts) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / random | First-start administrator (used only when there are no users yet) |
| `TRUST_PROXY` | `0` | Number of reverse proxies in front (`1` for one) |
| `SCAN_INTERVAL` | `300` | Seconds between automatic checks for new videos |
| `SETTLE_SECONDS` | `30` | Ignore files modified more recently than this (still copying) |
| `SESSION_DAYS` | `365` | How long someone stays signed in without using the site |
| `TZ` | `UTC` | Time zone name (such as `America/Chicago`) used for when the day turns over |

The container runs as user `568` by default and works as any user id: set `--user` (or `user:` in Compose)
to someone who can **read** the videos and **write** the data folder.

## Security and supply chain

* Passwords are stored as salted scrypt hashes. Repeated wrong passwords are slowed down.
  Sign-in cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` when served over HTTPS.
* There is intentionally **one** server worker: it owns the background scanner. Many people can
  still watch at once (16 threads).
* The generated `secret.key` and `stories.db` are in `DATA_DIR`.
* Images are built **only** by GitHub Actions from this repository. Every image has a
  signed build record (Sigstore/cosign, keyless), an SBOM and build provenance. Check one yourself:

  ```bash
  cosign verify jimbo333/stories-and-videos:1 \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github\.com/jim-edwards/stories-and-videos/\.github/workflows/publish\.yml@'
  ```
* Dependencies and the base image are kept current by Dependabot. Every change is scanned for known
  vulnerabilities (Trivy) and code problems (CodeQL), the published image is re-scanned every day, and
  it is rebuilt every week so operating-system fixes reach you. See [SECURITY.md](SECURITY.md) to report a
  problem.

## Development

```bash
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests          # needs ffmpeg installed to build test videos
VIDEO_DIR=./sample-videos DATA_DIR=./data ADMIN_PASSWORD=devpassword1 .venv/bin/flask --app app run --debug
```

`scripts/make-sample-videos.sh sample-videos` creates a few tiny demo videos with pictures (needs ffmpeg).
To build and try the image yourself:

```bash
docker build -t stories-and-videos .
scripts/smoke-test.sh stories-and-videos        # starts it and checks the basics
docker run --rm -p 8080:8080 -v "$PWD/sample-videos:/videos:ro" -v sav-data:/data \
  -e ADMIN_PASSWORD='choose a password' stories-and-videos
```

**Dependencies:** `requirements.in` lists the direct ones; `requirements.txt` is generated from it with
`pip-compile --generate-hashes --strip-extras --output-file requirements.txt requirements.in` and pins every
package by hash.

**How releases happen** (all in `.github/workflows`):

* `ci.yml` runs on every push and pull request: the tests, a Dockerfile and workflow lint, an image build with
  a start-up smoke test, and Trivy scans of the image and the repository.
* Dependabot opens weekly update pull requests. Patch and minor updates merge by themselves once CI is green.
  Major updates wait for a person.
* `auto-release.yml` runs every Monday. If the app, Dockerfile or dependencies changed since the last release it
  tags the next patch version, otherwise it rebuilds the latest release to pick up operating-system fixes.
  Minor and major releases are made by pushing a `vX.Y.0` / `vX.0.0` tag by hand.
* `publish.yml` re-runs CI on the tagged source, builds `amd64` and `arm64` images, pushes them to Docker Hub,
  signs them and creates the GitHub release. `scan.yml` re-scans the published image daily and rebuilds it if a
  fixable high-severity problem shows up.
* The repository needs the variable `DOCKERHUB_USERNAME` (and optionally `DOCKERHUB_NAMESPACE`) and the
  secret `DOCKERHUB_TOKEN` (a Docker Hub access token with read/write scope) to publish. Without them
  everything else still runs and nothing is published.

## License

Copyright © 2026 Jim Edwards. Stories and Videos is free software under the **GNU General Public License,
version 3** ([LICENSE](LICENSE)): you can use, study, change and share it, and changes you distribute must
stay under the same license. It comes with no warranty.
