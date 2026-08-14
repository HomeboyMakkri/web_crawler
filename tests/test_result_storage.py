import json

import pytest

from src.result_storage import save_crawl_results


@pytest.mark.asyncio
async def test_save_crawl_results_writes_complete_utf8_json(tmp_path) -> None:
    output_path = tmp_path / "crawl.json"
    results = {
        "https://example.com": {
            "url": "https://example.com",
            "title": "Пример",
        }
    }
    failed_urls = {"https://example.com/broken": "Ошибка сети"}
    statistics: dict[str, int | float] = {
        "pages_completed": 2,
        "pages_per_second": 4.5,
    }

    returned_path = await save_crawl_results(
        output_path,
        results=results,
        failed_urls=failed_urls,
        statistics=statistics,
    )

    assert returned_path == output_path
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "results": results,
        "failed_urls": failed_urls,
        "statistics": statistics,
    }
    assert "Пример" in output_path.read_text(encoding="utf-8")
