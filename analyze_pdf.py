import pdfplumber
from pathlib import Path

pdf_path = r"C:\BOM-Components-Validator\backend\documents\raw\81351387\81351387-40 VMF 95A SAP DATA.pdf"

if not Path(pdf_path).exists():
    print(f"ERROR: File not found")
    exit(1)

print("PDF ANALYSIS - pdfplumber")
print("=" * 80)

with pdfplumber.open(pdf_path) as pdf:
    num_pages = len(pdf.pages)
    print(f"1. TOTAL PAGES: {num_pages}")
    print()
    
    all_text = ""
    total_tables = 0
    
    for page_num, page in enumerate(pdf.pages, 1):
        print(f"--- PAGE {page_num} ---")
        text = page.extract_text()
        all_text += text if text else ""
        
        tables = page.extract_tables()
        num_tables = len(tables) if tables else 0
        total_tables += num_tables
        
        print(f"  Tables detected: {num_tables}")
        print(f"  Text characters: {len(text) if text else 0}")
        
        if tables:
            for t_idx, table in enumerate(tables, 1):
                rows = len(table)
                cols = len(table[0]) if table else 0
                print(f"    Table {t_idx}: {rows} rows × {cols} cols")
                if table:
                    print(f"      Header row: {table[0]}")
        print()
    
    print("=" * 80)
    print("2. SUMMARY STATISTICS")
    print("=" * 80)
    print(f"  Total pages: {num_pages}")
    print(f"  Total tables: {total_tables}")
    print(f"  Total text length: {len(all_text)} characters")
    print(f"  Avg chars per page: {len(all_text)//num_pages if num_pages > 0 else 0}")
    print()
    
    print("=" * 80)
    print("3. COMPLETE TEXT EXTRACTION")
    print("=" * 80)
    print(all_text)
    print()
    print("=" * 80)
    print("4. DATA EXTRACTION COMPLETENESS")
    print("=" * 80)
    print(f"  Text extraction: SUCCESS ({len(all_text)} chars)")
    print(f"  Tables found: {total_tables}")
    print(f"  Potential data gaps:")
    print(f"    - Complex formatted tables: May need manual review")
    print(f"    - Scanned images: Requires OCR")
    print(f"    - Embedded graphics: Cannot extract as text")
