"""Build the bundled browser assets using esbuild 0.25.12."""
import argparse
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
# Keep the current build in its own directory so legacy bundles remain usable
# by open pages while the new assets are generated.
DIST = WEB / 'dist' / 'current'
MAPS = ROOT / 'frontend-maps' / 'current'
VENDOR = ROOT / 'frontend' / 'three'
VERSIONED = r'(?:[a-z0-9-]+-[A-Z0-9]{8}\.(?:js|css))(?:\.gz|\.map)?'


def build(esbuild: Path) -> None:
    html = (WEB / 'index.html').read_text('utf8')
    styles = re.findall(r'<link rel="stylesheet" href="/([^"/]+\.css)">', html)
    scripts = re.findall(r'<script type="module" src="/([^"/]+\.js)"></script>', html)
    if not styles or not scripts:
        raise ValueError('找不到页面的样式或脚本入口')
    with tempfile.TemporaryDirectory(prefix='.frontend-', dir=ROOT) as temporary:
        stage = Path(temporary)
        config = stage / 'tsconfig.json'
        config.write_text(json.dumps({'compilerOptions': {
            'baseUrl': '..',
            'paths': {'three': ['frontend/three/build/three.module.js'],
                      'three/addons/*': ['frontend/three/examples/jsm/*']},
        }}), encoding='utf8')
        css = stage / 'studio.css'
        css.write_text(''.join(f'@import "../web/{name}";\n' for name in styles), encoding='utf8')
        output = stage / 'assets'
        common = [
            str(esbuild), '--bundle', '--minify', '--sourcemap=external', '--legal-comments=eof',
            '--entry-names=[name]-[hash]', f'--outdir={output}', '--log-level=warning',
        ]
        subprocess.run([*common, '--target=es2022', '--format=esm', '--splitting', '--chunk-names=chunk-[hash]',
                        f'--tsconfig={config}', *(str(WEB / name) for name in scripts)], cwd=ROOT, check=True)
        subprocess.run([*common, '--target=esnext', str(css)], cwd=ROOT, check=True)
        stylesheet = next(output.glob('studio-*.css')).name
        for i, name in enumerate(styles):
            tag = f'<link rel="stylesheet" href="/{name}">'
            html = html.replace(tag, f'<link rel="stylesheet" href="/dist/current/{stylesheet}">' if i == 0 else '')
        for name in scripts:
            compiled = next(output.glob(f'{Path(name).stem}-*.js')).name
            html = html.replace(f'src="/{name}"', f'src="/dist/current/{compiled}"')
        html = re.sub(r'(?m)^\s*<script type="importmap">[^\n]*</script>\n', '\n', html)
        (output / 'index.html').write_text(html, encoding='utf8', newline='\n')
        (output / 'three.LICENSE.txt').write_bytes((VENDOR / 'LICENSE').read_bytes())
        for file in output.iterdir():
            if file.suffix in ('.js', '.css', '.html'):
                file.with_suffix(file.suffix + '.gz').write_bytes(gzip.compress(file.read_bytes(), mtime=0))
        if DIST.resolve() != DIST or MAPS.resolve() != MAPS:
            raise ValueError('构建输出目录不能是链接')
        DIST.mkdir(parents=True, exist_ok=True)
        MAPS.mkdir(parents=True, exist_ok=True)
        files = list(output.iterdir())
        names = {file.name for file in files}
        for file in sorted(files, key=lambda item: item.name == 'index.html'):
            target = (MAPS if file.suffix == '.map' else DIST) / file.name
            if target.exists() and re.fullmatch(VERSIONED, file.name):
                continue
            os.replace(file, target)
        for folder in (DIST, MAPS):
            for file in folder.iterdir():
                if file.is_file() and re.fullmatch(VERSIONED, file.name) and file.name not in names:
                    try:
                        file.unlink()
                    except PermissionError:
                        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('esbuild', type=Path)
    build(parser.parse_args().esbuild.resolve(strict=True))
