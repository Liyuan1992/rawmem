"""Render the verified synthetic interoperability transcript as a GIF."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 960, 540
BACKGROUND = "#071018"
PANEL = "#101c27"
TEXT = "#d8e6f3"
MUTED = "#7f93a6"
GREEN = "#45d483"
BLUE = "#5db7ff"
ORANGE = "#ffb454"

SCENES = [
    (
        "rawmem + DeepSeek Harness",
        [
            "Local-first, append-only evidence",
            "Official Harness MCP bridge / stdio",
            "Synthetic fixture - no private ledger data",
        ],
        3500,
        BLUE,
    ),
    (
        "Harness version used",
        ["$ dsh --version", "0.1.1-rc.2"],
        4000,
        BLUE,
    ),
    (
        "Install the released wheel",
        [
            "$ pip install \"rawmem[deepseek-harness,mcp] @",
            "  github.com/Liyuan1992/rawmem/releases/download/v0.7.0/",
            "  rawmem-0.7.0-py3-none-any.whl\"",
            "Successfully installed rawmem-0.7.0",
        ],
        5500,
        GREEN,
    ),
    (
        "Compose the Harness overlay",
        [
            "$ dsh web --patch rawmem.cordis.yml --dump-config",
            "- id: memory-rawmem",
            "  name: '@deepseek-ai/dsh-mcp-client'",
            "  serverName: rawmem",
            "  command: rawmem-mcp",
            "  scopes: read:summary",
        ],
        6000,
        BLUE,
    ),
    (
        "Query the real MCP stdio server",
        [
            "$ python examples/deepseek-harness/demo.py",
            "tools: rawmem_archives, rawmem_recent, rawmem_status",
            "read_only: true        chain_valid: true",
            "recent: A fictional API timeout was reproduced.",
            "recent: The fictional retry test passed after the fix.",
            "raw_text_exposed: false",
        ],
        8500,
        GREEN,
    ),
    (
        "Fail closed at the evidence boundary",
        [
            "projection: full  ->  scope_denied",
            "No capture, rewrite, approval, or promotion MCP tool",
            "rawmem is evidence - not approved long-term memory",
            "github.com/Liyuan1992/rawmem",
        ],
        6000,
        ORANGE,
    ),
]


def _font(size: int, *, bold: bool = False):
    names = ["CascadiaMono.ttf", "consolab.ttf" if bold else "consola.ttf", "DejaVuSansMono.ttf"]
    for name in names:
        windows = os.environ.get("WINDIR")
        candidate = Path(windows) / "Fonts" / name if windows else Path(name)
        try:
            return ImageFont.truetype(str(candidate if candidate.exists() else name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _frame(index: int, title: str, lines: list[str], accent: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((38, 32, WIDTH - 38, HEIGHT - 32), radius=18, fill=PANEL, outline="#243747", width=2)
    draw.ellipse((62, 56, 76, 70), fill="#ff5f57")
    draw.ellipse((84, 56, 98, 70), fill="#febc2e")
    draw.ellipse((106, 56, 120, 70), fill="#28c840")
    draw.text((142, 51), "DeepSeek Harness interoperability demo", font=_font(17), fill=MUTED)
    draw.text((68, 105), title, font=_font(27, bold=True), fill=accent)
    y = 160
    for source in lines:
        color = GREEN if source.startswith("$") else TEXT
        for line in textwrap.wrap(source, width=82, subsequent_indent="  ") or [""]:
            draw.text((72, y), line, font=_font(19), fill=color)
            y += 31
        y += 4
    draw.text((68, HEIGHT - 68), "Actual package + stdio output; fictional data only", font=_font(15), fill=MUTED)
    progress_left = 610
    progress_width = 270
    draw.rounded_rectangle((progress_left, HEIGHT - 65, progress_left + progress_width, HEIGHT - 55), radius=5, fill="#243747")
    fill = int(progress_width * (index + 1) / len(SCENES))
    draw.rounded_rectangle((progress_left, HEIGHT - 65, progress_left + fill, HEIGHT - 55), radius=5, fill=accent)
    return image


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output = root / "docs" / "assets" / "deepseek-harness-demo.gif"
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = [_frame(i, title, lines, accent) for i, (title, lines, _, accent) in enumerate(SCENES)]
    durations = [duration for _, _, duration, _ in SCENES]
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"rendered {output.name}: {sum(durations) / 1000:.1f}s, {len(frames)} frames")


if __name__ == "__main__":
    main()
