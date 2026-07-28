from __future__ import annotations

from pathlib import Path
import tempfile
import zipfile

from lxml import etree


ROOT = Path(r"C:\Users\eric7\OneDrive\Desktop\pjt")
WORK = ROOT / ".codex-line-removal"
BACKUP = WORK / "backup"
DELIVERABLES = ROOT / "deliverables"

REQ_NAME = "點餐指南針_AI智慧點餐_系統需求書.docx"
PROP_NAME = "點餐指南針_AI智慧點餐_企劃書.docx"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def node_text(node) -> str:
    return "".join(node.xpath(".//w:t/text()", namespaces=NS))


def replace_paragraph_text(root, replacements: dict[str, str]) -> None:
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        old_text = node_text(paragraph)
        if old_text not in replacements:
            continue
        text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
        if not text_nodes:
            raise RuntimeError(f"段落沒有文字節點：{old_text}")
        text_nodes[0].text = replacements[old_text]
        for text_node in text_nodes[1:]:
            text_node.text = ""


def replace_all_text(root, old: str, new: str) -> None:
    for text_node in root.xpath(".//w:t", namespaces=NS):
        if text_node.text and old in text_node.text:
            text_node.text = text_node.text.replace(old, new)


def remove_matching_ancestor(root, xpath: str, ancestor_tag: str, needle: str) -> int:
    removed = 0
    for node in list(root.xpath(xpath, namespaces=NS)):
        if needle not in node_text(node):
            continue
        ancestor = node
        while ancestor is not None and ancestor.tag != f"{{{W_NS}}}{ancestor_tag}":
            ancestor = ancestor.getparent()
        if ancestor is None:
            raise RuntimeError(f"找不到 {ancestor_tag} 祖先：{needle}")
        ancestor.getparent().remove(ancestor)
        removed += 1
    return removed


