import os
import re
import json
import tkinter as tk
from tkinter import filedialog
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from collections import defaultdict
import time

# API information
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Define keywords for each topic
# You should customize these based on your interests
TOPIC_KEYWORDS = {
    "philosophy": ["philosophy", "ethics", "metaphysics", "epistemology", "existentialism", 
                  "stoicism", "kant", "nietzsche", "plato", "aristotle", "socrates","derrida","postmodern","zizek","politic","hegel","acid"],
    
    "theology": ["theology", "religion", "christianity", "islam", "judaism", "buddhism", 
                "hinduism", "spiritual", "god", "faith", "scripture", "divine"],
    
    "math": ["mathematics", "math", "algebra", "calculus", "geometry", "statistics", 
            "probability", "theorem", "equation", "mathematical"],
    
    "programming": ["programming", "coding", "python", "javascript", "java", "algorithm", 
                   "developer", "software", "code", "web development", "app"]
    
    # Add more topics and keywords as needed
}

# Configuration filename
CONFIG_FILE = "youtube_config.json"

def get_client_secrets_file():
    """Open a file dialog to select the client_secret.json file."""
    print("Please select your client_secret.json file in the dialog that opens")
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    file_path = filedialog.askopenfilename(
        title="Select client_secret.json file",
        filetypes=[("JSON files", "*.json")]
    )
    if not file_path:
        raise Exception("No client secrets file selected. Exiting.")
    print(f"Using client secrets file: {file_path}")
    return file_path

def get_authenticated_service():
    """Authenticate with the YouTube API and return the service object."""
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        get_client_secrets_file(), SCOPES)
    credentials = flow.run_local_server(port=8080)
    return googleapiclient.discovery.build(
        API_SERVICE_NAME, API_VERSION, credentials=credentials)

def load_config():
    """Load the saved configuration."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        return config
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "topic_playlists": {},
            "processed_videos": [],
            "source_playlist_id": None
        }

def save_config(config):
    """Save the configuration for future use."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)
        print(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"Error saving configuration: {e}")

def get_playlists(youtube):
    """Get all playlists and map them to topics."""
    print("Fetching your playlists...")
    topic_playlists = {}
    
    try:
        request = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50  # Adjust if you have more playlists
        )
        response = request.execute()
        
        source_playlist_id = None
        source_playlist_name = "To Sort"
        
        for item in response["items"]:
            playlist_title = item["snippet"]["title"]
            playlist_id = item["id"]
            
            # Check if this is our source playlist
            if playlist_title.lower() == source_playlist_name.lower():
                source_playlist_id = playlist_id
                print(f"Found source playlist: {playlist_title} ({playlist_id})")
            
            # Check if this playlist matches one of our defined topics
            playlist_title_lower = playlist_title.lower()
            for topic in TOPIC_KEYWORDS.keys():
                if topic in playlist_title_lower:
                    topic_playlists[topic] = playlist_id
                    print(f"Found topic playlist: {playlist_title} -> {topic}")
                    break
        
        # Create source playlist if it doesn't exist
        if not source_playlist_id:
            print(f"Creating source playlist '{source_playlist_name}'...")
            request = youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": source_playlist_name,
                        "description": "Videos pending categorization"
                    },
                    "status": {
                        "privacyStatus": "private"
                    }
                }
            )
            response = request.execute()
            source_playlist_id = response["id"]
            print(f"Created source playlist with ID: {source_playlist_id}")
        
        return topic_playlists, source_playlist_id
    
    except googleapiclient.errors.HttpError as e:
        print(f"Error getting playlists: {e}")
        return {}, None

def get_batch_of_videos(youtube, playlist_id, page_token=None, max_results=10):
    """Get a batch of videos from a playlist to save quota."""
    if not playlist_id:
        print("Invalid playlist ID")
        return [], None
    
    videos = []
    
    try:
        request = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=max_results,
            pageToken=page_token
        )
        response = request.execute()
        
        next_page_token = response.get("nextPageToken")
        
        for item in response["items"]:
            try:
                video_id = item["snippet"]["resourceId"]["videoId"]
                video_title = item["snippet"]["title"]
                
                # Skip deleted or private videos
                if video_title == "Deleted video" or video_title == "Private video":
                    continue
                
                # Extract what we can from the playlistItems response to save quota
                description = item["snippet"].get("description", "")
                
                videos.append({
                    "id": video_id,
                    "title": video_title,
                    "description": description,
                    "tags": [],  # We'll have to skip tags to save quota
                    "playlist_item_id": item["id"]
                })
            except Exception as e:
                print(f"Error processing video: {e}")
        
        return videos, next_page_token
                
    except googleapiclient.errors.HttpError as e:
        print(f"Error getting videos from playlist {playlist_id}: {e}")
        return [], None

