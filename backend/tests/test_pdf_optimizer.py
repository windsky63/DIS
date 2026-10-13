from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import fitz

from backend.pdf_optimizer import optimize_pdf


class PdfOptimizerTests(unittest.TestCase):
    def test_duplicate_image_streams_are_shared_and_pages_remain_renderable(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            source = folder / "source.pdf"
            output = folder / "optimized.pdf"
            one_page_files = []
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 120), False)
            pixmap.clear_with(220)
            image = pixmap.tobytes("png")
            for index in range(3):
                page_file = folder / f"page-{index}.pdf"
                with fitz.open() as document:
                    page = document.new_page(width=300, height=300)
                    page.insert_image(fitz.Rect(20, 20, 280, 280), stream=image)
                    document.save(page_file)
                one_page_files.append(page_file)
            with fitz.open() as merged:
                for page_file in one_page_files:
                    with fitz.open(page_file) as page_document:
                        merged.insert_pdf(page_document)
                merged.save(source)

            report = optimize_pdf(source, output)

            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["pageCount"], 3)
            with fitz.open(output) as optimized:
                image_xrefs = {
                    image_info[0]
                    for page in optimized
                    for image_info in page.get_images(full=True)
                }
                self.assertEqual(len(image_xrefs), 1)
                self.assertEqual(optimized.page_count, 3)
                self.assertGreater(optimized[1].get_pixmap().width, 0)


if __name__ == "__main__":
    unittest.main()
