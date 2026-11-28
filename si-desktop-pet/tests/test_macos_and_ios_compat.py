"""Tests for Mac mini (macOS Apple Silicon/Intel) and iOS PWA/SwiftUI compatibility."""
from __future__ import annotations

import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / 'src'))

from si_pet import native, storage, theme  # noqa: E402


class MacOSNativeIntegrationTests(unittest.TestCase):
    def test_parse_macos_audio_profiler_headphones_and_speakers(self):
        airpods_json = json.dumps({
            'SPAudioDataType': [{
                '_items': [
                    {
                        '_name': 'Mac mini Speakers',
                        'coreaudio_default_audio_output_device': 'spaudio_no',
                        'coreaudio_output_source': 'Internal Speakers',
                    },
                    {
                        '_name': 'AirPods Pro',
                        'coreaudio_default_audio_output_device': 'spaudio_yes',
                        'coreaudio_output_source': 'Bluetooth',
                    },
                ]
            }]
        })
        ff, name = native._parse_macos_audio_profiler(airpods_json)
        self.assertEqual(ff, native.ENDPOINT_FORMFACTOR_HEADPHONES)
        self.assertIn('AirPods Pro', name)
        self.assertTrue(native._classify_audio_endpoint(ff, name))

        ext_hp_json = json.dumps({
            'SPAudioDataType': [{
                '_items': [{
                    '_name': 'Mac mini Jack',
                    'coreaudio_default_audio_output_device': 'spaudio_yes',
                    'coreaudio_output_source': 'External Headphones',
                }]
            }]
        })
        ff2, name2 = native._parse_macos_audio_profiler(ext_hp_json)
        self.assertEqual(ff2, native.ENDPOINT_FORMFACTOR_HEADPHONES)
        self.assertTrue(native._classify_audio_endpoint(ff2, name2))

        spk_json = json.dumps({
            'SPAudioDataType': [{
                '_items': [{
                    '_name': 'Mac mini Speakers',
                    'coreaudio_default_audio_output_device': 'spaudio_yes',
                    'coreaudio_output_source': 'Internal Speakers',
                }]
            }]
        })
        ff3, name3 = native._parse_macos_audio_profiler(spk_json)
        self.assertIsNone(ff3)
        self.assertFalse(native._classify_audio_endpoint(ff3, name3))

    def test_macos_blocked_game_detection_non_exe_processes(self):
        with mock.patch('si_pet.native.MACOS', True), \
             mock.patch('si_pet.native._list_process_names', return_value={'finder', 'cs2', 'safari'}):
            self.assertEqual(native.running_blocked_game(force_refresh=True), 'cs2')

        with mock.patch('si_pet.native.MACOS', True), \
             mock.patch('si_pet.native._list_process_names', return_value={'finder', 'r5apex', 'dock'}):
            self.assertEqual(native.running_blocked_game(force_refresh=True), 'r5apex')

        with mock.patch('si_pet.native.MACOS', True), \
             mock.patch('si_pet.native._list_process_names', return_value={'finder', 'dock', 'terminal'}):
            self.assertIsNone(native.running_blocked_game(force_refresh=True))

    def test_macos_launch_agent_set_startup_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / 'LaunchAgents'
            native.set_startup(True, launch_agents_dir=agents_dir)
            plist_path = agents_dir / 'com.sicompanion.desktoppet.plist'
            self.assertTrue(plist_path.is_file())
            payload = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(payload['Label'], 'com.sicompanion.desktoppet')
            self.assertTrue(payload['RunAtLoad'])
            self.assertEqual(payload['ProgramArguments'][-1], '--quiet')

            native.set_startup(False, launch_agents_dir=agents_dir)
            self.assertFalse(plist_path.exists())

    def test_macos_afplay_async_play_and_stop(self):
        fake_proc = mock.MagicMock()
        fake_proc.poll.return_value = None
        with mock.patch('si_pet.native.WINDOWS', False), \
             mock.patch('si_pet.native.MACOS', True), \
             mock.patch('si_pet.native._afplay_binary', return_value='/usr/bin/afplay'), \
             mock.patch('si_pet.native.subprocess.Popen', return_value=fake_proc) as popen:
            self.assertTrue(native.has_audio_playback())
            self.assertTrue(native.play_wav_file('/tmp/test.wav', async_play=True))
            popen.assert_called_once()
            native.stop_wav_playback()
            fake_proc.terminate.assert_called_once()

    def test_macos_storage_and_theme_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            app_bin_dir = fake_home / 'Applications' / 'SiDesktopPet.app' / 'Contents' / 'MacOS'
            with mock.patch.object(storage.sys, 'platform', 'darwin'), \
                 mock.patch('si_pet.storage.app_root', return_value=app_bin_dir), \
                 mock.patch('si_pet.storage.Path.home', return_value=fake_home):
                resolved = storage.data_root()
                self.assertEqual(
                    resolved,
                    fake_home / 'Library' / 'Application Support' / 'SiDesktopPet' / 'data',
                )
                self.assertTrue(resolved.is_dir())

        fake_ok = mock.MagicMock(returncode=0, stdout='Dark\n')
        with mock.patch.object(theme.sys, 'platform', 'darwin'), \
             mock.patch('si_pet.theme.subprocess.run', return_value=fake_ok):
            self.assertTrue(theme._macos_dark_mode())

        self.assertIn('PingFang SC', theme.panel_stylesheet(False))


class IOSAndWebPWAAssetTests(unittest.TestCase):
    def test_web_pwa_manifest_sw_and_ios_meta_exist(self):
        web = REPO_ROOT / 'si-web'
        manifest = json.loads((web / 'manifest.webmanifest').read_text(encoding='utf-8'))
        self.assertEqual(manifest['display'], 'standalone')
        self.assertTrue((web / 'sw.js').is_file())
        for icon in ('apple-touch-icon.png', 'pwa-192.png', 'pwa-512.png'):
            self.assertTrue((web / 'assets' / icon).is_file(), icon)

        html = (web / 'index.html').read_text(encoding='utf-8')
        self.assertIn('apple-mobile-web-app-capable', html)
        self.assertIn('manifest.webmanifest', html)
        self.assertIn('SiDesktopPet-2.1.2-macOS-Source.zip', html)

    def test_ios_swiftui_project_and_macos_build_files_exist(self):
        ios = ROOT / 'mobile' / 'ios'
        for rel in (
            'Package.swift',
            'project.yml',
            'README.md',
            'Sources/SiCompanionCore/CompanionCore.swift',
            'Tests/SiCompanionCoreTests/CompanionCoreTests.swift',
            'SiCompanionApp/SiCompanionApp.swift',
            'SiCompanionApp/Views/ContentView.swift',
            'SiCompanionApp/Resources/happy.png',
        ):
            self.assertTrue((ios / rel).is_file(), rel)
        self.assertTrue((ROOT / 'SiDesktopPet-macOS.spec').is_file())
        self.assertTrue((ROOT / 'start_macos.command').is_file())
        self.assertTrue((ROOT / 'tools' / 'build_macos.sh').is_file())
        self.assertNotIn('\r\n', (ROOT / 'tools' / 'build_macos.sh').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
