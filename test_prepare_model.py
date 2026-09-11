import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from prepare_model import copy_runtime_texture, zip_metadata_encoding


class ZipMetadataEncodingTests(unittest.TestCase):
    def test_runtime_texture_resize_preserves_source_and_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.png'
            runtime = Path(folder) / 'runtime.png'
            Image.new('RGBA', (8, 4), (10, 20, 30, 77)).save(source)
            before = source.read_bytes()

            details = copy_runtime_texture(source, runtime, max_size=4)

            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(details, {
                'source_size': [8, 4], 'runtime_size': [4, 2], 'resized': True})
            with Image.open(runtime) as image:
                self.assertEqual(image.size, (4, 2))
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getpixel((0, 0))[3], 77)

    def test_runtime_texture_below_limit_is_copied_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.png'
            runtime = Path(folder) / 'runtime.png'
            Image.new('RGBA', (4, 3), (1, 2, 3, 4)).save(source)

            details = copy_runtime_texture(source, runtime, max_size=4)

            self.assertEqual(runtime.read_bytes(), source.read_bytes())
            self.assertFalse(details['resized'])

    def test_detects_gbk_names_misdecoded_as_cp437(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'legacy.zip'
            placeholder = 'aa/aa.model3.json'
            expected = '椿/椿.model3.json'
            self.assertEqual(len(placeholder.encode('ascii')), len(expected.encode('gbk')))
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr(placeholder, '{}')
            archive.write_bytes(archive.read_bytes().replace(
                placeholder.encode('ascii'), expected.encode('gbk')))
            self.assertEqual(zip_metadata_encoding(archive), 'gbk')
            with zipfile.ZipFile(archive, metadata_encoding='gbk') as source:
                self.assertEqual(source.namelist(), [expected])

    def test_keeps_utf8_and_ascii_metadata_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            for filename in ('plain/model.model3.json', '椿/椿.model3.json'):
                archive = Path(folder) / (str(len(filename)) + '.zip')
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr(filename, '{}')
                self.assertIsNone(zip_metadata_encoding(archive))


if __name__ == '__main__':
    unittest.main()
