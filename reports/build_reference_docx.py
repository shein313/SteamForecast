"""Генерация reports/reference.docx - шаблона оформления Word для report.Rmd.

Требования методички СПбГУТ, зашитые в шаблон:
  * шрифт Times New Roman, 14 пт, межстрочный интервал 1.5, выравнивание по ширине;
  * абзацный отступ 1.25 см;
  * поля: левое 3 см, правое 1 см, верхнее 2 см, нижнее 2 см (формат A4);
  * сквозная нумерация страниц внизу по центру (на титульном листе номер скрыт);
  * заголовки: Times New Roman 14 пт, полужирный, чёрный; заголовок 1-го уровня
    - прописными, по центру, с новой страницы;
  * листинги - Courier New 9 пт, одинарный интервал; таблицы - одинарный интервал, рамки.

Запуск (из корня проекта):  python reports/build_reference_docx.py
Исходный шаблон берётся у pandoc:  pandoc -o reports/_ref_default.docx --print-default-data-file reference.docx
(скрипт сам вызывает pandoc, если он есть в PATH или передан в переменной PANDOC).
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPORTS = Path(__file__).resolve().parent
SRC = REPORTS / '_ref_default.docx'
DST = REPORTS / 'reference.docx'

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

# 1 см = 567 twips
PAGE_W, PAGE_H = 11906, 16838
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 1701, 567, 1134, 1134  # 3 / 1 / 2 / 2 см
TEXT_WIDTH = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT  # 9638 twips
FIRST_LINE = 709  # 1.25 см
LINE_15 = 360     # интервал 1.5 (240 = одинарный)


def ensure_default_reference() -> None:
    if SRC.exists():
        return
    pandoc = os.environ.get('PANDOC') or shutil.which('pandoc')
    if not pandoc:
        sys.exit('Не найден pandoc: положите reference.docx pandoc в reports/_ref_default.docx')
    subprocess.run([pandoc, '-o', str(SRC), '--print-default-data-file', 'reference.docx'], check=True)


def sub_style(styles: str, style_id: str, new_xml: str) -> str:
    """Заменяет определение стиля целиком; если его нет - добавляет в конец."""
    pattern = re.compile(r'<w:style\b[^>]*w:styleId="%s"[^>]*>.*?</w:style>' % re.escape(style_id), re.DOTALL)
    if pattern.search(styles):
        return pattern.sub(lambda _m: new_xml, styles, count=1)
    return styles.replace('</w:styles>', new_xml + '\n</w:styles>')


def para_style(style_id, name, ppr='', rpr='', based_on='Normal', next_style=None,
               custom=True, extra=''):
    custom_attr = ' w:customStyle="1"' if custom else ''
    return (
        f'<w:style w:type="paragraph"{custom_attr} w:styleId="{style_id}">'
        f'<w:name w:val="{name}"/>'
        + (f'<w:basedOn w:val="{based_on}"/>' if based_on else '')
        + (f'<w:next w:val="{next_style}"/>' if next_style else '')
        + extra + '<w:qFormat/>'
        + (f'<w:pPr>{ppr}</w:pPr>' if ppr else '')
        + (f'<w:rPr>{rpr}</w:rPr>' if rpr else '')
        + '</w:style>'
    )


BOLD = '<w:b/><w:bCs/>'
SZ14 = '<w:sz w:val="28"/><w:szCs w:val="28"/>'


def build_styles(styles: str) -> str:
    # 1. Шрифты и цвета: убираем темы/цвета во всех стилях
    tnr = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman" w:eastAsia="Times New Roman"/>'
    styles = re.sub(r'<w:rFonts\b[^>]*/>', tnr, styles, flags=re.DOTALL)
    styles = re.sub(r'<w:color\b[^>]*/>', '', styles, flags=re.DOTALL)

    # 2. Умолчания документа: TNR 14, интервал 1.5, русский язык
    doc_defaults = (
        '<w:docDefaults><w:rPrDefault><w:rPr>' + tnr + SZ14 +
        '<w:lang w:val="ru-RU" w:eastAsia="ru-RU" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
        f'<w:pPrDefault><w:pPr><w:spacing w:before="0" w:after="0" w:line="{LINE_15}" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
    )
    styles = re.sub(r'<w:docDefaults>.*?</w:docDefaults>', lambda _m: doc_defaults, styles, count=1, flags=re.DOTALL)

    # 3. Основной текст
    styles = sub_style(styles, 'BodyText', para_style(
        'BodyText', 'Body Text',
        ppr=f'<w:spacing w:before="0" w:after="0"/><w:ind w:firstLine="{FIRST_LINE}"/><w:jc w:val="both"/>',
        custom=False, extra='<w:link w:val="BodyTextChar"/>'))
    styles = sub_style(styles, 'FirstParagraph', para_style(
        'FirstParagraph', 'First Paragraph', based_on='BodyText', next_style='BodyText'))
    # Compact: элементы списков (интервал 1.5 из умолчаний) и ячейки таблиц (интервал 1.0 задаёт стиль таблицы)
    styles = sub_style(styles, 'Compact', para_style(
        'Compact', 'Compact', ppr='<w:ind w:firstLine="0"/><w:jc w:val="left"/>'))
    styles = sub_style(styles, 'BlockText', para_style(
        'BlockText', 'Block Text', based_on='BodyText', next_style='BodyText', custom=False,
        ppr='<w:ind w:left="709" w:right="0" w:firstLine="0"/>'))

    # 4. Заголовки
    styles = sub_style(styles, 'Heading1', para_style(
        'Heading1', 'heading 1', custom=False, next_style='BodyText',
        extra='<w:link w:val="Heading1Char"/><w:uiPriority w:val="9"/>',
        ppr='<w:keepNext/><w:keepLines/><w:pageBreakBefore/><w:spacing w:before="0" w:after="240"/>'
            '<w:jc w:val="center"/><w:outlineLvl w:val="0"/>',
        rpr=BOLD + '<w:caps/>' + SZ14))
    for lvl, ital in ((2, ''), (3, ''), (4, '<w:i/><w:iCs/>')):
        styles = sub_style(styles, f'Heading{lvl}', para_style(
            f'Heading{lvl}', f'heading {lvl}', custom=False, next_style='BodyText',
            extra=f'<w:link w:val="Heading{lvl}Char"/><w:uiPriority w:val="9"/>',
            ppr='<w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="120"/>'
                f'<w:ind w:firstLine="{FIRST_LINE}"/><w:jc w:val="left"/><w:outlineLvl w:val="{lvl - 1}"/>',
            rpr=BOLD + ital + SZ14))
    styles = sub_style(styles, 'Title', para_style(
        'Title', 'Title', custom=False, next_style='BodyText',
        ppr='<w:spacing w:before="0" w:after="240"/><w:jc w:val="center"/>', rpr=BOLD + SZ14))
    styles = sub_style(styles, 'TOCHeading', para_style(
        'TOCHeading', 'TOC Heading', custom=False, next_style='BodyText',
        ppr='<w:keepNext/><w:spacing w:before="0" w:after="240"/><w:jc w:val="center"/>',
        rpr=BOLD + '<w:caps/>' + SZ14))

    # 5. Оглавление
    for lvl, left in ((1, 0), (2, 284), (3, 568)):
        styles = sub_style(styles, f'TOC{lvl}', para_style(
            f'TOC{lvl}', f'toc {lvl}', custom=False, next_style='Normal',
            extra='<w:uiPriority w:val="39"/>',
            ppr=f'<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{TEXT_WIDTH}"/></w:tabs>'
                f'<w:ind w:left="{left}" w:firstLine="0"/><w:jc w:val="left"/>'))

    # 6. Подписи к рисункам и таблицам (ГОСТ 7.32-2017): "Рисунок N - ..." под рисунком по центру,
    #    "Таблица N - ..." над таблицей слева без абзацного отступа
    styles = sub_style(styles, 'Caption', para_style(
        'Caption', 'Caption', custom=False,
        ppr='<w:spacing w:before="120" w:after="240"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>'))
    styles = sub_style(styles, 'ImageCaption', para_style(
        'ImageCaption', 'Image Caption', based_on='Caption',
        ppr='<w:spacing w:before="120" w:after="240"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>'))
    styles = sub_style(styles, 'TableCaption', para_style(
        'TableCaption', 'Table Caption', based_on='Caption',
        ppr='<w:keepNext/><w:spacing w:before="240" w:after="120"/><w:ind w:firstLine="0"/><w:jc w:val="left"/>'))
    styles = sub_style(styles, 'Figure', para_style(
        'Figure', 'Figure', ppr='<w:spacing w:before="240" w:after="0" w:line="240" w:lineRule="auto"/>'
                                '<w:ind w:firstLine="0"/><w:jc w:val="center"/>'))
    styles = sub_style(styles, 'CaptionedFigure', para_style(
        'CaptionedFigure', 'Captioned Figure', based_on='Figure', ppr='<w:keepNext/>'))

    # 7. Листинги и код
    styles = sub_style(styles, 'VerbatimChar', (
        '<w:style w:type="character" w:customStyle="1" w:styleId="VerbatimChar"><w:name w:val="Verbatim Char"/>'
        '<w:basedOn w:val="DefaultParagraphFont"/><w:rPr>'
        '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New" w:eastAsia="Courier New"/>'
        '<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>'))
    styles = sub_style(styles, 'SourceCode', para_style(
        'SourceCode', 'Source Code',
        ppr='<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/>'
            '<w:jc w:val="left"/><w:shd w:val="clear" w:color="auto" w:fill="F5F5F5"/>',
        rpr='<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New" w:eastAsia="Courier New"/>'
            '<w:sz w:val="18"/><w:szCs w:val="18"/>'))

    # 8. Таблицы: сетка, одинарный интервал, 12 пт, шапка полужирным
    border = ''.join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                     for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    styles = sub_style(styles, 'Table', (
        '<w:style w:type="table" w:default="1" w:styleId="Table"><w:name w:val="Table"/>'
        '<w:basedOn w:val="TableNormal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
        '<w:rPr><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>'
        '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
        f'<w:tblBorders>{border}</w:tblBorders>'
        '<w:tblCellMar><w:top w:w="28" w:type="dxa"/><w:left w:w="85" w:type="dxa"/>'
        '<w:bottom w:w="28" w:type="dxa"/><w:right w:w="85" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:bCs/></w:rPr>'
        '<w:tcPr><w:vAlign w:val="center"/></w:tcPr></w:tblStylePr></w:style>'))

    # 9. Прочее
    styles = sub_style(styles, 'Hyperlink', (
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:basedOn w:val="DefaultParagraphFont"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'))
    styles = sub_style(styles, 'Footer', para_style(
        'Footer', 'footer', custom=False,
        ppr='<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>',
        rpr='<w:sz w:val="24"/><w:szCs w:val="24"/>'))
    styles = sub_style(styles, 'Bibliography', para_style(
        'Bibliography', 'Bibliography', custom=False,
        ppr='<w:ind w:left="0" w:firstLine="709"/><w:jc w:val="both"/>'))

    # 10. Пользовательские стили титульного листа и служебные
    spacing_single = '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'
    styles = sub_style(styles, 'TitleCenter', para_style(
        'TitleCenter', 'TitleCenter',
        ppr=f'<w:spacing w:before="0" w:after="0" w:line="{LINE_15}" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>'))
    styles = sub_style(styles, 'TitleBold', para_style(
        'TitleBold', 'TitleBold', based_on='TitleCenter', rpr=BOLD))
    styles = sub_style(styles, 'TitleBlock', para_style(
        'TitleBlock', 'TitleBlock',
        ppr=f'{spacing_single}<w:ind w:left="4536" w:firstLine="0"/><w:jc w:val="left"/>'))
    styles = sub_style(styles, 'PlainNoIndent', para_style(
        'PlainNoIndent', 'PlainNoIndent', based_on='BodyText', ppr='<w:ind w:firstLine="0"/><w:jc w:val="left"/>'))
    return styles


FOOTER_PAGE = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
    '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
    '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>'
)
FOOTER_EMPTY = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr></w:p></w:ftr>'
)

SECT_PR = (
    '<w:sectPr>'
    '<w:footerReference w:type="default" r:id="rId901"/>'
    '<w:footerReference w:type="first" r:id="rId902"/>'
    '<w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>'
    f'<w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
    f'<w:pgMar w:top="{MARGIN_TOP}" w:right="{MARGIN_RIGHT}" w:bottom="{MARGIN_BOTTOM}" w:left="{MARGIN_LEFT}" '
    'w:header="709" w:footer="567" w:gutter="0"/>'
    '<w:titlePg/>'
    '</w:sectPr>'
)


def main() -> None:
    ensure_default_reference()
    with zipfile.ZipFile(SRC) as zin:
        parts = {name: zin.read(name) for name in zin.namelist()}

    parts['word/styles.xml'] = build_styles(parts['word/styles.xml'].decode('utf-8')).encode('utf-8')

    doc = parts['word/document.xml'].decode('utf-8')
    doc = re.sub(r'<w:sectPr>.*?</w:sectPr>', lambda _m: SECT_PR, doc, flags=re.DOTALL)
    if 'xmlns:r=' not in doc.split('>', 2)[1] and 'xmlns:r=' not in doc[:2000]:
        doc = doc.replace('<w:document ', f'<w:document xmlns:r="{R_NS}" ', 1)
    parts['word/document.xml'] = doc.encode('utf-8')

    parts['word/footer1.xml'] = FOOTER_PAGE.encode('utf-8')
    parts['word/footer2.xml'] = FOOTER_EMPTY.encode('utf-8')

    rels = parts['word/_rels/document.xml.rels'].decode('utf-8')
    footer_rel = (
        f'<Relationship Type="{R_NS}/footer" Id="rId901" Target="footer1.xml"/>'
        f'<Relationship Type="{R_NS}/footer" Id="rId902" Target="footer2.xml"/>'
    )
    parts['word/_rels/document.xml.rels'] = rels.replace('</Relationships>', footer_rel + '</Relationships>').encode('utf-8')

    ctypes = parts['[Content_Types].xml'].decode('utf-8')
    footer_ct = ''.join(
        f'<Override PartName="/word/footer{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        for i in (1, 2))
    parts['[Content_Types].xml'] = ctypes.replace('</Types>', footer_ct + '</Types>').encode('utf-8')

    with zipfile.ZipFile(DST, 'w', zipfile.ZIP_DEFLATED) as zout:
        # [Content_Types].xml должен идти первым
        order = ['[Content_Types].xml'] + [n for n in parts if n != '[Content_Types].xml']
        for name in order:
            zout.writestr(name, parts[name])
    print(f'Создан {DST}')


if __name__ == '__main__':
    main()
