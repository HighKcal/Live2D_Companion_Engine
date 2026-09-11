"""Print structural inventory for a prepared Live2D runtime without guessing semantics."""
import argparse
import json
from pathlib import Path
import sys


def inspect(model_path):
    model_path = Path(model_path).resolve()
    runtime = model_path.parent
    model = json.loads(model_path.read_text(encoding='utf-8-sig'))
    references = model.get('FileReferences', {})
    assets_path = runtime / 'assets.json'
    assets = json.loads(assets_path.read_text(encoding='utf-8')) if assets_path.exists() else {}
    display_path = references.get('DisplayInfo')
    display = {}
    if display_path and (runtime / display_path).exists():
        display = json.loads((runtime / display_path).read_text(encoding='utf-8-sig'))
    return {
        'model3': str(model_path),
        'moc': references.get('Moc'),
        'textures': references.get('Textures', []),
        'physics': references.get('Physics'),
        'pose': references.get('Pose'),
        'hit_areas': model.get('HitAreas', []),
        'groups': model.get('Groups', []),
        'expressions': [
            {'source': item.get('source'), 'runtime_file': item.get('file'), 'name': item.get('name')}
            for item in assets.get('expressions', [])
        ],
        'motions': [
            {'source': item.get('source'), 'runtime_file': item.get('file'),
             'name': item.get('name'), 'duration': item.get('duration')}
            for item in assets.get('motions', [])
        ],
        'display_parameters': display.get('Parameters', []),
        'display_parts': display.get('Parts', []),
        'semantic_review_required': [
            'expression meanings', 'positive/negative/ambient choices',
            'sleep motion choice', 'petting hit area', 'special parameter effects'
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(inspect(args.model), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result, encoding='utf-8')
    else:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(result)


if __name__ == '__main__':
    main()
