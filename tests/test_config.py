"""Разбор DATABASE_URL: мусор при копировании с телефона не должен ломать запуск."""
import pytest

from bot.config import normalize_database_url


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@dpg-x-a/db",
        "postgres://u:p@dpg-x-a/db",
        "  'postgresql://u:p@dpg-x-a/db'\n",
        "​postgresql://u:p@dpg-x-a/db",
        "DATABASE_URL=postgresql://u:p@dpg-x-a/db",
        "postgresql://u:p@dpg-x-a/db postgresql://u:p@dpg-x-a/db",
    ],
)
def test_database_url_cleanup(raw):
    assert normalize_database_url(raw) == "postgresql+asyncpg://u:p@dpg-x-a/db"


@pytest.mark.parametrize(
    "raw, hint",
    [("PGPASSWORD=secret123 psql -h dpg-x", "PSQL Command"), ("dpg-das14t7", "Hostname"), ("", "пустой")],
)
def test_bad_database_url_message_hides_password(raw, hint):
    with pytest.raises(RuntimeError) as exc:
        normalize_database_url(raw)
    assert hint in str(exc.value) and "secret123" not in str(exc.value)
