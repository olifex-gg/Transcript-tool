# Transcript Tool

A small app that turns YouTube videos **and whole playlists** into text.
It is built to be used from a phone: paste a link, tap *Transcribe*, then copy, share or download the result.
It runs on your computer (or a server) and your phone connects to it over Wi-Fi.

## Download the app (no setup)

Grab the build for your computer from the **[latest release](https://github.com/olifex-gg/Transcript-tool/releases/latest)**:

| Computer | File |
| --- | --- |
| Mac with Apple silicon (M1 or newer) | `transcript-tool-macos-apple-silicon.zip` |
| Mac with an Intel chip | `transcript-tool-macos-intel.zip` |
| Windows (64-bit) | `transcript-tool-windows-x64.zip` |
| Linux (x86-64) | `transcript-tool-linux-x64.tar.gz` |

Unzip it and open **Transcript Tool** (Mac) or **transcript-tool** inside the folder (Windows/Linux).
Your browser opens with a QR code; scan it with your phone while both are on the same Wi-Fi, then use
the phone browser's *Add to Home screen*. Transcripts are kept in your user data folder, and the app
keeps running until you close its window (Windows/Linux) or quit it from the Dock or the page footer (Mac).

The builds are not code-signed, so the first launch needs one extra click:

- **Mac:** right-click the app and choose *Open*, then *Open* again. On newer macOS versions, go to
  *System Settings → Privacy & Security* and click *Open Anyway* if the first attempt is blocked.
- **Windows:** if SmartScreen appears, click *More info* → *Run anyway*. Allow the app through the
  firewall on private networks so your phone can reach it.

The desktop builds include Whisper speech-to-text for videos without captions (the Intel Mac build may not,
depending on library availability). If YouTube changes something and transcripts stop working, download the
newest release; each one bundles the current yt-dlp.

Everything below is for running it yourself with Python or Docker, which is the way to go for a server
that is reachable from anywhere.

- **Videos and playlists.** Paste a playlist link and every video in it is transcribed, a few at a time,
  with per-video progress. Channel pages work too (they are treated as playlists).
- **Fast by default.** Uses the captions YouTube already has (human or auto-generated), so a video
  takes a second or two and nothing heavy runs on your server.
- **Whisper fallback.** Optionally runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  locally for videos that have no captions at all.
- **Mobile first.** Big tap targets, dark mode, *Copy* / *Share* buttons, installable as a home-screen
  app, and it registers as an Android share target so YouTube's *Share* button can send a video straight to it.
- **Formats.** Plain text (paragraphs or timestamped), Markdown, SRT, VTT, JSON. Playlists can be
  downloaded as one combined file or a ZIP.
- **Survives restarts.** Jobs are saved on disk; close the tab, come back later, the transcripts are still there.

## Quick start

### With Docker (recommended for running on a server)

```bash
git clone <this repo> && cd Transcript-tool
docker compose up -d --build
```

Open `http://<server-ip>:8000`. Transcripts are stored in the `transcript-data` volume.

To include Whisper speech-to-text in the image, set `WHISPER: "1"` in `docker-compose.yml`
(or `docker build --build-arg WHISPER=1 -t yt-transcript .`).

### With Python

```bash
pip install .              # or: pip install ".[whisper]" for the speech-to-text fallback
yt-transcript serve        # http://0.0.0.0:8000
```

Python 3.10+ is required. `ffmpeg` is optional but recommended (`apt install ffmpeg` / `brew install ffmpeg`);
yt-dlp uses it for some audio formats when Whisper is in play.

## Using it from your phone

The app is a web page, so it works in any mobile browser once your phone can reach the server:

| Where the server runs | How the phone reaches it |
| --- | --- |
| A computer at home, phone on the same Wi-Fi | `http://<computer-ip>:8000` |
| A computer at home, phone anywhere | Install [Tailscale](https://tailscale.com) on both; use the computer's Tailscale IP or MagicDNS name |
| A cheap VPS / Fly.io / Railway / Render (all accept the Dockerfile) | Its public URL. **Set `APP_PASSWORD`** so strangers can't use it |

### Android: share from the YouTube app

1. Open the site in Chrome, tap the menu, choose **Add to Home screen** (or **Install app**).
   This needs HTTPS unless the address is `localhost`; a reverse proxy with Let's Encrypt, Tailscale's
   `tailscale serve`, or a hosted platform all provide it.
2. In the YouTube app tap **Share** on a video or playlist and pick **Transcript**.
   The job starts immediately and you land on its progress page.

### iPhone: a one-tap Shortcut

Safari can add the site to the Home Screen (Share → **Add to Home Screen**) for an app icon, but iOS has
no share-target API, so use a Shortcut instead:

1. Open **Shortcuts**, create a new shortcut, and enable **Show in Share Sheet** in its details
   (accept **URLs**).
2. Add one action: **Open URLs**, with the URL set to
   `https://YOUR-SERVER/t?url=` followed by the **Shortcut Input** variable.
3. Name it *Transcript*. Now YouTube's Share → *Transcript* opens the job page in Safari.

The `/t` endpoint accepts `url`, `text` or `title` query parameters, plus optional `language`,
`engine` and `playlist=false`, and redirects to the job page. It also works from any other automation tool.

### Copying the text

Each finished video has **View**, **Copy**, **Share** (sends the text or file to any app: Notes,
email, a chat), **Download**, and a link back to YouTube. For a playlist, **Copy all** / **Combined file**
give you one document with all videos, and **Download .zip** gives one file per video, numbered in playlist order.
The format selector (Text / Markdown / SRT / VTT / JSON) and the *Timestamps* toggle apply to all of these.

## Combining several transcripts

The **Recent** list has a checkbox on each entry. Tick two or more and a bar appears with
**Download .txt**, which merges every finished video from the selected jobs into one text file:
a numbered contents list at the top, then each video under its title and link. **Copy** puts the
same text on the clipboard, and **Share** (on phones) sends it to another app. **Select all** takes
everything that has a transcript. Entries still running, or with no finished video, cannot be ticked.

Videos appear in the order the entries are listed (newest first), and each job keeps its own
internal playlist order. The same thing is available over HTTP:

```
GET /api/merge?jobs=<id>,<id>&format=txt&timestamps=0
```

## Playlists

- Paste any playlist URL, or a video URL that contains `&list=`; the *Transcribe the whole playlist*
  option (on by default) controls whether the list is expanded.
- Videos run `WORKERS` at a time (default 2) to stay under YouTube's rate limits.
- Private or removed videos are reported as failed without stopping the rest; **Retry failed** re-runs just those.
- `MAX_PLAYLIST_ITEMS` (default 500) caps very large lists and channels.
- YouTube "Mix" lists (`list=RD…`) are endless auto-generated radio; they are ignored and only the video is transcribed.

## Languages and engines

**Language** is the caption language you'd like, e.g. `en`, `de`, `pt-BR`. Comma-separate fallbacks
(`en,de`). Use `orig` for "whatever the video is in". Human-made subtitles are preferred over auto-captions;
if the language you asked for only exists as YouTube's machine translation, that is used and marked as such.

**Engine**:

| Engine | What it does |
| --- | --- |
| `auto` (default) | Captions if the video has any, otherwise Whisper (when installed) |
| `captions` | Captions only; fails fast for videos without them |
| `whisper` | Always download the audio and transcribe locally |

One dependency note: to download the audio, yt-dlp needs a JavaScript runtime to solve YouTube's player
challenges. Install [Deno](https://deno.com) (recommended) or [Node.js](https://nodejs.org) and make sure it is
on your PATH; the desktop app and the Docker image pick up either automatically. Captions never need this.

Whisper runs on CPU by default with the `small` model (a decent accuracy/speed trade-off; roughly
real-time on a modern laptop core, slower on small VPS instances). Change with `WHISPER_MODEL`
(`tiny`, `base`, `small`, `medium`, `large-v3`, or any faster-whisper/CTranslate2 model name).
Models are downloaded on first use. Videos longer than `WHISPER_MAX_MINUTES` (default 240) are refused
to protect small servers.

## Configuration

All settings are environment variables (see `docker-compose.yml`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATA_DIR` | `data` (`/data` in Docker) | Where jobs and transcripts are stored |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Bind address |
| `APP_PASSWORD` | unset | Enables HTTP basic auth (user name `yt`, or set `APP_USERNAME`) |
| `DEFAULT_LANGUAGE` | `en` | Default caption language preference |
| `DEFAULT_ENGINE` | `auto` | `auto`, `captions` or `whisper` |
| `EXPAND_PLAYLISTS` | `true` | Default for the playlist toggle |
| `MAX_PLAYLIST_ITEMS` | `500` | Cap on videos per playlist/channel |
| `WORKERS` | `2` | Videos transcribed concurrently |
| `WHISPER_MODEL` | `small` | faster-whisper model name |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda` or `auto` |
| `WHISPER_COMPUTE_TYPE` | `int8` | e.g. `int8`, `float16` (GPU) |
| `WHISPER_MAX_MINUTES` | `240` | Longest video Whisper will accept (`0` = unlimited) |
| `YT_COOKIES_FILE` | unset | Netscape `cookies.txt` for YouTube (see troubleshooting) |
| `YT_PROXY` | unset | Proxy URL for yt-dlp requests |
| `YTDLP_OPTS` | unset | JSON object of extra yt-dlp options, e.g. `{"extractor_args": {"youtube": {"player_client": ["android"]}}}` |

## Command line

```bash
yt-transcript desktop                                                 # same as the packaged app: opens browser, shows phone QR
yt-transcript https://www.youtube.com/watch?v=dQw4w9WgXcQ            # -> transcripts/<title>.txt
yt-transcript "https://www.youtube.com/playlist?list=PL..." -f srt -o out/
yt-transcript URL --stdout --timestamps                                # print to the terminal
yt-transcript URL --engine whisper -l de                               # force Whisper, prefer German
yt-transcript URL --no-playlist                                        # ignore &list= in the URL
yt-transcript serve --port 9000                                        # run the web app
```

## HTTP API

The UI is a thin client over a JSON API (interactive docs at `/api/docs`):

| Method & path | Purpose |
| --- | --- |
| `POST /api/jobs` `{"urls": [...], "language": "en", "engine": "auto", "expand_playlists": true}` | Start a job |
| `GET /api/jobs` | Recent jobs |
| `GET /api/jobs/{id}` | Job status with per-video items |
| `GET /api/jobs/{id}/items/{n}?format=txt&timestamps=0&download=0` | One transcript (`txt`, `md`, `srt`, `vtt`, `json`) |
| `GET /api/jobs/{id}/items/{n}/segments` | Timed segments as JSON |
| `GET /api/jobs/{id}/combined?format=md` | All finished transcripts in one file (`md` or `txt`) |
| `GET /api/jobs/{id}/zip?format=srt` | One file per video |
| `GET /api/merge?jobs=a,b&format=txt` | Several jobs merged into one document (`txt` or `md`) |
| `POST /api/jobs/{id}/retry`, `POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/{id}` | Manage a job |
| `GET /t?url=…` | Start a job and redirect to it (share sheets, Shortcuts) |

## Troubleshooting

- **"Sign in to confirm you're not a bot"** or HTTP 403/429 from YouTube. Cloud/VPS IP ranges are
  often rate-limited. Export your browser cookies to a Netscape `cookies.txt`
  (e.g. the *Get cookies.txt LOCALLY* extension), mount it, and set `YT_COOKIES_FILE`.
  Lowering `WORKERS` to 1 also helps.
- **Video has no captions.** Install the Whisper extra (`pip install ".[whisper]"` or the
  `WHISPER=1` Docker build) and use the `auto` or `whisper` engine.
- **"No supported JavaScript runtime"** when Whisper tries to download audio. Install Deno or Node.js
  (see *Languages and engines*).
- **YouTube changed something and everything fails.** Update yt-dlp: `pip install -U yt-dlp`
  (or rebuild the Docker image). yt-dlp is updated within days of YouTube changes.
- **Share target does not appear on Android.** The site must be installed from an HTTPS origin;
  reinstall after enabling HTTPS.

## Development

```bash
pip install -e ".[dev,whisper]"
pytest
yt-transcript serve --reload
```

### Building the desktop app

```bash
pip install ".[whisper,build]"
pyinstaller --noconfirm --clean packaging/transcript-tool.spec
dist/transcript-tool/transcript-tool selfcheck      # macOS: "dist/Transcript Tool.app/Contents/MacOS/Transcript Tool"
```

GitHub Actions (`.github/workflows/build.yml`) builds all four platforms on every push and attaches them to a
GitHub Release whenever a `v*` tag is pushed:

```bash
git tag v0.1.1 && git push origin v0.1.1
```

Tests do not touch the network; the job pipeline is exercised with fake resolvers/transcribers.
