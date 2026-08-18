import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.result_storage import save_error_report


class AsyncFileContext:
    def __init__(self) -> None:
        self.file = MagicMock()
        self.file.write = AsyncMock()

    async def __aenter__(self) -> MagicMock:
        return self.file

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_save_error_report_writes_structured_utf8_json() -> None:
    output_path = Path("reports/day5_errors.json")
    errors = {
        "https://example.com/missing": {
            "url": "https://example.com/missing",
            "error_type": "PermanentError",
            "status_code": 404,
            "message": "Страница не найдена",
        },
    }
    statistics: dict[str, object] = {
        "errors_by_type": {"PermanentError": 1},
        "successful_retries": 0,
    }
    file_context = AsyncFileContext()

    with patch(
        "src.result_storage.aiofiles.open",
        return_value=file_context,
    ) as open_mock:
        returned_path = await save_error_report(
            output_path,
            errors=errors,
            statistics=statistics,
        )

    assert returned_path == output_path
    open_mock.assert_called_once_with(output_path, "w", encoding="utf-8")
    file_context.file.write.assert_awaited_once()
    serialized = file_context.file.write.await_args.args[0]
    assert json.loads(serialized) == {
        "errors": errors,
        "statistics": statistics,
    }
    assert "Страница не найдена" in serialized
