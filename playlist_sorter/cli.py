"""Command-line interface (headless alternative to the GUI)."""

import argparse
import sys

from . import core


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m playlist_sorter --cli",
        description="Sort videos from a YouTube 'To Sort' playlist into topic playlists.")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--secrets", help="path to client_secret.json")
    parser.add_argument("--source", help="name of the source playlist (default: saved or 'To Sort')")
    parser.add_argument("--dry-run", action="store_true", help="preview without changing anything")
    parser.add_argument("--keep", action="store_true", help="copy instead of move (keep in source)")
    parser.add_argument("--max", type=int, default=None, help="maximum videos to process this run")
    parser.add_argument("--no-new-playlists", action="store_true",
                        help="don't create playlists for videos that match no topic")
    parser.add_argument("--min-new", type=int, metavar="N",
                        help="minimum videos needed to start a new playlist (floor; scales up automatically)")
    parser.add_argument("--max-new", type=int, metavar="N", help="maximum new playlists per run")
    parser.add_argument("--max-topics", type=int, metavar="N", help="never grow beyond N topics")
    args = parser.parse_args(argv)

    # Video titles often contain emoji; don't crash on consoles that can't show them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    config = core.load_config()
    topics = core.load_topics()
    if args.secrets:
        config["client_secrets_file"] = args.secrets
    if args.source:
        config["source_playlist_name"] = args.source
    auto = config["auto_create"]
    if args.no_new_playlists:
        auto["enabled"] = False
    for key, value in (("min_videos", args.min_new), ("max_new_per_run", args.max_new),
                       ("max_topics", args.max_topics)):
        if value:
            auto[key] = value

    youtube = core.get_authenticated_service(config["client_secrets_file"], log=print)
    playlists = core.list_my_playlists(youtube)

    source_name = config["source_playlist_name"]
    source_id = core.find_playlist(playlists, source_name)
    if not source_id:
        print(f"Creating source playlist '{source_name}'...")
        source_id = core.create_playlist(youtube, source_name, "Videos pending categorization")
    config["source_playlist_id"] = source_id

    mapping = core.auto_map_topics([p for p in playlists if p["id"] != source_id], topics)
    mapping.update({t: pid for t, pid in config.get("topic_playlists", {}).items()
                    if t in topics and any(p["id"] == pid for p in playlists)})
    config["topic_playlists"] = mapping
    core.save_config(config)

    if not mapping and not auto["enabled"]:
        print("No topic playlists found. Create playlists whose names contain your topic names.")
        return 1
    titles = {p["id"]: p["title"] for p in playlists}
    for topic, pid in mapping.items():
        print(f"  {topic:<15} -> {titles.get(pid, pid)}")

    summary = core.sort_playlist(youtube, config, topics, dry_run=args.dry_run,
                                 remove_from_source=not args.keep, max_videos=args.max,
                                 auto_create=auto, log=print)
    print(f"\nDone: {summary['sorted']} sorted, {summary['unmatched']} unmatched, "
          f"{summary['skipped']} skipped, {summary['errors']} errors, "
          f"~{summary.get('quota_used', 0)} quota units used.")
    for t in summary["new_topics"]:
        verb = "Would create" if args.dry_run else "Created"
        print(f"  {verb} playlist '{t['title']}' ({t['count']} videos; keywords: {', '.join(t['keywords'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
