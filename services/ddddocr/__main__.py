from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Rushbuy ddddocr FastAPI service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("services.ddddocr.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
