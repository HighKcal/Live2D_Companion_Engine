"""Create a source-only ZIP using an explicit allowlist. Never bundle model assets."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = ('app.py', 'prepare_model.py', 'verify_runtime.py', 'requirements.txt',
         'setup.ps1', 'run.cmd', 'README.md', '.gitignore', '.gitattributes',
         '.python-version', 'export_source.py', 'desktop_pet.py', 'pet_behavior.py',
         'model_profiles.py', 'profiles/hibana.json', 'profiles/tsubaki.json', 'profiles/icegirl.json', 'verify_pet.py',
         'test_pet_behavior.py', 'test_model_profiles.py', 'test_prepare_model.py', 'inspect_model.py', 'MODEL_PROFILES.md', 'PET_GUIDE.md',
         'probe_petting.py', 'PETTING_FIX.md', 'probe_idle.py', 'IDLE_REACTIONS.md',
         'probe_relocation.py', 'RELOCATION.md', 'probe_poke.py')


if __name__ == '__main__':
    target = ROOT / 'dist' / 'live2d-prototype-source.zip'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    print(target)
