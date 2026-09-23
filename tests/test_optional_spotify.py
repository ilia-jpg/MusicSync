import os
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infrastructure.config import ConfigManager
from plugins.spotify import SpotifySourcePlugin
from tui.app import MusicSyncApp
from tui.flows.management import ManagementFlow


class OptionalSpotifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_directory = Path.cwd()
        self.workspace = tempfile.TemporaryDirectory()
        os.chdir(self.workspace.name)

    def tearDown(self):
        os.chdir(self.original_directory)
        gc.collect()  # Release SQLite connections before removing the Windows temp directory.
        self.workspace.cleanup()

    async def test_fresh_tui_starts_and_accepts_non_spotify_sources(self):
        with patch('plugins.spotify.SpotifyOAuth') as oauth:
            app = MusicSyncApp()
            try:
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    self.assertNotIn('spotify', app.plugins)
                    self.assertTrue({'youtube', 'billboard', 'local_folder'} <= app.plugins.keys())
                    self.assertEqual(app.config.get('spotify.mode'), 'disabled')
                    await pilot.press('2', 'a')
                    await pilot.pause()
                    self.assertIn('YouTube/Billboard URL', app.management_flow.options)
                    self.assertNotIn('Spotify/YouTube/Billboard URL', app.management_flow.options)
                    flow = ManagementFlow(kind='external_collection_url', origin_screen_id='collections', origin_actions='', query='https://open.spotify.com/playlist/example')
                    with patch.object(app, 'show_status') as status, patch.object(app.job_manager, 'submit') as submit:
                        await app.commit_external_collection_url(flow)
                        self.assertIn('disabled', status.call_args.args[0])
                        await app.commit_external_media_url(flow)
                        self.assertIn('disabled', status.call_args.args[0])
                        submit.assert_not_called()
                    oauth.assert_not_called()
            finally:
                app.job_manager.shutdown()

    async def test_configuration_requires_opt_in_and_real_credentials(self):
        config = ConfigManager()
        with patch('plugins.spotify.SpotifyOAuth') as oauth:
            config.config['spotify'] = {'client_id': 'existing-id', 'client_secret': 'existing-secret'}
            with self.assertRaisesRegex(ValueError, 'disabled'):
                SpotifySourcePlugin(config)
            config.config['spotify']['mode'] = 'enabled'
            for value in ['', '   ', 'YOUR_CLIENT_SECRET_HERE']:
                config.config['spotify']['client_secret'] = value
                with self.assertRaisesRegex(ValueError, 'setup incomplete'):
                    SpotifySourcePlugin(config)
            oauth.assert_not_called()

    async def test_saved_settings_enable_then_disable_spotify(self):
        app = MusicSyncApp()
        try:
            with patch('plugins.spotify.SpotifyOAuth'), patch('plugins.spotify.spotipy.Spotify'):
                app.settings.set('spotify.client_id', 'test-client-id')
                app.settings.set('spotify.client_secret', 'test-client-secret')
                app.settings.set('spotify.mode', 'enabled')
                app.save_settings_changes()
                self.assertIn('spotify', app.plugins)
                app.settings.set('spotify.mode', 'disabled')
                app.save_settings_changes()
                self.assertNotIn('spotify', app.plugins)
                self.assertIn('youtube', app.plugins)
                self.assertIn('local_folder', app.plugins)
                self.assertEqual(ConfigManager().get('spotify.mode'), 'disabled')
        finally:
            app.job_manager.shutdown()


if __name__ == '__main__':
    unittest.main()
