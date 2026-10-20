from __future__ import annotations


def test_import_core_packages() -> None:
    import bangcle  # noqa: F401
    import bs_dvid  # noqa: F401
    import settings  # noqa: F401
    import services.ddddocr  # noqa: F401


def test_ddddocr_app_factory() -> None:
    from services.ddddocr.main import app

    assert app.title in (app.title,)  # FastAPI instance
