from __future__ import annotations

from dsh_bing_search.providers.commons import parse_commons_pages

PAYLOAD = {
    "query": {
        "pages": {
            "52157232": {
                "pageid": 52157232,
                "ns": 6,
                "title": "File:A detail about School Uniform for GZ41MS (S).jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/wikipedia/commons/8/87/A_detail_about_School_Uniform_for_GZ41MS_%28S%29.jpg",
                        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/x.jpg/640px-x.jpg",
                        "width": 578,
                        "height": 922,
                        "mime": "image/jpeg",
                    }
                ],
            },
            "999": {
                "pageid": 999,
                "ns": 6,
                "title": "File:Not an image.pdf",
                "imageinfo": [{"url": "https://upload.wikimedia.org/wikipedia/commons/x.pdf", "mime": "application/pdf"}],
            },
        }
    }
}


def test_commons_pages_mapped_and_non_images_dropped() -> None:
    items = parse_commons_pages(PAYLOAD, count=10)
    assert len(items) == 1
    item = items[0]
    assert item.title == "File:A detail about School Uniform for GZ41MS (S).jpg"
    assert item.murl.startswith("https://upload.wikimedia.org")
    assert item.width == 578 and item.height == 922
    assert item.purl == "https://commons.wikimedia.org/wiki/File:A_detail_about_School_Uniform_for_GZ41MS_(S).jpg"


def test_commons_count_limit() -> None:
    items = parse_commons_pages(PAYLOAD, count=1)
    assert len(items) == 1
