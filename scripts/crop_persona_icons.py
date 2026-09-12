"""Crop the right-hand (small simplified) persona icon out of each
persona-*-full.png source sheet and save it as a standalone .webp.

Layout of every source sheet (1536x1024): larger detailed icon on the LEFT,
smaller simplified icon on the RIGHT, both on a dark textured background,
with the left icon's glow sometimes bleeding past the horizontal midline.

Because of that bleed a naive "crop the right half" picks up part of the
left icon, so instead we:
  1. classify every pixel as background (sheet-corner-sampled color) or
     content,
  2. project content onto the x axis, merge runs separated by small gaps,
     and expect two column-ranges -- the LEFT icon and the RIGHT icon --
     then keep the LAST range,
  3. project rows within that column band and keep the content row-range,
  4. crop that box with a small margin so the rounded border and its glow
     are not clipped.
"""
import os

from PIL import Image

PAD = 10          # px margin kept around the detected icon bounds
THRESHOLD = 24    # per-channel diff vs background that counts as content
MERGE_GAP = 12    # gap (px) below which neighbouring content runs merge
MIN_RUN = 50      # ignore content runs narrower/shorter than this

SOURCES = {
    "attacker": "frontend/public/persona-attacker-full.png",
    "sacrificer": "frontend/public/persona-sacrificer-full.png",
    "defender": "frontend/public/persona-defender-full.png",
    "positional": "frontend/public/persona-positional-full.png",
}


def background_color(im, w, h):
    """Median of the four sheet corners -- guaranteed empty background."""
    corners = [
        im.getpixel((2, 2)),
        im.getpixel((w - 3, 2)),
        im.getpixel((2, h - 3)),
        im.getpixel((w - 3, h - 3)),
    ]
    chans = list(zip(*corners))
    return tuple(sorted(c)[len(c) // 2] for c in chans)


def is_content(px, x, y, bg):
    r, g, b = px[x, y]
    return (
        abs(r - bg[0]) > THRESHOLD
        or abs(g - bg[1]) > THRESHOLD
        or abs(b - bg[2]) > THRESHOLD
    )


def runs_from_flags(flags, merge_gap, min_run):
    """Turn a boolean sequence into merged [(start, end_exclusive), ...]."""
    runs = []
    start = None
    empty = 0
    for i, flag in enumerate(flags + [False] * (merge_gap + 1)):
        if flag:
            if start is None:
                start = i
            empty = 0
        elif start is not None:
            empty += 1
            if empty > merge_gap:
                end = i - (empty - 1)
                if end - start >= min_run:
                    runs.append((start, end))
                start = None
                empty = 0
    return runs


def crop_right_icon(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    bg = background_color(im, w, h)

    # Column projection over the whole sheet -> expect [left icon, right icon].
    col_flags = [any(is_content(px, x, y, bg) for y in range(0, h, 4)) for x in range(w)]
    col_runs = runs_from_flags(col_flags, MERGE_GAP, MIN_RUN)
    assert len(col_runs) == 2, f"{path}: expected 2 column runs, got {col_runs}"
    x0, x1 = col_runs[-1]

    # Row projection within the right icon's column band.
    row_flags = [
        any(is_content(px, x, y, bg) for x in range(x0, x1, 4)) for y in range(h)
    ]
    row_runs = runs_from_flags(row_flags, MERGE_GAP, MIN_RUN)
    assert len(row_runs) == 1, f"{path}: expected 1 row run, got {row_runs}"
    y0, y1 = row_runs[0]

    box = (
        max(0, x0 - PAD),
        max(0, y0 - PAD),
        min(w, x1 + PAD),
        min(h, y1 + PAD),
    )
    return im.crop(box), bg


def main():
    out_dir = "frontend/public"
    for name, path in sorted(SOURCES.items()):
        cropped, bg = crop_right_icon(path)
        out_path = os.path.join(out_dir, f"persona-{name}.webp")
        cropped.save(out_path, "WEBP", quality=90, method=6)
        print(
            f"{name}: {os.path.basename(path)} -> {out_path} "
            f"size={cropped.size[0]}x{cropped.size[1]} bg={bg} "
            f"bytes={os.path.getsize(out_path)}"
        )


if __name__ == "__main__":
    main()
