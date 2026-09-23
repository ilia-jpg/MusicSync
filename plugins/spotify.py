import spotipy
from spotipy.oauth2 import SpotifyOAuth
import logging
import re
from typing import Optional
import requests

from infrastructure.config import ConfigManager
from infrastructure.models import Collection, MediaItem

logger = logging.getLogger(__name__)

class SpotifySourcePlugin:
    """A pure data fetcher. Does not interact with the database."""
    source_type = "spotify"
    
    def __init__(self, config: ConfigManager):
        self.config = config
        
        client_id = self.config.get("spotify.client_id")
        client_secret = self.config.get("spotify.client_secret")
        
        if not client_id or not client_secret:
            raise ValueError("Spotify credentials missing in config.yaml")

        self.client = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="http://127.0.0.1:8080",
            scope="playlist-read-private playlist-read-collaborative"
        ))

    def fetch_collection(self, url: str) -> Optional[Collection]:
        playlist_id = self._extract_playlist_id(url)
        if not playlist_id:
            logger.error(f"Invalid Spotify URL: {url}")
            return None

        try:
            pl_data = self.client.playlist(playlist_id, fields="name,external_urls")
            playlist_name = pl_data.get('name', 'Unknown Spotify Playlist')
            external_url = pl_data.get('external_urls', {}).get('spotify', url)
            
            logger.info(f"Fetching data from Spotify: '{playlist_name}'")
            
            items = self._fetch_playlist_tracks(playlist_id)
            
            return Collection(
                name=playlist_name,
                source_type='spotify',
                external_id=playlist_id,
                external_url=external_url,
                items=items
            )
            
        except requests.exceptions.RequestException as e:
            # Catches network timeouts, DNS failures, and dead URLs cleanly
            import logging
            logging.error(f"Network error while reaching Spotify: {e}")
            return None
            
        except spotipy.SpotifyException as e:
            # Catches Spotify API errors (like private playlists or bad IDs)
            import logging
            logging.error(f"Spotify API rejected the request: {e}")
            return None
            
        except Exception as e:
            # Catches everything else
            import logging
            logging.error(f"Unexpected error parsing Spotify playlist: {e}")
            return None
        
    def fetch_item(self, url: str) -> Optional[MediaItem]:
        """Fetches a single track or episode and returns a standardized MediaItem."""
        track_match = re.search(r'(?:track/|track:)([a-zA-Z0-9]{22})', url)
        episode_match = re.search(r'(?:episode/|episode:)([a-zA-Z0-9]{22})', url)
        
        try:
            if track_match:
                track_id = track_match.group(1)
                track = self.client.track(track_id)
                logger.info(f"Fetching track from Spotify: '{track['name']}'")
                return MediaItem(
                    title=track['name'],
                    artist=", ".join([a['name'] for a in track['artists']]),
                    album=track['album']['name'],
                    duration_ms=track['duration_ms'],
                    media_type='music',
                    external_id=track_id,
                    external_source=self.source_type,
                    external_url=track.get('external_urls', {}).get('spotify'),
                )
            elif episode_match:
                ep_id = episode_match.group(1)
                ep = self.client.episode(ep_id)
                logger.info(f"Fetching episode from Spotify: '{ep['name']}'")
                return MediaItem(
                    title=ep['name'],
                    artist=ep['show']['name'],
                    duration_ms=ep['duration_ms'],
                    media_type='podcast',
                    external_id=ep_id,
                    external_source=self.source_type,
                    external_url=ep.get('external_urls', {}).get('spotify'),
                )
            else:
                logger.error(f"Invalid Spotify Track/Episode URL: {url}")
                return None
        
        except requests.exceptions.RequestException as e:
            # Catches network timeouts, DNS failures, and dead URLs cleanly
            import logging
            logging.error(f"Network error while reaching Spotify: {e}")
            return None
            
        except spotipy.SpotifyException as e:
            # Catches Spotify API errors (like private playlists or bad IDs)
            import logging
            logging.error(f"Spotify API rejected the request: {e}")
            return None
            
        except Exception as e:
            # Catches everything else
            import logging
            logging.error(f"Unexpected error parsing Spotify media item: {e}")
            return None
        
    def _extract_playlist_id(self, uri_or_url: str) -> str | None:
        match = re.search(r'(?:playlist/|playlist:)([a-zA-Z0-9]{22})', uri_or_url)
        return match.group(1) if match else None

    def _fetch_playlist_tracks(self, playlist_id: str) -> list[MediaItem]:
        items = []
        try:
            results = self.client.playlist_items(playlist_id, additional_types=['track', 'episode'])
            
            while results:
                for item in results['items']:
                    track = item.get('track') or item.get('item')
                    
                    if not track or track.get('is_local'):
                        continue
                        
                    media_type = 'podcast' if track.get('type') == 'episode' else 'music'
                    album_name = track['album']['name'] if 'album' in track else None
                    artist_name = ", ".join([a['name'] for a in track.get('artists', [])]) if 'artists' in track else track.get('show', {}).get('name', 'Unknown Podcast')
                        
                    media_item = MediaItem(
                        title=track['name'],
                        artist=artist_name,
                        album=album_name,
                        duration_ms=track['duration_ms'],
                        media_type=media_type,
                        external_id=track['id'],
                        external_source=self.source_type,
                        external_url=track.get('external_urls', {}).get('spotify'),
                    )
                    items.append(media_item)
                
                results = self.client.next(results) if results['next'] else None
                    
        except Exception as e:
            logger.error(f"Failed to fetch tracks: {e}")
            
        return items
