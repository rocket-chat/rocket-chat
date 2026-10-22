"""Assemble the README's animated demo (doc/assets/demo.gif) from the browser screencast the
Playwright recorder saved in ``.demo-frames`` (``pnpm run docs:demo`` runs both steps).

The screencast has a frame only when the page changed, each with its time. They are resampled to
a constant ``FPS`` so the motion is smooth and true to the recording, captioned from the
recorder's marks, and identical neighbours are merged into one longer frame, which keeps the file
light enough to load inline on GitHub. The frames are lossless on purpose: filtering JPEG noise
out of them leaves ghosts of anything that fades. Pillow is fetched on demand (``uv run --with pillow``),
not a dependency of the project.
"""

from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / ".demo-frames"
OUTPUT = ROOT / "doc" / "assets" / "demo.gif"
FPS = 20
WIDTH = 960
CAPTION_HEIGHT = 60
ORANGE = (254, 88, 37)
INK = (24, 24, 27)
HOLD_AT_END_SECONDS = 0.4


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow without sized default fonts
        return ImageFont.load_default()


def _captioned(
    shot: Image.Image, caption: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont
) -> Image.Image:
    height = round(shot.height * WIDTH / shot.width)
    resized = shot.convert("RGB").resize((WIDTH, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, height + CAPTION_HEIGHT), INK)
    canvas.paste(resized, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, height, WIDTH, height + 3), fill=ORANGE)
    box = draw.textbbox((0, 0), caption, font=font)
    draw.text(
        (
            (WIDTH - (box[2] - box[0])) / 2,
            height + 3 + (CAPTION_HEIGHT - 3 - (box[3] - box[1])) / 2 - box[1],
        ),
        caption,
        fill=(250, 250, 250),
        font=font,
    )
    return canvas


def main() -> int:
    files = sorted(FRAMES.glob("*.png"), key=lambda f: float(f.stem))
    marks_file = FRAMES / "marks.json"
    if not files or not marks_file.exists():
        print(
            f"No recording in {FRAMES}: run `pnpm --filter web docs:demo` first.", file=sys.stderr
        )
        return 1
    marks = json.loads(marks_file.read_text())
    stamps = [float(f.stem) for f in files]
    mark_times = [m["t"] for m in marks]
    start = max(stamps[0], marks[0]["t"] - 1.0)
    end = stamps[-1] + HOLD_AT_END_SECONDS
    font = _font(25)

    ticks = int((end - start) * FPS)
    rendered: list[tuple[Image.Image, int]] = []  # (image, number of ticks it stays)
    previous_key: tuple[int, str] | None = None
    cache: Image.Image | None = None
    for tick in range(ticks):
        moment = start + tick / FPS
        index = max(0, bisect.bisect_right(stamps, moment) - 1)
        at = bisect.bisect_right(mark_times, moment) - 1
        caption = marks[at]["caption"] if at >= 0 else "Rocket Chat"
        key = (index, caption)
        if key != previous_key:
            cache = _captioned(Image.open(files[index]), caption, font)
            rendered.append((cache, 1))
            previous_key = key
        else:
            image, held = rendered[-1]
            rendered[-1] = (image, held + 1)

    # One palette for the whole recording (it is one UI), drawn from frames across its length.
    sample = [rendered[i][0] for i in range(0, len(rendered), max(1, len(rendered) // 12))]
    strip = Image.new("RGB", (WIDTH, sample[0].height * len(sample)))
    for i, image in enumerate(sample):
        strip.paste(image, (0, i * image.height))
    palette = strip.quantize(colors=200, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image, _ in rendered]
    durations = [held * 1000 // FPS for _, held in rendered]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=1,
    )
    seconds = sum(durations) / 1000
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: {OUTPUT.stat().st_size // 1024} KiB, "
        f"{len(frames)} distinct frames over {seconds:.1f}s ({FPS} fps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