def write_patched_docx(source: Path, destination: Path, patcher) -> None:
    with zipfile.ZipFile(source, "r") as source_zip:
        xml = source_zip.read("word/document.xml")
        root = etree.fromstring(xml)
        patcher(root)
        patched_xml = etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )

        with tempfile.NamedTemporaryFile(
            suffix=".docx", delete=False, dir=destination.parent
        ) as temporary:
            temporary_path = Path(temporary.name)

        try:
            with zipfile.ZipFile(
                temporary_path, "w", zipfile.ZIP_DEFLATED
            ) as output_zip:
                for item in source_zip.infolist():
                    data = (
                        patched_xml
                        if item.filename == "word/document.xml"
                        else source_zip.read(item.filename)
                    )
                    output_zip.writestr(item, data)
            temporary_path.replace(destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


def patch_requirements(root) -> None:
    replace_paragraph_text(
        root,
        {
            "系統範圍：Web 與 LINE 對話入口、Google 菜單擷取、菜單照片辨識與人工確認、標準化菜單資料、偏好與預算蒐集、AI 推薦與規則驗證、評價證據整理、健康檢查與操作紀錄。":
                "系統範圍：響應式 Web 入口、Google 菜單擷取、菜單照片辨識與人工確認、標準化菜單資料、偏好與預算蒐集、AI 推薦與規則驗證、評價證據整理、健康檢查與操作紀錄。",
            "安全性：API 金鑰僅由環境變數載入；LINE webhook 必須以原始請求本文驗證 HMAC-SHA256 簽章；上傳檔案須驗證格式、大小與安全檔名。":
                "安全性：API 金鑰僅由環境變數載入；上傳檔案須驗證格式、大小與安全檔名；錯誤訊息與日誌不得輸出密鑰內容。",
            "可維護性：菜單辨識、推薦、評論、LINE 整合與 API 層應模組化；核心規則須具單元測試，並以一致的 schema v2 儲存已確認菜單。":
                "可維護性：菜單辨識、推薦、評論、Web 介面與 API 層應模組化；核心規則須具單元測試，並以一致的 schema v2 儲存已確認菜單。",
            "系統應提供 Web 與 LINE Bot 兩種使用入口，並在同一後端服務中處理菜單、推薦與對話流程。":
                "系統應提供響應式 Web 使用入口，並在同一後端服務中處理菜單、推薦與對話流程。",
            "系統應提供健康檢查端點，顯示菜單載入、Vision 與 LINE 設定狀態，但不得回傳任何密鑰內容。":
                "系統應提供健康檢查端點，顯示菜單載入與 Vision 設定狀態，但不得回傳任何密鑰內容。",
            "a. 在 Web 或 LINE 選擇餐廳並上傳菜單照片。":
                "a. 在 Web 選擇餐廳並上傳菜單照片。",
        },
    )

    rows = root.xpath(".//w:tr", namespaces=NS)
    matching_rows = [row for row in rows if "OC-F-013" in node_text(row)]
    if len(matching_rows) != 1:
        raise RuntimeError(f"預期一個 OC-F-013 列，實際為 {len(matching_rows)}")
    matching_rows[0].getparent().remove(matching_rows[0])

    tables = root.xpath(".//w:tbl", namespaces=NS)
    matching_tables = [
        table
        for table in tables
        if "透過 LINE Bot 完成菜單辨識與推薦" in node_text(table)
    ]
    if len(matching_tables) != 1:
        raise RuntimeError(f"預期一個 LINE 使用案例表，實際為 {len(matching_tables)}")
    matching_tables[0].getparent().remove(matching_tables[0])

    public_menu_tables = [
        table
        for table in root.xpath(".//w:tbl", namespaces=NS)
        if "依餐廳名稱擷取公開菜單" in node_text(table)
    ]
    if len(public_menu_tables) != 1:
        raise RuntimeError(
            f"預期一個公開菜單使用案例表，實際為 {len(public_menu_tables)}"
        )
    preceding = public_menu_tables[0].getprevious()
    has_page_break = (
        preceding is not None
        and preceding.tag == f"{{{W_NS}}}p"
        and bool(
            preceding.xpath(
                './/w:br[@w:type="page"] | .//w:pageBreakBefore',
                namespaces=NS,
            )
        )
    )
    if not has_page_break:
        raise RuntimeError("找不到公開菜單使用案例前的分頁段落")
    preceding.getparent().remove(preceding)

    supplement_paragraphs = [
        paragraph
        for paragraph in root.xpath(".//w:p", namespaces=NS)
        if node_text(paragraph)
        == "補充：所有外部資料與 AI 產生內容均需通過格式、來源與限制條件檢查；菜單價格、過敏原及實際供應狀態仍以店家現場資訊為準。"
    ]
    if len(supplement_paragraphs) != 1:
        raise RuntimeError(
            f"預期一個補充段落，實際為 {len(supplement_paragraphs)}"
        )
    supplement_paragraphs[0].getparent().remove(supplement_paragraphs[0])

    replace_all_text(root, "OC-F-014", "OC-F-013")
    replace_all_text(root, "OC-UC004", "OC-UC003")
    replace_all_text(root, "OC-UC005", "OC-UC004")


def patch_proposal(root) -> None:
    replace_paragraph_text(
        root,
        {
            "從手機菜單照片或公開資料建立可修正的結構化菜單，依預算、偏好與忌口產生點餐建議，再以程式驗證品項、價格與限制條件；支援 Web 與 LINE 現場使用。":
                "從手機菜單照片或公開資料建立可修正的結構化菜單，依預算、偏好與忌口產生點餐建議，再以程式驗證品項、價格與限制條件；支援響應式 Web 現場使用。",
            "點餐指南針服務在「看不懂菜單、選擇太多、限制難表達、資訊可信度不足」的用餐情境。使用者可由網頁或 LINE 上傳紙本菜單照片，系統先辨識店名、分類、品項與價格，再以人工確認機制修正衝突，建立可供運算的正式菜單。":
                "點餐指南針服務在「看不懂菜單、選擇太多、限制難表達、資訊可信度不足」的用餐情境。使用者可由響應式網頁上傳紙本菜單照片，系統先辨識店名、分類、品項與價格，再以人工確認機制修正衝突，建立可供運算的正式菜單。",
            "創新四｜同一能力跨介面：Web 適合視覺化確認，LINE 適合現場拍照與即時對話；兩者共享後端、菜單與推薦規則，減少重複開發。":
                "創新四｜行動優先 Web：單一響應式介面整合拍照上傳、品質預覽、編號修正、確認與推薦流程，降低使用者的操作與安裝門檻。",
            "第一層｜互動入口：響應式 Web 提供餐廳選擇、照片辨識、品質預覽與對話推薦；LINE Bot 提供現場拍照、編號修正、確認／取消與推薦對話。":
                "第一層｜互動入口：響應式 Web 提供餐廳選擇、照片辨識、品質預覽、編號修正、確認／取消與對話推薦。",
            "第四層｜資料與外部服務：已確認菜單與評論快取以 JSON schema 保存；外部整合包含 Google 公開資訊、OpenAI-compatible 模型服務及 LINE Messaging API。":
                "第四層｜資料與外部服務：已確認菜單與評論快取以 JSON schema 保存；外部整合包含 Google 公開資訊與 OpenAI-compatible 模型服務。",
            "故障隔離：爬蟲、Vision、推薦模型與 LINE 皆以獨立設定啟用；任一外部服務失敗時，不覆蓋已確認資料，推薦可切換決定式備援。":
                "故障隔離：爬蟲、Vision 與推薦模型皆以獨立設定啟用；任一外部服務失敗時，不覆蓋已確認資料，推薦可切換決定式備援。",
            "LINE 對話採低學習成本指令：使用者可傳照片，依預覽編號輸入「改 3 菜名 …」或「改 3 價格 …」，最後以「確認／取消」完成；自然語言舊句型仍保留相容性。":
                "Web 修正流程採低學習成本設計：使用者可依預覽編號修正菜名或價格，查看衝突與品質指標，最後以「確認／取消」完成。",
            "(4) LINE Messaging API、HTML／CSS／JavaScript、Git 與單元測試":
                "(4) HTML／CSS／JavaScript、Git 與單元測試",
            "Web／LINE 介面整合與人工確認流程":
                "響應式 Web 介面整合與人工確認流程",
        },
    )

    paragraphs = root.xpath(".//w:p", namespaces=NS)
    matching = [
        paragraph
        for paragraph in paragraphs
        if node_text(paragraph)
        == "LINE webhook 使用原始本文 HMAC-SHA256 驗證；圖片先快速回覆，再於背景處理並推送結果，避免外部模型延遲造成 reply token 逾時。"
    ]
    if len(matching) != 1:
        raise RuntimeError(f"預期一個 LINE webhook 段落，實際為 {len(matching)}")
    matching[0].getparent().remove(matching[0])


def main() -> None:
    write_patched_docx(
        BACKUP / REQ_NAME,
        DELIVERABLES / REQ_NAME,
        patch_requirements,
    )
    write_patched_docx(
        BACKUP / PROP_NAME,
        DELIVERABLES / PROP_NAME,
        patch_proposal,
    )
    print("OOXML patch complete")


if __name__ == "__main__":
    main()
