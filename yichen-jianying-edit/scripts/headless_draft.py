#!/usr/bin/env python3
"""Pinned Skill entrypoint for a separately checked-out Jianying Headless project."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

def project_root():
    configured = os.environ.get('JIANYING_HEADLESS_ROOT')
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise SystemExit('JIANYING_HEADLESS_ROOT must be an absolute checkout path')
        candidates = [path.resolve()]
    else:
        candidates = list(Path(__file__).resolve().parents)
    for path in candidates:
        marker = path / 'project.json'
        if marker.is_file() and not marker.is_symlink():
            try:
                identity = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if identity.get('id') == 'jianying-headless' and identity.get('schema') == 'jianying-headless-project/v1':
                return path
    raise SystemExit('Jianying Headless checkout unavailable. Clone the project with authorized GitHub access, '
                     'then set JIANYING_HEADLESS_ROOT to its absolute path. The Skill alone does not include the engine.')


PROJECT_ROOT = project_root()
BACKEND = PROJECT_ROOT / 'engine'
PINS = {
    'native_fonts.py': 'ddd7b4c1ecd55890bd645c14930f2c5f6687794691c2280daa32673e048da5e6',
    'runtime_profiles.py': '15d18c9b71e37d68c2e255bb58f75d9c469a6ac9275f7dd8c1acddc3938cb251',
    'jy14_headless.py': '3feb3efeb10173303986609a36b9e173ccd651430acbb6b0cc8e945e4ffc2a9b',
    'native_motion.py': '5d743caaa38c921779166e5663d36f72a0c3fdb130a690ac3942a7adcf62d6c2',
    'native_effects.py': 'c46b2fc9221dd613f220564b752e532f8f3753dd5595aaffc24f41d5236e4e97',
    'native_resources.py': '9bddfbb1cd688cebd69ac49f9bbf63c242522d9666a2ef7b412fe097113f68f2',
    'native_visual_effects.py': '15df7e56cc7d575a552c180e72ec712f136c618d271b2f3ad2d32dd5929e844c',
    'native-resource-catalog.json': '97af2df27463a9183fb1aa8f2ef534b37a644cb196f340fe88fdc50b456abde9',
    'native_compound.py': '93701d566a4dbdcb498fc756aeadc50d3b2f5c6c2eb789ef3b91df6c207321aa',
    'compound-blueprint.json': '9cba9435053280abf9072d5eaccb8586c841b11dac6854b32daf9cbdba76af8e',
    'native_edit.py': 'fb98b04201aa722ebb66499d96a9ed9e0b3ce36913c37e0c793b5902cf9c0dba',
    'native_export.py': '587f48f1911ec2deda42d8e98ee40f15006f790d952aac8c118d658d459fefce',
    'ui_export.py': '2d3d6a1812ef3c394e5404294dd5de17f828c3ec3e4548f604dcbff87e51186c',
    'native_export.cpp': '641ae1aa82e66e9bf00c22716bef2a321867dc4677df94c50e7cad27e9c35354',
    'headless_runtime.py': '81d75135473eb531688099b45a5a2acf922b4c385a8feaf39f0c92963b46ccca',
    'blueprint.json': '91f7eddad5bff9af23eb88b53713c180e3e3d4054edd469140cfa9aa56bc1dc9',
    'windows_portable.py': '707e5f1040ad59384f864e5e2ad41ff2c93853be8c7bd562d44f6fb632d244ca',
    'windows_export.py': 'b4f20ce94b6ca0a72d5c542bc56ce9fd23a13a09826ba71a34beb99e23474fc8',
    'ffmpeg_graph.py': 'bc0d897993f9e7c2c5f6c0233a3002c07e235323da20fe0eca2755f1410aa594',
    'ffmpeg_tools.py': 'c8d0817c57573e0e755f3277466fa13e08bdc588d90471344faaf30438e9992f',
}

for name, expected in PINS.items():
    path = BACKEND / name
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('Headless component changed or unavailable; reverify before updating the pin: ' + name)

sys.path.insert(0, str(BACKEND))
entrypoint = 'jy14_headless.py'
command = sys.argv[1] if len(sys.argv) > 1 else None
if command == 'edit':
    if os.name == 'nt':
        raise SystemExit('Native Jianying draft editing is macOS-only')
    entrypoint = 'native_edit.py'
    del sys.argv[1]
elif command == 'export':
    requested = None
    if '--backend' in sys.argv:
        index = sys.argv.index('--backend')
        if index + 1 >= len(sys.argv):
            raise SystemExit('--backend needs a value')
        requested = sys.argv[index + 1]
        del sys.argv[index:index + 2]
    if requested not in {None, 'native', 'windows-ffmpeg'}:
        raise SystemExit('Unsupported export backend: ' + requested)
    entrypoint = ('windows_export.py'
                  if requested == 'windows-ffmpeg' or (requested is None and os.name == 'nt')
                  else 'native_export.py')
    del sys.argv[1]
elif command == 'ui-export':
    if os.name == 'nt':
        raise SystemExit('UI export acceptance is macOS-only')
    entrypoint = 'ui_export.py'
    del sys.argv[1]
elif os.name == 'nt':
    if command not in {None, '--help', '-h', 'doctor', 'build', 'verify-build'}:
        raise SystemExit('This command requires the macOS native backend: ' + str(command))
    entrypoint = 'windows_portable.py'
runpy.run_path(str(BACKEND / entrypoint), run_name='__main__')
