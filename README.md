# YouTube Playlist Sorter

A small desktop app that empties a YouTube **"To Sort"** playlist by moving each video into a topic playlist (Philosophy, Math, Programming…), based on keywords in the video's title and description.

Drop videos into one holding playlist as you find them, then let the sorter file them away.

## Features

- **One-screen desktop app** (Tkinter, nothing extra to install): sign in, Preview, Sort now. There's a headless CLI too.
- **Topic editor**: add, rename and delete topics and their keywords, with a live "try a title" tester
- **Playlist mapping**: topics are matched to playlists by name automatically, and you can override any match or create a missing playlist in one click
- **Creates new playlists on its own**: when several unmatched videos share a theme (say five chess videos), it creates a playlist for them, but only once enough of them have built up
- **Preview mode**: see exactly where every video would go before anything changes
- **Move or copy**: optionally keep videos in the source playlist
- **Quota-aware**: caps videos per run, stops cleanly when the daily API quota runs out, and resumes where it left off
- **Remembers your sign-in**: the OAuth token is cached locally, so you only log in once

## Setup

### 1. Install

Requires [Python 3.9+](https://www.python.org/downloads/).

```bash
git clone https://github.com/Sir-Bob-thebald/youtube-playlist-sorter.git
cd youtube-playlist-sorter
```

You can also click **Code → Download ZIP** on GitHub and unzip it. The launchers in step 3 install the dependencies for you, or run `pip install -r requirements.txt` yourself.

### 2. Get Google API credentials

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable the **YouTube Data API v3** under *APIs & Services → Library*.
3. Configure the **OAuth consent screen**: choose *External*, and add your own Google account as a *Test user*.
4. Under *Credentials → Create credentials → OAuth client ID*, choose **Desktop app**.
5. Download the JSON file and save it as `client_secret.json` in the project folder (see `client_secret.example.json` for the format).

> `client_secret.json`, `token.json` and `youtube_config.json` are listed in `.gitignore`. Never commit them.

### 3. Run

- **Windows:** double-click **`Start Playlist Sorter.bat`**.
- **macOS / Linux:** run `./run.sh` from the project folder.

Both launchers install the required packages the first time they run. To start it by hand instead, run `python -m playlist_sorter` (or `py -m playlist_sorter` on Windows).

1. Click **Sign in with Google**. A browser window opens for Google login.
2. The main screen shows how many videos are waiting in your `To Sort` playlist. If that playlist doesn't exist yet, the app offers to create it.
3. Click **Preview** to see where every video would go, grouped by playlist. New playlists are marked with ★. Nothing changes in your account.
4. Click **Sort now** to apply.

Everything else is in **Settings**:

- **Topics**: edit topics and keywords, and test how a title would be classified.
- **Playlists**: choose which playlist each topic goes to.
- **Options**: source playlist name, move vs. copy, videos per run, and the rules for creating new playlists.

### Command line

```bash
python -m playlist_sorter --cli --dry-run          # preview
python -m playlist_sorter --cli --max 40           # sort up to 40 videos
python -m playlist_sorter --cli --keep             # copy instead of move
python -m playlist_sorter --cli --source "Watch Later Sorting"
python -m playlist_sorter --cli --no-new-playlists # only use existing topics
python -m playlist_sorter --cli --min-new 5 --max-new 2 --max-topics 15
```

## How classification works

For each topic, every keyword is matched case-insensitively at the start of a word, so `politic` also matches *political*:

- **+3** if the keyword appears in the title
- **+1** for each occurrence in the title or description

If one topic clearly wins (more than twice the average score), the video goes there. Otherwise it is added to every topic that scored. Videos with no match stay in the source playlist.

### New playlists for unmatched videos

Before sorting, the app looks at every video in the source playlist that matches no topic and searches the titles for shared themes, such as a word or two-word phrase like *chess* or *black hole*. Common filler words (*explained*, *tutorial*, *episode*, *official*…) are ignored. The theme shared by the most videos becomes a new topic, gets its own private playlist, and the process repeats on the videos that are left.

To avoid ending up with lots of playlists holding one or two videos, a theme only becomes a playlist if enough videos share it:

```
minimum videos = max(min_videos, 5% of unmatched videos) + (number of topics ÷ 5)
```

| Situation | Minimum videos for a new playlist |
|---|---|
| 14 unmatched, 4 topics | 3 |
| 60 unmatched, 8 topics | 4 |
| 200 unmatched, 12 topics | 12 |

So a large backlog needs a stronger theme, and the bar keeps rising as your collection gains topics. Two further caps apply: at most `max_new_per_run` new playlists per run (default 3) and at most `max_topics` topics in total (default 20). Videos that don't reach the minimum stay in the source playlist, and get grouped later once similar videos arrive.

New topics are added to `topics.json`, where you can rename them or refine their keywords like any other topic. Use **Preview only** to see which playlists would be created before anything changes. All of these settings are on the **Sort** tab, and you can switch the feature off there.

Topics are stored in [`topics.json`](topics.json). Edit them in the app or by hand.

## API quota

The YouTube Data API allows **10,000 units per day** by default. Adding a video to a playlist costs 50 units and removing one costs 50, so a move costs about 100 units, and creating a playlist costs 50. That works out to **roughly 100 videos per day**. Progress is saved after every video, so the next run picks up where the last one stopped.

## Project structure

```
Start Playlist Sorter.bat   # Windows launcher (double-click)
run.sh                      # macOS / Linux launcher
playlist_sorter/
  core.py      # auth, API calls, categorisation, sorting (no UI)
  gui.py       # Tkinter interface
  cli.py       # command-line interface
  __main__.py  # entry point: GUI by default, --cli for terminal
topics.json    # your topics and keywords
```

## License

[MIT](LICENSE)
