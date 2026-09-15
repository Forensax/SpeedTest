"""Render the project's simple bar mark to a Windows ICO."""

from pathlib import Path

from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[1]
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=48, fill="#2563eb")
    for x, top in ((55, 143), (109, 105), (163, 61)):
        draw.rounded_rectangle((x, top, x + 37, 195), radius=9, fill="white")
    path = root / "assets" / "speedtest.ico"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(path)


if __name__ == "__main__":
    main()
