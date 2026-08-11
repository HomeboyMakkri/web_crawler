from src.day2_demo import build_page_summary, build_statistics


def make_result(
    *,
    url: str = "https://example.com",
    error: str | None = None,
) -> dict:
    return {
        "url": url,
        "title": "Example" if error is None else "",
        "text": "Example text" if error is None else "",
        "links": ["https://example.com/about"] if error is None else [],
        "images": [{"src": "https://example.com/image.png", "alt": ""}],
        "headings": [{"level": "h1", "text": "Heading"}],
        "error": error,
    }


def test_build_page_summary() -> None:
    summary = build_page_summary(make_result())

    assert summary == {
        "url": "https://example.com",
        "status": "success",
        "title": "Example",
        "text_length": 12,
        "links_count": 1,
        "images_count": 1,
        "headings": ["Heading"],
        "links_sample": ["https://example.com/about"],
        "error": None,
    }


def test_build_statistics_counts_successes_and_failures() -> None:
    results = [
        make_result(),
        make_result(url="https://failed.example", error="Error: Timeout"),
    ]

    statistics = build_statistics(results, duration=1.23456)

    assert statistics == {
        "pages_total": 2,
        "pages_successful": 1,
        "pages_failed": 1,
        "total_text_length": 12,
        "total_links": 1,
        "total_images": 2,
        "elapsed_seconds": 1.235,
    }
