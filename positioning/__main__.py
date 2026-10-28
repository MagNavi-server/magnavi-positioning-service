"""업무 DB 없이 로컬 모델 gRPC 서버를 실행하는 검증용 진입점이다."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal

from fastapi import FastAPI

from services.model_lifecycle import model_lifespan


async def run(config_path: Path, ready_file: Path | None):
    """같은 모델 수명 관리로 서버를 시작하고 정상 종료 신호를 기다린다."""
    os.environ["POSITIONING_CONFIG"] = str(config_path)
    application = FastAPI()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    owns_ready_file = False
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        async with model_lifespan(application):
            server = application.state.positioning_server
            metadata = {"host": server.config.host, "port": server.port, "ready": server.ready}
            if ready_file:
                # 이미 있는 파일을 지우거나 다른 실행의 준비 표시를 덮어쓰지 않는다.
                with ready_file.open("x", encoding="utf-8") as output:
                    json.dump(metadata, output)
                owns_ready_file = True
            print(json.dumps(metadata), flush=True)
            await stopped.wait()
    finally:
        if owns_ready_file:
            ready_file.unlink(missing_ok=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main():
    """명시적인 모델 등록 파일을 받아 루프백 검증 서버를 실행한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ready-file", type=Path)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.config, arguments.ready_file))


if __name__ == "__main__":
    main()