def categorize_video(video, topics_keywords):
    """Determine which topic(s) a video belongs to based on its metadata."""
    video_title = video["title"].lower()
    video_description = video["description"].lower()
    
    # Combine text for analysis (no tags to save quota)
    all_text = video_title + " " + video_description
    
    # Score each topic based on keyword matches
    topic_scores = defaultdict(int)
    
    for topic, keywords in topics_keywords.items():
        for keyword in keywords:
            # Add weight if keyword is in title (most important)
            if keyword.lower() in video_title:
                topic_scores[topic] += 3
            
            # Count occurrences in the text
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            matches = len(re.findall(pattern, all_text))
            topic_scores[topic] += matches
    
    # Get topics with scores above threshold
    threshold = 1
    qualified_topics = {topic: score for topic, score in topic_scores.items() if score >= threshold}
    
    if not qualified_topics:
        return []
    
    # If we have exactly one topic, or a clear winner, just return that
    if len(qualified_topics) == 1 or max(qualified_topics.values()) > 2 * sum(qualified_topics.values()) / len(qualified_topics):
        return [max(qualified_topics, key=qualified_topics.get)]
    
    # Otherwise return all topics with decent scores
    return list(qualified_topics.keys())

def process_batch(youtube, videos, topic_playlists, source_playlist_id, processed_videos):
    """Process a batch of videos to save quota."""
    batch_processed = 0
    batch_already_processed = 0
    
    for video in videos:
        video_id = video["id"]
        
        # Skip if we've already processed this video
        if video_id in processed_videos:
            print(f"Skipping already processed video: {video['title']}")
            batch_already_processed += 1
            continue
            
        print(f"\nProcessing video: {video['title']}")
        
        # Determine which topics this video matches
        matched_topics = categorize_video(video, TOPIC_KEYWORDS)
        
        if matched_topics:
            print(f"  Matched topics: {matched_topics}")
            
            # Check if we have playlists for these topics
            added_to_playlist = False
            for topic in matched_topics:
                if topic in topic_playlists:
                    playlist_id = topic_playlists[topic]
                    print(f"  Adding to '{topic}' playlist")
                    
                    try:
                        youtube.playlistItems().insert(
                            part="snippet",
                            body={
                                "snippet": {
                                    "playlistId": playlist_id,
                                    "resourceId": {
                                        "kind": "youtube#video",
                                        "videoId": video_id
                                    }
                                }
                            }
                        ).execute()
                        added_to_playlist = True
                    except googleapiclient.errors.HttpError as e:
                        print(f"  Error adding to playlist: {e}")
            
            # Remove from source playlist if added to at least one topic playlist
            if added_to_playlist:
                try:
                    youtube.playlistItems().delete(id=video["playlist_item_id"]).execute()
                    print("  Removed from source playlist")
                    batch_processed += 1
                    processed_videos.append(video_id)
                except googleapiclient.errors.HttpError as e:
                    print(f"  Error removing from source playlist: {e}")
        else:
            print("  No matching topics found, keeping in source playlist")
    
    return batch_processed, batch_already_processed

def main():
    # When running locally, disable OAuthlib's HTTPS verification
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    
    # Load existing configuration
    config = load_config()
    topic_playlists = config.get("topic_playlists", {})
    processed_videos = config.get("processed_videos", [])
    source_playlist_id = config.get("source_playlist_id")
    
    # Get authenticated YouTube API service
    youtube = get_authenticated_service()
    print("Authentication successful!")
    
    # Check if we need to refresh playlist information
    if not topic_playlists or not source_playlist_id:
        topic_playlists, source_playlist_id = get_playlists(youtube)
        
        if not topic_playlists:
            print("No topic playlists found. Please create playlists with names that include your topics.")
            return
            
        if not source_playlist_id:
            print("Could not find or create 'To Sort' playlist. Exiting.")
            return
            
        # Update configuration
        config["topic_playlists"] = topic_playlists
        config["source_playlist_id"] = source_playlist_id
        save_config(config)
    
    print(f"Using source playlist ID: {source_playlist_id}")
    print(f"Using topic playlists: {topic_playlists}")
    print(f"Already processed {len(processed_videos)} videos")
    
    # Process videos in batches to save quota
    batch_size = 5  # Small batch size to avoid quota issues
    page_token = None
    total_processed = 0
    total_skipped = 0
    
    print("\nStarting to process videos in batches...")
    
    # Process one batch at a time
    while True:
        print(f"\nFetching batch of up to {batch_size} videos...")
        videos, page_token = get_batch_of_videos(youtube, source_playlist_id, page_token, batch_size)
        
        if not videos:
            print("No more videos to process or error fetching videos.")
            break
            
        print(f"Got {len(videos)} videos in this batch")
        
        # Process this batch
        batch_processed, batch_skipped = process_batch(
            youtube, videos, topic_playlists, source_playlist_id, processed_videos)
        
        total_processed += batch_processed
        total_skipped += batch_skipped
        
        # Save progress after each batch
        config["processed_videos"] = processed_videos
        save_config(config)
        
        print(f"\nBatch summary: Processed {batch_processed}, Skipped {batch_skipped}")
        
        if not page_token:
            print("No more pages of videos to process.")
            break
            
        # Optional: Add a delay between batches to spread out API usage
        print("Waiting 5 seconds before processing next batch...")
        time.sleep(5)
    
    print(f"\nProcessing complete! Processed {total_processed} videos, Skipped {total_skipped} already processed videos.")

if __name__ == "__main__":
    main()