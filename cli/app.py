import sys
import logging
import msvcrt
import re
from pathlib import Path

from core.settings import SettingsManager
from infrastructure.database import DatabaseManager
from core.library import LibraryManager
from core.collections import CollectionManager
from core.review import ReviewManager
from core.acquisition import AcquisitionManager
from core.exports import ExportManager
from core.matching import MatchingEngine
from core.jobs import JobManager, JobType, JobStatus
from core.models import MediaQuery

from plugins.spotify import SpotifySourcePlugin
from plugins.youtube import YouTubeSourcePlugin

logger = logging.getLogger(__name__)

class MusicSyncCLI:
    def __init__(self):
        # 1. Initialize Settings (Replaces ConfigManager)
        self.settings = SettingsManager()
        self.config = self.settings # Alias so legacy managers don't break
        
        # 2. Database MUST be initialized next
        self.db = DatabaseManager(self.settings.get("database_path", "./database.db"))
        
        # 3. Then Library, which needs the DB
        self.library = LibraryManager(self.config, self.db)
        
        # 4. Then everything else
        self.jobs = JobManager(max_workers=4)
        self.collections = CollectionManager(self.db)
        self.collections.recover_draft_revisions()
        self.review = ReviewManager(self.db)
        self.acquisition = AcquisitionManager(self.config, self.db, self.library)
        self.exports = ExportManager(self.config, self.db)
        
        self.spotify = SpotifySourcePlugin(self.config)
        self.youtube_plugin = YouTubeSourcePlugin(self.config)
        
        # The Plugin Registry pattern
        self.discovery_plugins = {
            'spotify': self.spotify,
            'youtube': self.youtube_plugin
        }
        self.plugins = self.discovery_plugins
        self.resolver_plugins = [self.youtube_plugin]
        self.matching = MatchingEngine(self.config, self.db, self.resolver_plugins)

    def _get_keypress(self, prompt=""):
        print(prompt, end='', flush=True)
        char = msvcrt.getch().decode('utf-8', 'ignore').lower()
        print(char)
        return char

    def print_main_menu(self):
        print("\n=============================================")
        print(" 🎵 MusicSync v3 - Master Control Menu")
        print("=============================================")
        print("  [1] Discover & Match")
        print("  [2] Process Review Queue")
        print("  [3] Download Approved Matches")
        print("  [4] Verify Physical Library")
        print("  [5] Export Collections")
        print("  [6] Manage Collections")
        print("  [7] Background Jobs Dashboard")
        print("  [8] System Settings")
        print("  [q] Quit")
        print("=============================================")

    def run(self):
        try:
            while True:
                self.print_main_menu()
                choice = self._get_keypress("Select an option: ")
                
                if choice == '1': 
                    self._run_discovery()
                elif choice == '2': 
                    # Powered by the new MediaQuery Engine
                    query = MediaQuery(in_review_queue=True)
                    items_to_review = self.collections.search_media(query)
                    self.review.review_media(items_to_review)
                elif choice == '3':
                    # Powered by the new MediaQuery Engine
                    query = MediaQuery(matched=True, downloaded=False)
                    pending_downloads = self.collections.search_media(query)
                    self.jobs.submit(
                        job_type=JobType.DOWNLOAD_COLLECTION,
                        description="Downloading Approved Matches",
                        target_func=self.acquisition.download_media,
                        media_items=pending_downloads
                    )
                    print("\n✅ Download job queued! View progress in [7] Background Jobs.")
                elif choice == '4':
                    self.jobs.submit(
                        job_type=JobType.VERIFY_LIBRARY,
                        description="Verifying Physical Library",
                        target_func=self.library.verify_library
                    )
                    print("\n✅ Library verification job queued! View progress in [7] Background Jobs.")
                elif choice == '5': 
                    self._handle_exports()
                elif choice == '6': 
                    self._handle_collections()
                elif choice == '7': 
                    self._handle_jobs()
                elif choice == '8': 
                    self._handle_settings()
                elif choice == 'q':
                    print("\nShutting down background jobs safely...")
                    self.collections.commit_all_draft_revisions("app_close")
                    self.jobs.shutdown()
                    break
        except KeyboardInterrupt:
            print("\nShutting down background jobs safely...")
            self.collections.commit_all_draft_revisions("app_close")
            self.jobs.shutdown()
            sys.exit(0)

    def _run_discovery(self):
        print("\n--- Phase 1: Discovery (Queueing Refresh Jobs) ---")
        
        # Join collections table to filter out archived/deleted items, and fetch names for the UI.
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.collection_id, c.name, cs.source_type, cs.external_url 
                FROM collections c
                JOIN collection_sources cs ON c.collection_id = cs.collection_id
                WHERE cs.external_url IS NOT NULL
                  AND (c.status != 'DELETED' OR c.status IS NULL)
                  AND (c.status != 'ARCHIVED' OR c.status IS NULL)
            """)
            sources = [dict(row) for row in cursor.fetchall()]

        if not sources:
            print("  No active external collections found to refresh.")
        else:
            print("Select collections to refresh:")
            print("  [0] Refresh ALL Collections")
            for i, src in enumerate(sources, 1):
                print(f"  [{i}] {src['name']} ({src['source_type']})")
            print("  [s] Skip Refresh (Run Phase 2 Matching Only)")

            choice = input("\nSelect option: ").strip().lower()

            targets = []
            if choice == '0':
                targets = sources
            elif choice.isdigit() and 1 <= int(choice) <= len(sources):
                targets = [sources[int(choice)-1]]
            elif choice == 's':
                targets = []
            else:
                print("❌ Invalid selection. Skipping refresh.")

            for source in targets:
                url = source['external_url']
                s_type = source['source_type']
                name = source['name']
                plugin = self.plugins.get(s_type)
                
                if plugin:
                    # Wrapper function to ensure the network fetch yields to the job context
                    def fetch_and_save(p, u, c_mgr, source_type=None, collection_id=None, job_context=None):
                        if job_context: job_context.update_current_item(f"Fetching {source_type or 'source'} data...")
                        fetch_kwargs = {}
                        if source_type == "youtube" and self._is_youtube_radio_url(u):
                            count = c_mgr.count_active_collection_tracks(collection_id) if collection_id else 0
                            if count:
                                fetch_kwargs["max_results"] = count
                                if job_context:
                                    job_context.update_progress_message(f"Keeping radio size at {count} videos")
                        coll = p.fetch_collection(u, **fetch_kwargs)
                        if coll and not (job_context and job_context.is_cancelled):
                            c_mgr.save_collection(coll, job_context=job_context)
                            
                    self.jobs.submit(
                        job_type=JobType.REFRESH_COLLECTION,
                        description=f"Refresh: {name}",
                        target_func=fetch_and_save,
                        p=plugin,
                        u=url,
                        c_mgr=self.collections,
                        source_type=s_type,
                        collection_id=source.get("collection_id"),
                    )
                else:
                    logger.warning(f"No plugin registered for source type: {s_type}")
                    
            if targets:
                print("\n✅ Refresh jobs queued! They are running in the background.")

        print("\n--- Phase 2: Matching Engine ---")
        self.jobs.submit(
            job_type=JobType.DISCOVER_MATCH,
            description="Matching Discovered Tracks",
            target_func=self.matching.process_discovered_tracks
        )
        print("✅ Matching Engine queued! (View progress in [7] Background Jobs)")
        print("Note: It may take a moment for fetched playlists to finish importing before they are matched.")

    def _is_youtube_radio_url(self, url: str | None) -> bool:
        value = (url or "").lower()
        return "list=rd" in value or "start_radio=1" in value

    def _handle_exports(self):
        while True:
            print("\n--- Export Collections ---")
            print("  [1] Create One-Time Export (Snapshot)")
            print("  [2] Create Managed Export (Tracked)")
            print("  [3] Update Managed Export(s)")
            print("  [4] Delete Managed Export(s)")
            print("  [5] Scan & Recover Lost Exports")
            print("  [b] Back to Main Menu")
            
            choice = self._get_keypress("Select option: ")
            
            if choice == 'b': break
                
            elif choice in ['1', '2']:
                active_cols = [c for c in self.collections.get_all_collections() if c['status'] == 'ACTIVE']
                if not active_cols:
                    print("\n❌ No active collections available.")
                    continue
                    
                print(f"\n--- Select Collection for {'One-Time' if choice == '1' else 'Managed'} Export ---")
                for i, c in enumerate(active_cols, 1):
                    print(f"  [{i}] {c['name']} ({c['track_count']} tracks)")
                
                col_sel = input("\nSelect collection number: ").strip()
                if col_sel.isdigit() and 1 <= int(col_sel) <= len(active_cols):
                    col_data = active_cols[int(col_sel)-1]
                    
                    roots = self.config.get("exports.roots", ["./exports_root"])
                    if isinstance(roots, str): roots = [roots]
                    
                    print("\n--- Select Export Root ---")
                    for i, r in enumerate(roots, 1): print(f"  [{i}] {Path(r).resolve()}")
                    
                    root_sel = input("\nSelect root number: ").strip()
                    if not (root_sel.isdigit() and 1 <= int(root_sel) <= len(roots)):
                        print("❌ Invalid root.")
                        continue
                        
                    selected_root = Path(roots[int(root_sel)-1]).resolve()
                    
                    print("\n(Optional) Enter a relative sub-folder (e.g., 'Summer'). Leave blank for root.")
                    rel_path = input("Sub-folder: ").strip()
                    
                    safe_col_name = re.sub(r'[<>:"/\\|?*]', '', col_data['name']).strip()
                    target_path = selected_root / rel_path / safe_col_name if rel_path else selected_root / safe_col_name
                        
                    print(f"\nTarget Path will be: {target_path}")
                    if input("Proceed? (y/n): ").strip().lower() == 'y':
                        mode = 'STATIC' if choice == '1' else 'MANAGED'
                        self.jobs.submit(
                            job_type=JobType.UPDATE_EXPORT,
                            description=f"Export: {col_data['name']}",
                            target_func=self.exports.create_export,
                            collection_id=col_data['collection_id'],
                            target_path=str(target_path),
                            mode=mode
                        )
                        print(f"\n✅ {mode} export job queued!")
                        
            elif choice == '3':
                managed = self.exports.get_managed_exports()
                if not managed:
                    print("\n❌ No managed exports found.")
                    continue
                    
                print("\n--- Update Managed Exports ---")
                print("  [0] Update ALL Managed Exports")
                for i, exp in enumerate(managed, 1):
                    print(f"  [{i}] {exp['collection_name']} -> {exp['target_path']}")
                print("  [c] Cancel")
                
                upd_sel = input("\nSelect export to update: ").strip().lower()
                if upd_sel == 'c': continue
                elif upd_sel == '0':
                    for exp in managed:
                        self.jobs.submit(
                            job_type=JobType.UPDATE_EXPORT,
                            description=f"Update: {exp['collection_name']}",
                            target_func=self.exports.update_managed_export,
                            export_id=exp['export_id']
                        )
                    print("\n✅ Update jobs queued for all exports!")
                elif upd_sel.isdigit() and 1 <= int(upd_sel) <= len(managed):
                    exp = managed[int(upd_sel)-1]
                    self.jobs.submit(
                        job_type=JobType.UPDATE_EXPORT,
                        description=f"Update: {exp['collection_name']}",
                        target_func=self.exports.update_managed_export,
                        export_id=exp['export_id']
                    )
                    print("\n✅ Update job queued!")

            elif choice == '4':
                managed = self.exports.get_managed_exports()
                if not managed:
                    print("\n❌ No managed exports found.")
                    continue

                print("\n--- Delete Managed Export ---")
                for i, exp in enumerate(managed, 1):
                    print(f"  [{i}] {exp['collection_name']} -> {exp['target_path']}")
                print("  [c] Cancel")

                del_sel = input("\nSelect export to delete: ").strip().lower()
                if del_sel.isdigit() and 1 <= int(del_sel) <= len(managed):
                    selected_exp = managed[int(del_sel)-1]
                    print(f"\nExport: {selected_exp['collection_name']}")
                    print("  [1] Stop Managing (Removes tracking, KEEPS folder & mp3s)")
                    print("  [2] Delete Entirely (Removes tracking, DELETES folder & mp3s)")
                    print("  [c] Cancel")
                    
                    action_sel = self._get_keypress("Select action: ")
                    if action_sel == '1':
                        self.exports.delete_managed_export(selected_exp['export_id'], delete_folder=False)
                        print("\n✅ Export unmanaged. Files kept.")
                    elif action_sel == '2':
                        self.exports.delete_managed_export(selected_exp['export_id'], delete_folder=True)
                        print("\n✅ Export and folder deleted.")
                    
            elif choice == '5':
                self.jobs.submit(
                    job_type=JobType.UPDATE_EXPORT,
                    description="Scan & Recover Lost Exports",
                    target_func=self.exports.scan_and_recover
                )
                print("\n✅ Recovery scan queued!")

    def _handle_jobs(self):
        while True:
            jobs = self.jobs.get_all_jobs()
            print("\n--- Background Jobs Dashboard ---")
            if not jobs:
                print("  (No active or historical jobs.)")
            else:
                for i, j in enumerate(jobs, 1):
                    # Calculate percentage for a cleaner display
                    percent = (j.progress_current / j.progress_total * 100) if j.progress_total > 0 else 0
                    status_str = f"[{j.status.name}]"
                    
                    # Highlight errors or pauses
                    alert = ""
                    if j.status.name == "FAILED": alert = " ❌ FAILED"
                    elif j.status.name == "PAUSED": alert = " ⏸️ PAUSED"
                    
                    print(f"  [{i}] {status_str} {j.description} ({percent:.1f}%){alert}")

            print("\n  [#] Enter Job Number for Details & Actions")
            print("  [x] Clear All Finished Jobs")
            print("  [r] Refresh Dashboard")
            print("  [b] Back to Main Menu")
            
            choice = input("\nSelect option: ").strip().lower()
            
            if choice == 'b': break
            elif choice == 'r': continue
            elif choice == 'x':
                self.jobs.cleanup_finished_jobs()
                print("✅ Cleared finished jobs.")
            elif choice.isdigit() and 1 <= int(choice) <= len(jobs):
                self._job_details_menu(jobs[int(choice)-1].id)
            else:
                print("❌ Invalid selection.")

    def _job_details_menu(self, job_id: str):
        """Interactive drill-down to test the new JobManager lifecycle controls."""
        while True:
            job = self.jobs.get_job(job_id)
            if not job:
                print("\n❌ Job no longer exists (it may have been removed).")
                break

            print(f"\n--- Job Details: {job.description} ---")
            print(f"  ID:       {job.id}")
            print(f"  Status:   {job.status.name}")
            print(f"  Type:     {job.job_type}")
            print(f"  Progress: {job.progress_current} / {job.progress_total}")
            
            if job.current_item: 
                print(f"  Item:     {job.current_item}")
            if job.progress_message: 
                print(f"  Message:  {job.progress_message}")
            if job.error_message: 
                print(f"  Error:    {job.error_message}")
                
            print(f"  Duration: {job.duration:.1f}s")

            # Dynamically fetch which actions are valid right now
            actions = self.jobs.get_available_actions(job_id)
            action_map = {}
            
            print("\n  Available Actions:")
            if not actions:
                print("  (No actions available)")
                
            if "Pause" in actions:
                print("  [p] Pause Job")
                action_map['p'] = self.jobs.pause
            if "Resume" in actions:
                print("  [r] Resume Job")
                action_map['r'] = self.jobs.resume
            if "Cancel" in actions:
                print("  [c] Cancel Job")
                action_map['c'] = self.jobs.cancel
            if "Retry" in actions:
                print("  [t] Retry Job (Creates a new identical job)")
                action_map['t'] = self.jobs.retry
            if "Remove" in actions:
                print("  [x] Remove Job from History")
                action_map['x'] = self.jobs.remove_job

            print("  [b] Back to Dashboard")

            act = self._get_keypress("\nSelect action: ")
            
            if act == 'b': 
                break
            elif act in action_map:
                try:
                    result = action_map[act](job_id)
                    print(f"\n✅ Action executed successfully.")
                    if act == 't':
                        print(f"  -> New Retried Job ID: {result}")
                    if act == 'x':
                        break # Job is removed, exit back to dashboard
                except Exception as e:
                    print(f"\n❌ Action failed: {e}")
            else:
                # If they pressed a key that isn't valid right now, do nothing and loop
                pass

    def _view_tracks_drilldown(self, col_name, tracks, col_id=None):
        """Helper method to view tracks and provide deletion/removal options."""
        while True:
            print(f"\n--- {col_name} ---")
            if not tracks: 
                print("  (Empty)")
            for i, t in enumerate(tracks, 1):
                archived_tag = "[ARCHIVED] " if t.get('membership_status') == 'ARCHIVED' else ""
                status = t.get('song_status', t.get('status', 'UNKNOWN'))
                print(f"  [{i}] {archived_tag}{t['title']} - {t['artist']} ({status})")
            print("  [b] Back")
            
            t_sel = input("\nSelect track for details: ").strip()
            if t_sel == 'b': break
            if t_sel.isdigit() and 1 <= int(t_sel) <= len(tracks):
                song_id = tracks[int(t_sel)-1]['song_id']
                details = self.collections.get_track_details(song_id)
                
                print("\n--- Track Details ---")
                for k, v in details.items(): 
                    if k not in ['acquisition_info'] and v is not None:
                        print(f"  {str(k).capitalize()}: {v}")
                if details.get('collections'):
                    print(f"  Collections: {', '.join(details['collections'])}")
                    
                # The Deletion Action Menu
                print("\nActions:")
                if col_id:
                    print("  [r] Remove from this Collection")
                print("  [x] Delete from Master Database entirely")
                print("  [Enter] Go Back")
                
                action = self._get_keypress("Select action: ")
                if action == 'r' and col_id:
                    self.collections.remove_item_from_collection(col_id, song_id)
                    print("✅ Removed from collection.")
                    tracks = self.collections.get_collection_tracks(col_id) # Refresh view
                elif action == 'x':
                    confirm = input("Delete from entire database? (y/n): ").strip().lower()
                    if confirm == 'y':
                        self.collections.delete_standalone_item(song_id)
                        print("✅ Deleted from database.")
                        # Refresh view dynamically
                        if col_id: tracks = self.collections.get_collection_tracks(col_id)
                        else: tracks = self.collections.get_all_media_items()

    def _handle_collections(self):
        # Helper function for background imports
        def _import_job(plugin, url, job_context=None):
            if job_context: 
                job_context.update_current_item("Fetching playlist data from source...")
            coll = plugin.fetch_collection(url)
            if coll and not (job_context and job_context.is_cancelled):
                self.collections.save_collection(coll, job_context=job_context)

        while True:
            print("\n--- Manage Collections & Media ---")
            print("  [1] View Collections & Tracks")
            print("  [2] Add Collection")
            print("  [3] Add Existing Song to a Collection")
            print("  [4] Add Standalone Song to Database")
            print("  [5] Archive Collection")
            print("  [b] Back to Main Menu")
            
            choice = self._get_keypress("Select option: ")
            if choice == 'b': break
            
            elif choice == '1':
                while True:
                    cols = self.collections.get_all_collections()
                    print("\n--- View Collections ---")
                    print("  [0] Full Master Database (All Songs)")
                    for i, c in enumerate(cols, 1):
                        print(f"  [{i}] {c['name']} ({c['track_count']} tracks)")
                    print("  [b] Back")
                    
                    c_sel = input("\nSelect collection: ").strip()
                    if c_sel == 'b': break
                    
                    if c_sel == '0':
                        tracks = self.collections.get_all_media_items()
                        col_name = "Master Database"
                        col_id = None
                    elif c_sel.isdigit() and 1 <= int(c_sel) <= len(cols):
                        col_id = cols[int(c_sel)-1]['collection_id']
                        col_name = cols[int(c_sel)-1]['name']
                        tracks = self.collections.get_collection_tracks(col_id)
                    else: continue
                    
                    # Pass the col_id into the drilldown
                    self._view_tracks_drilldown(col_name, tracks, col_id)

            elif choice == '2':
                print("\n  [1] Spotify Playlist | [2] YouTube Playlist | [3] Custom")
                src = self._get_keypress("Select source: ")
                if src == '1':
                    url = input("\nEnter Spotify URL: ").strip()
                    self.jobs.submit(
                        job_type=JobType.REFRESH_COLLECTION,
                        description="Import Spotify Collection",
                        target_func=_import_job,
                        plugin=self.spotify,
                        url=url
                    )
                    print("✅ Import queued in background.")
                elif src == '2':
                    url = input("\nEnter YouTube Playlist URL: ").strip()
                    self.jobs.submit(
                        job_type=JobType.REFRESH_COLLECTION,
                        description="Import YouTube Collection",
                        target_func=_import_job,
                        plugin=self.youtube_plugin,
                        url=url
                    )
                    print("✅ Import queued in background.")
                elif src == '3':
                    name = input("\nEnter Collection Name: ").strip()
                    self.collections.create_custom_collection(name)
                    print(f"✅ Created custom collection: '{name}'")

            elif choice == '3':
                songs = self.collections.get_all_media_items()
                if not songs:
                    print("❌ No songs in database.")
                    continue
                print("\n--- Select Song ---")
                for i, s in enumerate(songs, 1):
                    print(f"  [{i}] {s['title']} - {s['artist']}")
                s_sel = input("\nSelect song number: ").strip()
                
                if s_sel.isdigit() and 1 <= int(s_sel) <= len(songs):
                    song_id = songs[int(s_sel)-1]['song_id']
                    cols = self.collections.get_all_collections()
                    print("\n--- Select Collection ---")
                    for i, c in enumerate(cols, 1):
                        print(f"  [{i}] {c['name']}")
                    c_sel = input("\nSelect collection number: ").strip()
                    if c_sel.isdigit() and 1 <= int(c_sel) <= len(cols):
                        col_id = cols[int(c_sel)-1]['collection_id']
                        self.collections.add_item_to_collection(col_id, song_id)
                        print("✅ Song added to collection.")

            elif choice == '4':
                print("\n  [1] Spotify URL | [2] YouTube URL | [3] Manual Entry")
                src = self._get_keypress("Select source: ")
                if src == '1':
                    item = self.spotify.fetch_item(input("\nEnter Spotify URL: ").strip())
                    if item: self.collections.save_media_item(item)
                elif src == '2':
                    item = self.youtube_plugin.fetch_item(input("\nEnter YouTube Video URL: ").strip())
                    if item: self.collections.save_media_item(item)
                elif src == '3':
                    self.collections.add_standalone_item(
                        input("\nTitle: ").strip(), 
                        input("Artist: ").strip(), 
                        input("Media Type [music]: ").strip() or 'music', 
                        input("Direct URL (optional): ").strip() or None
                    )

            elif choice == '5':
                cols = self.collections.get_all_collections()
                print("\n--- Archive Collection ---")
                for i, c in enumerate(cols, 1):
                    print(f"  [{i}] {c['name']}")
                c_sel = input("\nSelect collection to archive: ").strip()
                if c_sel.isdigit() and 1 <= int(c_sel) <= len(cols):
                    col_name = cols[int(c_sel)-1]['name']
                    col_id = cols[int(c_sel)-1]['collection_id']
                    confirm = input(f"Are you sure you want to archive '{col_name}'? (y/n): ").strip().lower()
                    if confirm == 'y':
                        self.collections.delete_collection(col_id)
                        print("✅ Collection archived.")

    def _handle_settings(self):
        """Dynamic settings menu powered entirely by the SettingsManager."""
        while True:
            print("\n--- System Settings ---")
            categories = self.settings.get_categories()
            for i, cat in enumerate(categories, 1):
                print(f"  [{i}] {cat.capitalize()}")
            print("  [p] Manage Matching Penalties")
            print("  [b] Back to Main Menu")
            
            choice = self._get_keypress("Select option: ")
            if choice == 'b': break
            elif choice == 'p':
                self._handle_penalties()
            elif choice.isdigit() and 1 <= int(choice) <= len(categories):
                cat = categories[int(choice)-1]
                self._edit_category(cat)

    def _edit_category(self, category: str):
        """Dynamically builds an edit form based on the schema."""
        while True:
            print(f"\n--- Settings: {category.capitalize()} ---")
            entries = list(self.settings.iter_schema_entries(category))
            for i, (label, path, meta) in enumerate(entries, 1):
                current_val = self.settings.get(path, meta.get("default"))
                if meta.get("type") == "list":
                    current_val = ", ".join(str(item) for item in (current_val or []))
                elif meta.get("type") == "choice":
                    current_val = self._choice_label(meta, current_val)
                print(f"  [{i}] {label}: {current_val}")
            print("  [r] Reset Category to Defaults")
            print("  [b] Back")

            choice = input("\nSelect setting to edit: ").strip().lower()
            if choice == 'b': break
            elif choice == 'r':
                self.settings.reset(category)
                self.settings.save()
                print(f"✅ {category.capitalize()} reset to defaults.")
            elif choice.isdigit() and 1 <= int(choice) <= len(entries):
                label, path, meta = entries[int(choice)-1]
                current_val = self.settings.get(path, meta.get("default"))
                print(f"\nEditing: {label}")
                print(f"Description: {meta.get('desc', 'No description')}")
                print(f"Type: {meta.get('type')} (Current: {current_val})")
                
                if meta.get("type") == "choice":
                    options = meta.get("options", [])
                    for opt_idx, option in enumerate(options, 1):
                        option_value = option.get("value") if isinstance(option, dict) else option
                        option_label = option.get("label", str(option_value)) if isinstance(option, dict) else str(option_value)
                        print(f"  [{opt_idx}] {option_label}")
                    selected = input("Select option number (or blank to cancel): ").strip()
                    if not selected:
                        continue
                    if not selected.isdigit() or not (1 <= int(selected) <= len(options)):
                        print("Invalid option.")
                        continue
                    selected_option = options[int(selected) - 1]
                    new_val = selected_option.get("value") if isinstance(selected_option, dict) else selected_option
                else:
                    new_val = input("Enter new value (or blank to cancel): ").strip()
                if new_val:
                    try:
                        if meta.get("type") == "list":
                            new_val = [item.strip() for item in new_val.split(",") if item.strip()]
                        self.settings.set(path, new_val)
                        self.settings.save()
                        print("✅ Setting updated.")
                    except ValueError as e:
                        print(f"❌ Invalid input: {e}")

    def _choice_label(self, meta: dict, value):
        for option in meta.get("options", []):
            option_value = option.get("value") if isinstance(option, dict) else option
            if str(option_value) == str(value):
                return option.get("label", str(option_value)) if isinstance(option, dict) else str(option_value)
        return value

    def _handle_penalties(self):
        """CRUD interface for the dynamic penalty system."""
        while True:
            print("\n--- Matching Penalties ---")
            penalties = self.settings.get_penalties()
            if not penalties:
                print("  (No penalties configured)")
            else:
                for k, v in penalties.items():
                    print(f"  - {k}: {v}")
                    
            print("\n  [a] Add/Update Penalty")
            print("  [d] Delete Penalty")
            print("  [b] Back")
            
            choice = self._get_keypress("Select option: ")
            if choice == 'b': break
            elif choice == 'a':
                keyword = input("\nEnter keyword (e.g., 'karaoke'): ").strip().lower()
                if keyword:
                    val = input(f"Enter penalty value for '{keyword}' (e.g., 0.2): ").strip()
                    try:
                        self.settings.add_penalty(keyword, val)
                        self.settings.save()
                        print(f"✅ Penalty '{keyword}' set to {val}.")
                    except ValueError:
                        print("❌ Invalid number format.")
            elif choice == 'd':
                keyword = input("\nEnter keyword to remove: ").strip().lower()
                self.settings.remove_penalty(keyword)
                self.settings.save()
                print(f"✅ Penalty '{keyword}' removed (if it existed).")
