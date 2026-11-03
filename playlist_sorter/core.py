"""Core logic: authentication, playlist discovery, categorisation and sorting.

Nothing in this module talks to the user directly. Progress is reported through
a ``log`` callback so the same code drives both the GUI and the CLI.
"""

import json
import math
import os
import re
import time
from collections import Counter, defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

CONFIG_FILE = "youtube_config.json"
TOPICS_FILE = "topics.json"
TOKEN_FILE = "token.json"
DEFAULT_CLIENT_SECRETS = "client_secret.json"
DEFAULT_SOURCE_PLAYLIST = "To Sort"

# YouTube Data API quota costs (units) for the calls we make.
QUOTA_LIST = 1
QUOTA_INSERT = 50
QUOTA_DELETE = 50
DAILY_QUOTA = 10_000

UNAVAILABLE_TITLES = {"Deleted video", "Private video"}

# Settings for creating new playlists from videos that match no topic.
DEFAULT_AUTO_CREATE = {
    "enabled": True,
    "min_videos": 3,      # floor for how many videos a new playlist must start with
    "max_new_per_run": 3,
    "max_topics": 20,     # never grow beyond this many topics in total
}

DEFAULT_TOPICS = {
    "philosophy": ["philosophy", "ethics", "metaphysics", "epistemology", "existentialism",
                   "stoicism", "kant", "nietzsche", "plato", "aristotle", "socrates"],
    "theology": ["theology", "religion", "christianity", "islam", "judaism", "buddhism",
                 "hinduism", "spiritual", "god", "faith", "scripture", "divine"],
    "math": ["mathematics", "math", "algebra", "calculus", "geometry", "statistics",
             "probability", "theorem", "equation"],
    "programming": ["programming", "coding", "python", "javascript", "java", "algorithm",
                    "developer", "software", "code", "web development"],
}


