"""Assemble individually rendered Blender PNGs into a portable review index."""

import csv
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/city-realism-study"
GALLERY = STUDY / "render-gallery"


def main():
    reports = json.loads((GALLERY / "coverage.json").read_text(encoding="utf-8"))
    expected_legacy = list(csv.DictReader((STUDY / "asset-inventory.csv").open(encoding="utf-8")))
    expected_study = json.loads((STUDY / "study.json").read_text(encoding="utf-8"))["assets"]
    expected = {f"legacy/{Path(row['path']).stem}" for row in expected_legacy}
    expected.update(f"study/{asset['id']}" for asset in expected_study)
    missing = expected - reports.keys()
    if missing:
        raise SystemExit(f"Missing gallery entries: {sorted(missing)}")
    font = ImageFont.load_default(size=17)
    cards = []
    for group in ("study", "legacy"):
        entries = [(key, value) for key, value in reports.items() if key.startswith(group + "/")]
        entries.sort()
        for page in range(0, len(entries), 20):
            subset = entries[page : page + 20]
            sheet = Image.new("RGB", (1600, 360 * ((len(subset) + 4) // 5)), "#172129")
            draw = ImageDraw.Draw(sheet)
            for index, (key, value) in enumerate(subset):
                if value["status"] == "failed":
                    raise SystemExit(f"Failed render: {key}")
                image_path = STUDY / value["render"]
                with Image.open(image_path) as original:
                    thumbnail = original.convert("RGB").resize((312, 312), Image.Resampling.LANCZOS)
                x, y = (index % 5) * 320, (index // 5) * 360
                sheet.paste(thumbnail, (x + 4, y + 4))
                draw.text((x + 8, y + 321), key.split("/", 1)[1], fill="#eef4f7", font=font)
                draw.text(
                    (x + 8, y + 341), f"{value['triangles']:,} tris", fill="#9bb2bf", font=font
                )
            sheet.save(GALLERY / f"contact-{group}-{page // 20 + 1:02}.jpg", quality=92)
        for key, value in entries:
            render = Path(value["render"]).relative_to("render-gallery").as_posix()
            caption = html.escape(key.split("/", 1)[1])
            cards.append(
                f'<figure data-group="{group}"><a href="{render}">'
                f'<img src="{render}" loading="lazy" alt="{caption}"></a>'
                f"<figcaption>{caption}<small>{value['triangles']:,} triangles · "
                f"{value['meshes']} meshes</small>"
                f"<small>{html.escape(value['visualization'])}</small></figcaption></figure>"
            )
    page = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Future City · Individual Asset Review</title><style>
body{margin:0;background:#121a20;color:#e8f0f4;font:16px/1.6 system-ui}
header{padding:48px max(24px,5vw);max-width:1100px}
h1{font-size:clamp(28px,4vw,52px);line-height:1.12;margin:0 0 20px}
p{color:#b4c7d1}nav{display:flex;gap:8px}
button{font:inherit;border:1px solid #56717d;background:#21343e;color:inherit;
border-radius:8px;padding:8px 18px;cursor:pointer}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
gap:20px;padding:0 max(24px,5vw) 48px}
figure{margin:0;background:#1e2a32;border-radius:10px;overflow:hidden}
img{display:block;width:100%;aspect-ratio:1}figcaption{padding:14px}
small{display:block;color:#aac0cd}a{color:#79d8e5}[hidden]{display:none!important}
</style><header><h1>Future City<br>Individual Asset Review</h1>
<p>Every catalog entry is rendered separately from its GLB in Blender EEVEE.
New city models are authored study assets. Legacy images document the existing
inventory; they are not redesigned replacements. Animation-only GLBs display
their skeletons as review aids.</p><p>Coverage: COVERAGE. Technical rendering is
not a visual approval or a runtime performance measurement.</p><nav>
<button onclick="filter('study')">New city assets</button>
<button onclick="filter('legacy')">Legacy inventory</button>
<button onclick="filter('all')">All renders</button></nav></header><main>CARDS</main>
<script>function filter(group){document.querySelectorAll('figure').forEach(
el=>el.hidden=group!=='all'&&el.dataset.group!==group)}filter('study')</script></html>"""
    page = page.replace(
        "COVERAGE", f"{len(expected_study)} city assets and {len(expected_legacy)} legacy assets"
    )
    page = page.replace("CARDS", "\n".join(cards))
    (GALLERY / "index.html").write_text(page, encoding="utf-8")
    summary = {
        "legacy_expected": len(expected_legacy),
        "study_expected": len(expected_study),
        "rendered": len(expected),
        "missing": [],
        "source": "coverage.json",
        "art_approval": "pending visual runtime review",
    }
    (GALLERY / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (STUDY / "reference-inventory.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["asset", "source", "export", "bytes", "meshes", "triangles", "status"])
        for asset in expected_study:
            report = reports[f"study/{asset['id']}"]
            writer.writerow(
                [
                    asset["id"],
                    asset["source"],
                    asset["export"],
                    (STUDY / asset["export"]).stat().st_size,
                    report["meshes"],
                    report["triangles"],
                    "authored study; individual Blender render available",
                ]
            )
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
