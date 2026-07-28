from pathlib import Path
import zipfile

from lxml import etree


path = Path(
    r"C:\Users\eric7\OneDrive\Desktop\pjt\deliverables"
    r"\點餐指南針_AI智慧點餐_系統需求書.docx"
)
ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

with zipfile.ZipFile(path) as package:
    root = etree.fromstring(package.read("word/document.xml"))

body = root.find("w:body", ns)
for index, child in enumerate(body):
    text = "".join(child.xpath(".//w:t/text()", namespaces=ns))
    explicit_break = bool(
        child.xpath(
            './/w:br[@w:type="page"] | .//w:pageBreakBefore',
            namespaces=ns,
        )
    )
    print(
        index,
        etree.QName(child).localname,
        f"PAGE_BREAK={explicit_break}",
        repr(text[:120]),
    )