def _noop(_msg):
    pass


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def load_config(path=CONFIG_FILE):
    """Load saved state (playlist mapping, processed videos, settings)."""
    config = {
        "topic_playlists": {},
        "processed_videos": [],
        "source_playlist_id": None,
        "source_playlist_name": DEFAULT_SOURCE_PLAYLIST,
        "client_secrets_file": DEFAULT_CLIENT_SECRETS,
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    config["auto_create"] = {**DEFAULT_AUTO_CREATE, **(config.get("auto_create") or {})}
    return config


def save_config(config, path=CONFIG_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_topics(path=TOPICS_FILE):
    """Load topic -> keyword list. Falls back to built-in defaults."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            topics = json.load(f)
        return {str(t): [str(k) for k in kws] for t, kws in topics.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {t: list(kws) for t, kws in DEFAULT_TOPICS.items()}


def save_topics(topics, path=TOPICS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topics, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

def get_authenticated_service(client_secrets_file, token_file=TOKEN_FILE, log=_noop):
    """Return an authorised YouTube client, reusing a cached token when possible."""
    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except ValueError:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            log("Refreshing saved credentials...")
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(client_secrets_file):
            raise FileNotFoundError(
                f"Client secrets file not found: {client_secrets_file}\n"
                "Download it from Google Cloud Console (OAuth client ID, type 'Desktop app')."
            )
        log("Opening browser for Google sign-in...")
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            client_secrets_file, SCOPES)
        creds = flow.run_local_server(port=0)

    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    return googleapiclient.discovery.build(API_SERVICE_NAME, API_VERSION, credentials=creds)


def sign_out(token_file=TOKEN_FILE):
    if os.path.exists(token_file):
        os.remove(token_file)


# --------------------------------------------------------------------------- #
# Playlists
# --------------------------------------------------------------------------- #

def list_my_playlists(youtube):
    """Return every playlist on the signed-in channel as [{'id', 'title'}]."""
    playlists = []
    page_token = None
    while True:
        response = youtube.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            playlists.append({"id": item["id"], "title": item["snippet"]["title"]})
        page_token = response.get("nextPageToken")
        if not page_token:
            return playlists


def auto_map_topics(playlists, topics):
    """Map each topic to the first playlist whose title contains the topic name."""
    mapping = {}
    for topic in topics:
        for pl in playlists:
            if topic.lower() in pl["title"].lower():
                mapping[topic] = pl["id"]
                break
    return mapping


def find_playlist(playlists, title):
    for pl in playlists:
        if pl["title"].lower() == title.lower():
            return pl["id"]
    return None


def create_playlist(youtube, title, description="", privacy="private"):
    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        },
    ).execute()
    return response["id"]


def get_playlist_videos(youtube, playlist_id, log=_noop):
    """Fetch every available video in a playlist.

    All pages are read up front so that removing items while sorting does not
    shift later pages and cause videos to be skipped.
    """
    videos = []
    page_token = None
    while True:
        response = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            if snippet.get("title") in UNAVAILABLE_TITLES:
                continue
            videos.append({
                "id": snippet["resourceId"]["videoId"],
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "playlist_item_id": item["id"],
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    log(f"Found {len(videos)} videos in the source playlist.")
    return videos


# --------------------------------------------------------------------------- #
# Categorisation
# --------------------------------------------------------------------------- #

def score_video(video, topics):
    """Return {topic: score}. Title hits weigh 3, every text occurrence adds 1.

    Keywords match at the start of a word, so "politic" also matches "political".
    """
    title = video["title"].lower()
    text = title + " " + video.get("description", "").lower()
    scores = defaultdict(int)
    for topic, keywords in topics.items():
        for keyword in keywords:
            keyword = keyword.strip().lower()
            if not keyword:
                continue
            pattern = r"\b" + re.escape(keyword)
            if re.search(pattern, title):
                scores[topic] += 3
            scores[topic] += len(re.findall(pattern, text))
    return dict(scores)


def categorize_video(video, topics, threshold=1):
    """Return the list of topics a video belongs to (possibly empty)."""
    qualified = {t: s for t, s in score_video(video, topics).items() if s >= threshold}
    if not qualified:
        return []
    best = max(qualified, key=qualified.get)
    mean = sum(qualified.values()) / len(qualified)
    # A single topic or a clear winner gets that topic only.
    if len(qualified) == 1 or qualified[best] > 2 * mean:
        return [best]
    return sorted(qualified, key=qualified.get, reverse=True)


# --------------------------------------------------------------------------- #
# Topic discovery (new playlists for videos that match nothing)
# --------------------------------------------------------------------------- #

# Common words plus YouTube "format" words that describe the video, not its subject.
STOPWORDS = set("""
a about above after again against all also am an and any are aren as at be because been
before being below between both but by can cannot could did do does doing down during each
even ever every few for from further get gets getting got had has have having he her here
hers him his how i if in into is isn it its itself just let like make makes making me more
most much must my never new no nor not now of off on once one only or other our ours out
over own really same she should so some still such than that the their theirs them then
there these they this those through to too two under until up upon us very via vs want was
we were what when where which while who whom why will with without would yet you your yours
ever thing things way ways really actually without within across around among best better
top first last next part full official video videos clip clips episode episodes ep series
season live stream streaming lecture lectures explained explain explaining tutorial
tutorials guide intro introduction course lesson lessons class talk talks interview
podcast review reviews reaction react documentary short shorts trailer highlights compilation
version remastered edit hd 4k feat ft vlog update news day days week year years time
minute minutes hour hours min mins everything nothing something anything someone people
know need look watch see tell told said says say will shall might may great good bad big
small little old young many lot lots very made using use used vs pt vol chapter
subscribe channel""".split())


def _canon(word):
    """Fold simple plurals so 'game' and 'games' count as the same term."""
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _title_terms(title):
    """Return {canonical term: surface form} for the meaningful words and bigrams of a title."""
    words = re.findall(r"[^\W\d_]+", title.lower())
    keep = [len(w) >= 3 and w not in STOPWORDS for w in words]
    terms = {}
    for i, w in enumerate(words):
        if keep[i]:
            terms.setdefault(_canon(w), w)
            if i + 1 < len(words) and keep[i + 1]:
                bigram = f"{w} {_canon(words[i + 1])}"
                terms.setdefault(bigram, f"{w} {words[i + 1]}")
    return terms


def new_playlist_threshold(unmatched_count, topic_count, base_min=3):
    """Minimum number of videos a new playlist must start with.

    It scales with context so playlists stay meaningful:
      * a larger backlog demands bigger groups (5% of unmatched videos), and
      * every 5 existing topics raise the bar by one, so the collection
        gets pickier about adding more playlists as it grows.
    """
    return max(base_min, math.ceil(unmatched_count * 0.05)) + topic_count // 5


def discover_topics(videos, topics, *, min_videos, max_new, max_topics):
    """Group videos that match no topic into candidate new topics.

    Greedy: repeatedly take the title term shared by the most remaining videos.
    A term only becomes a topic if at least ``min_videos`` videos share it; the
    rest are left alone for a future run, once more similar videos turn up.
    Returns a list of {'name', 'title', 'keywords', 'videos'}.
    """
    blocked = set()
    for name, keywords in topics.items():
        blocked.add(_canon(name.lower()))
        blocked.update(_canon(k.strip().lower()) for k in keywords)

    remaining = [(v, _title_terms(v["title"])) for v in videos]
    found = []
    while remaining and len(found) < max_new and len(topics) + len(found) < max_topics:
        df = Counter()
        surface = defaultdict(Counter)
        for _v, terms in remaining:
            for term, form in terms.items():
                df[term] += 1
                surface[term][form] += 1

        candidates = [(t, c) for t, c in df.items() if c >= min_videos and t not in blocked]
        if not candidates:
            break
        # Prefer the most shared term; a two-word phrase gets a small bonus as it is more specific.
        seed, _ = max(candidates, key=lambda tc: (tc[1] * (1.25 if " " in tc[0] else 1), len(tc[0])))

        cluster = [(v, terms) for v, terms in remaining if seed in terms]
        co = Counter(t for _v, terms in cluster for t in terms if t != seed)
        seed_words = set(seed.split())
        needed = max(2, math.ceil(len(cluster) * 0.5))
        related = [t for t, c in co.most_common()
                   if c >= needed and t not in blocked and not set(t.split()) <= seed_words][:5]

        name = surface[seed].most_common(1)[0][0]
        found.append({
            "name": name,
            "title": name.title(),
            "keywords": [seed] + related,
            "videos": [v for v, _t in cluster],
        })
        blocked.update([seed, *related])
        cluster_ids = {id(v) for v, _t in cluster}
        remaining = [(v, t) for v, t in remaining if id(v) not in cluster_ids]
    return found


def _create_new_topics(youtube, videos, topics, mapping, auto_create, *, dry_run, quota, log):
    """Discover topics among unmatched videos and (unless dry_run) create their playlists.

    Mutates ``topics`` and ``mapping``. Returns a list describing each new topic.
    """
    unmatched = [v for v in videos if not categorize_video(v, topics)]
    if not unmatched:
        return []
    threshold = new_playlist_threshold(len(unmatched), len(topics), auto_create["min_videos"])
    log(f"{len(unmatched)} videos match no topic; a new playlist needs at least "
        f"{threshold} similar videos ({len(topics)} topics so far).")
    if len(topics) >= auto_create["max_topics"]:
        log(f"Topic limit ({auto_create['max_topics']}) reached; not creating new playlists.")
        return []

    found = discover_topics(unmatched, topics, min_videos=threshold,
                            max_new=auto_create["max_new_per_run"],
                            max_topics=auto_create["max_topics"])
    if not found:
        log("No group of unmatched videos is large enough for a new playlist yet.")
        return []

    existing = {} if dry_run else {p["title"].lower(): p["id"] for p in list_my_playlists(youtube)}
    created = []
    for topic in found:
        name, title = topic["name"], topic["title"]
        if dry_run:
            pid = "(new)"
            log(f"[preview] Would create playlist '{title}' for {len(topic['videos'])} videos "
                f"(keywords: {', '.join(topic['keywords'])})")
        elif title.lower() in existing:
            pid = existing[title.lower()]
            log(f"Using existing playlist '{title}' for new topic '{name}'.")
        else:
            try:
                pid = create_playlist(youtube, title, "Created automatically by YouTube Playlist Sorter")
                quota.add(QUOTA_INSERT)
                log(f"Created playlist '{title}' for {len(topic['videos'])} videos "
                    f"(keywords: {', '.join(topic['keywords'])})")
            except googleapiclient.errors.HttpError as e:
                log(f"Could not create playlist '{title}': {_http_reason(e)}")
                continue
        topics[name] = topic["keywords"]
        mapping[name] = pid
        created.append({"name": name, "title": title, "playlist_id": pid,
                        "keywords": topic["keywords"], "count": len(topic["videos"])})
    return created


# --------------------------------------------------------------------------- #
# Sorting run
# --------------------------------------------------------------------------- #

class QuotaTracker:
    def __init__(self):
        self.used = 0

    def add(self, units):
        self.used += units


def sort_playlist(youtube, config, topics, *, dry_run=False, remove_from_source=True,
                  max_videos=None, delay=0.5, auto_create=None, log=_noop, on_result=None,
                  on_progress=None, should_stop=lambda: False):
    """Sort videos from the source playlist into topic playlists.

    ``auto_create`` (see DEFAULT_AUTO_CREATE) enables creating new playlists for
    groups of videos that match no topic; ``None`` or ``enabled: False`` skips it.
    ``topics`` is not modified; new topics are returned in ``summary['new_topics']``
    and, unless ``dry_run``, saved to topics.json and the config.
    ``on_result(video, topics, action)`` is called for every video.
    ``on_progress(done, total)`` is called after every video.
    Returns a summary dict.
    """
    quota = QuotaTracker()
    source_id = config["source_playlist_id"]
    topics = {t: list(k) for t, k in topics.items()}
    # Work on a copy so a preview never records placeholder playlists.
    mapping = dict(config["topic_playlists"])
    processed = config.setdefault("processed_videos", [])
    processed_set = set(processed)

    videos = get_playlist_videos(youtube, source_id, log)
    quota.add(QUOTA_LIST * max(1, -(-len(videos) // 50)))

    new_topics = []
    if auto_create and auto_create.get("enabled"):
        # Discovery looks at the whole backlog, not just this run's slice, for better context.
        pending = [v for v in videos if v["id"] not in processed_set]
        new_topics = _create_new_topics(youtube, pending, topics, mapping, auto_create,
                                        dry_run=dry_run, quota=quota, log=log)
        if new_topics and not dry_run:
            config["topic_playlists"] = mapping
            save_topics(topics)
            save_config(config)
    new_names = {t["name"] for t in new_topics}

    if max_videos:
        videos = videos[:max_videos]

    summary = {"sorted": 0, "skipped": 0, "unmatched": 0, "errors": 0,
               "total": len(videos), "dry_run": dry_run, "new_topics": new_topics}

    for i, video in enumerate(videos, 1):
        if should_stop():
            log("Stopped by user.")
            break

        if video["id"] in processed_set:
            summary["skipped"] += 1
            _emit(on_result, video, [], "already processed")
            _emit(on_progress, i, len(videos))
            continue

        matched = categorize_video(video, topics)
        targets = [t for t in matched if t in mapping]

        if not matched:
            summary["unmatched"] += 1
            log(f"No match: {video['title']}")
            _emit(on_result, video, [], "no match")
        elif not targets:
            summary["unmatched"] += 1
            log(f"Matched {matched} but no playlist is mapped: {video['title']}")
            _emit(on_result, video, matched, "no playlist mapped")
        elif dry_run:
            summary["sorted"] += 1
            log(f"[preview] {video['title']} -> {', '.join(targets)}")
            action = "would move" if remove_from_source else "would copy"
            _emit(on_result, video, targets, action + _new_suffix(targets, new_names))
        else:
            added = []
            for topic in targets:
                try:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={"snippet": {
                            "playlistId": mapping[topic],
                            "resourceId": {"kind": "youtube#video", "videoId": video["id"]},
                        }},
                    ).execute()
                    quota.add(QUOTA_INSERT)
                    added.append(topic)
                except googleapiclient.errors.HttpError as e:
                    log(f"Error adding '{video['title']}' to {topic}: {_http_reason(e)}")
                    if _is_quota_error(e):
                        log("Daily API quota exhausted. Try again tomorrow.")
                        summary["errors"] += 1
                        _save_progress(config, processed)
                        summary["quota_used"] = quota.used
                        return summary

            if added:
                action = "copied"
                if remove_from_source:
                    try:
                        youtube.playlistItems().delete(id=video["playlist_item_id"]).execute()
                        quota.add(QUOTA_DELETE)
                        action = "moved"
                    except googleapiclient.errors.HttpError as e:
                        log(f"Error removing '{video['title']}' from source: {_http_reason(e)}")
                processed.append(video["id"])
                processed_set.add(video["id"])
                summary["sorted"] += 1
                log(f"{action.capitalize()}: {video['title']} -> {', '.join(added)}")
                _emit(on_result, video, added, action + _new_suffix(added, new_names))
                _save_progress(config, processed)
            else:
                summary["errors"] += 1
                _emit(on_result, video, targets, "error")

            if delay:
                time.sleep(delay)

        _emit(on_progress, i, len(videos))

    summary["quota_used"] = quota.used
    return summary


def _new_suffix(targets, new_names):
    return " (new playlist)" if new_names.intersection(targets) else ""


def _emit(callback, *args):
    if callback:
        callback(*args)


def _save_progress(config, processed):
    config["processed_videos"] = processed
    save_config(config)


def _http_reason(error):
    try:
        return json.loads(error.content)["error"]["message"]
    except Exception:
        return str(error)


def _is_quota_error(error):
    return getattr(error, "resp", None) is not None and error.resp.status == 403 \
        and b"quota" in (error.content or b"").lower()
