"""Convert the three reviewed local RabbitMQ SVGs to PNG; never upload or edit articles.

Requires rsvg-convert or the frontend's installed sharp, and backend Pillow.
Only use reviewed repository assets, never untrusted SVGs supplied by an API.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape

from PIL import Image, ImageFont


ASSETS = {
    "upgrade-path": "RabbitMQ 버전 업그레이드 경로",
    "message-flow": "RabbitMQ 메시지 전달 흐름",
    "incident-summary": "RabbitMQ 장애 원인과 조치 요약",
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "docs/autolog-assets/2026-10-21-rabbitmq")
    parser.add_argument("--output", type=Path, default=ROOT / ".deploy/rabbitmq-images")
    parser.add_argument("--font-file", type=Path, required=True, help="Reviewed Korean font, e.g. NotoSansCJK-Regular.ttc")
    args = parser.parse_args()
    missing = [name + ".svg" for name in ASSETS if not (args.source / (name + ".svg")).is_file()]
    if missing:
        parser.error("Missing reviewed source assets: " + ", ".join(missing))
    font = ImageFont.truetype(str(args.font_file), 24)
    missing_glyph = bytes(font.getmask("\U0010ffff"))
    if any(bytes(font.getmask(char)) == missing_glyph for char in "가나다한글"):
        parser.error("Selected font lacks Korean glyphs; refusing unreadable PNG output")
    renderer = shutil.which("rsvg-convert")
    node = shutil.which("node")
    if not renderer and not node:
        parser.error("Install librsvg2-bin or frontend dependencies (sharp) in your development environment")
    if args.output.exists():
        parser.error("Output already exists; select a new directory to preserve prior migration artifacts")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    # Build all files before publishing the local artifact directory.
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        fonts = Path(temporary) / "fonts"
        fonts.mkdir()
        shutil.copyfile(args.font_file, fonts / ("korean" + args.font_file.suffix))
        fontconfig = Path(temporary) / "fonts.conf"
        fontconfig.write_text('<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
                              '<dir>' + escape(str(fonts.resolve())) + '</dir></fontconfig>')
        renderer_env = {**os.environ, "FONTCONFIG_FILE": str(fontconfig.resolve())}
        staging = Path(temporary) / "images"
        staging.mkdir()
        for name, alt in ASSETS.items():
            source = args.source / (name + ".svg")
            output = staging / (name + ".png")
            if renderer:
                command = [renderer, "--format", "png", "--output", str(output), str(source)]
            else:
                # Reuse Next.js' installed native renderer; no network/package install.
                command = [node, "-e", "require('sharp')(process.argv[1], {limitInputPixels:40000000})"
                           ".png().toFile(process.argv[2]).catch(()=>process.exit(1))",
                           str(source.resolve()), str(output.resolve())]
            subprocess.run(command, cwd=ROOT / "frontend", check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=renderer_env)
            if output.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Converted image exceeds 10 MiB")
            with Image.open(output) as image:
                if image.format != "PNG" or image.width * image.height > 40_000_000:
                    raise ValueError("Converted image is invalid or exceeds the pixel budget")
                image.verify()
            with Image.open(output) as image:
                image.load()
            manifest.append({"source": name + ".svg", "file": name + ".png", "alt": alt,
                             "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                             "bytes": output.stat().st_size})
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        staging.rename(args.output)
    print(f"Prepared {len(manifest)} PNGs and manifest.json in {args.output}. No upload or article write performed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit("Local image conversion failed; no upload or article write performed.") from None
