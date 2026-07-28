from pathlib import Path

import pypdfium2 as pdfium


ROOT = Path(r"C:\Users\eric7\OneDrive\Desktop\pjt\.codex-line-removal")
QA = ROOT / "qa"
RENDERS = ROOT / "renders"
RENDERS.mkdir(parents=True, exist_ok=True)

for stem in ("requirements", "proposal"):
    document = pdfium.PdfDocument(QA / f"{stem}.pdf")
    for index in range(len(document)):
        page = document[index]
        image = page.render(scale=2.0).to_pil()
        image.save(RENDERS / f"{stem}-page-{index + 1}.png")
        page.close()
    print(f"{stem}: {len(document)} pages")
    document.close()
