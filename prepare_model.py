"""Extract the supplied archive without changing it; create an ASCII runtime copy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parent
MAX_RUNTIME_TEXTURE_SIZE = 4096


def _mojibake_score(name: str) -> int:
    """Count characters strongly associated with CP437-decoded legacy names."""
    return sum(1 for char in name
               if '\u2500' <= char <= '\u259f' or '\ue000' <= char <= '\uf8ff')


def zip_metadata_encoding(archive: Path):
    """Choose a legacy encoding only when it clearly improves unflagged names."""
    with zipfile.ZipFile(archive) as source:
        names = [item.filename for item in source.infolist()
                 if not item.flag_bits & 0x800]
    if not names or not any(_mojibake_score(name) for name in names):
        return None
    original_score = sum(map(_mojibake_score, names))
    for encoding in ('gbk', 'cp932'):
        try:
            decoded = [name.encode('cp437').decode(encoding) for name in names]
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if sum(map(_mojibake_score, decoded)) < original_score:
            return encoding
    return None


def copy_runtime_texture(source: Path, destination: Path,
                         max_size: int = MAX_RUNTIME_TEXTURE_SIZE) -> dict:
    """Copy a texture, shrinking oversized images only in the runtime copy."""
    with Image.open(source) as image:
        source_size = image.size
        longest = max(source_size)
        if longest <= max_size:
            shutil.copy2(source, destination)
            runtime_size = source_size
            resized = False
        else:
            ratio = max_size / longest
            runtime_size = tuple(max(1, round(value * ratio)) for value in source_size)
            image.convert('RGBA').resize(runtime_size, Image.Resampling.LANCZOS).save(
                destination, format='PNG', optimize=True)
            resized = True
    return {'source_size': list(source_size), 'runtime_size': list(runtime_size),
            'resized': resized}


def prepare(archive: Path) -> Path:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = ROOT / 'models' / digest[:12]
    original = target / 'original'
    runtime = target / 'runtime'
    marker = target / 'manifest.json'
    if marker.exists():
        return runtime / 'model.model3.json'
    original.mkdir(parents=True, exist_ok=True)
    encoding = zip_metadata_encoding(archive)
    options = {'metadata_encoding': encoding} if encoding else {}
    with zipfile.ZipFile(archive, **options) as z:
        for entry in z.infolist():
            path = (original / entry.filename).resolve()
            if not path.is_relative_to(original.resolve()):
                raise ValueError('Unsafe ZIP path')
            z.extract(entry, original)
    candidates = list(original.rglob('*.model3.json'))
    if len(candidates) != 1:
        raise ValueError('Expected one model3.json')
    source = candidates[0]
    config = json.loads(source.read_text(encoding='utf-8-sig'))
    runtime.mkdir(exist_ok=True)
    refs = config['FileReferences']
    copied = []

    def source_path(relative):
        src = (source.parent / relative.replace('\\', '/')).resolve()
        if not src.is_relative_to(original.resolve()):
            raise ValueError('Reference outside model directory')
        return src

    def copy(relative, name):
        src = source_path(relative)
        shutil.copy2(src, runtime / name)
        copied.append({'source': str(src.relative_to(original)), 'runtime': name})
        return name

    for key, ext in [('Moc', 'moc3'), ('Physics', 'physics3.json'), ('DisplayInfo', 'cdi3.json'), ('Pose', 'pose3.json')]:
        if key in refs:
            refs[key] = copy(refs[key], 'model.' + ext)
    texture_names = []
    for i, relative in enumerate(refs['Textures']):
        name = f'texture_{i:02}.png'
        src = source_path(relative)
        details = copy_runtime_texture(src, runtime / name)
        copied.append({'source': str(src.relative_to(original)), 'runtime': name, **details})
        texture_names.append(name)
    refs['Textures'] = texture_names
    # Keep unregistered assets separate: use native LoadExtra* at runtime.
    expressions = []
    motions = []
    for i, p in enumerate(sorted(source.parent.rglob('*.exp3.json'))):
        name = copy(str(p.relative_to(source.parent)), f'expression_{i:02}.exp3.json')
        expressions.append({'name': p.name.removesuffix('.exp3.json'), 'file': name,
                            'source': p.relative_to(source.parent).as_posix()})
    for i, p in enumerate(sorted(source.parent.rglob('*.motion3.json'))):
        name = copy(str(p.relative_to(source.parent)), f'motion_{i:02}.motion3.json')
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        motions.append({'name': p.name.removesuffix('.motion3.json'), 'file': name,
                        'source': p.relative_to(source.parent).as_posix(),
                        'duration': data['Meta']['Duration']})
    (runtime / 'model.model3.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    (runtime / 'assets.json').write_text(json.dumps({'expressions': expressions, 'motions': motions}, ensure_ascii=False, indent=2), encoding='utf-8')
    marker.write_text(json.dumps({'zip_sha256': digest, 'files': copied}, ensure_ascii=False, indent=2), encoding='utf-8')
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    return runtime / 'model.model3.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    print(prepare(parser.parse_args().archive))
