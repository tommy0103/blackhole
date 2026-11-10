"""Race regressions for host filesystem confinement; no live clipboard access."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'clipboard', Path(__file__).resolve().parents[1] / 'scripts/clipboard.py')
clipboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clipboard)
PNG = clipboard.PNG_SIGNATURE + b'fixture'


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blackhole-clipboard-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / 'project'
        self.outside = self.base / 'outside'
        self.project.mkdir()
        self.outside.mkdir()
        previous = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)

    def assert_no_temporary_files(self):
        self.assertFalse(list(self.project.rglob('.blackhole-clipboard.*')))

    def test_destination_created_at_publication_is_never_overwritten_or_followed(self):
        real_link = os.link
        destination = self.project / 'capture.png'
        for kind in ('file', 'directory', 'symlink'):
            with self.subTest(kind=kind):
                def race(*args, **kwargs):
                    if kind == 'file':
                        destination.write_bytes(b'original')
                    elif kind == 'directory':
                        destination.mkdir()
                    else:
                        destination.symlink_to(self.outside, target_is_directory=True)
                    return real_link(*args, **kwargs)

                with patch.object(clipboard.os, 'link', side_effect=race):
                    with self.assertRaises(FileExistsError):
                        clipboard.save_image('capture.png', PNG)
                if kind == 'file':
                    self.assertEqual(destination.read_bytes(), b'original')
                self.assertEqual(list(self.outside.iterdir()), [])
                self.assert_no_temporary_files()
                if kind == 'directory':
                    destination.rmdir()
                else:
                    destination.unlink()

    def test_parent_replaced_after_open_cannot_redirect_writes(self):
        parent = self.project / 'images'
        parent.mkdir()
        pinned = self.project / 'moved-images'
        real_open = os.open

        def race(path, *args, **kwargs):
            descriptor = real_open(path, *args, **kwargs)
            if path == 'images':
                parent.rename(pinned)
                parent.symlink_to(self.outside, target_is_directory=True)
            return descriptor

        with patch.object(clipboard.os, 'open', side_effect=race):
            clipboard.save_image('images/capture.png', PNG)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual((pinned / 'capture.png').read_bytes(), PNG)
        self.assert_no_temporary_files()

    def test_symlink_inserted_during_parent_creation_is_rejected(self):
        def race(component, **kwargs):
            (self.project / component).symlink_to(self.outside, target_is_directory=True)
            raise FileExistsError(component)

        with patch.object(clipboard.os, 'mkdir', side_effect=race):
            with self.assertRaises(OSError):
                clipboard.save_image('images/new/capture.png', PNG)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assert_no_temporary_files()

    def test_replaced_temporary_symlink_is_not_followed_or_published(self):
        victim = self.outside / 'existing'
        victim.write_bytes(b'original')
        real_link = os.link

        def race(source, *args, **kwargs):
            staged = self.project / source
            staged.unlink()
            staged.symlink_to(victim)
            return real_link(source, *args, **kwargs)

        with patch.object(clipboard.os, 'link', side_effect=race):
            with self.assertRaises(ValueError):
                clipboard.save_image('capture.png', PNG)
        self.assertEqual(victim.read_bytes(), b'original')
        self.assertFalse((self.project / 'capture.png').is_symlink())
        self.assert_no_temporary_files()

    def test_temporary_name_collision_does_not_remove_existing_file(self):
        existing = self.project / '.blackhole-clipboard.fixture'
        existing.write_bytes(b'original')
        with patch.object(clipboard.secrets, 'token_hex', return_value='fixture'):
            with self.assertRaises(FileExistsError):
                clipboard.save_image('capture.png', PNG)
        self.assertEqual(existing.read_bytes(), b'original')

    def test_concurrent_captures_publish_exactly_one_complete_file(self):
        images = [PNG + b'first', PNG + b'second']

        def save(data):
            try:
                clipboard.save_image('images/capture.png', data)
                return True
            except FileExistsError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, images))
        self.assertEqual(sorted(results), [False, True])
        self.assertIn((self.project / 'images/capture.png').read_bytes(), images)
        self.assert_no_temporary_files()

    def test_invalid_image_data_is_rejected(self):
        for encoded in (b'', b'not base64!', b'bm90IGEgcG5n'):
            with self.subTest(encoded=encoded):
                with patch.object(clipboard.subprocess, 'check_output',
                                  side_effect=['script.ps1', encoded]):
                    with self.assertRaises(ValueError):
                        clipboard.capture('script.ps1')
