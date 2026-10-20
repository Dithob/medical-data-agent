"""Portable bootstrap for the offline demo (Python 3.12+)."""

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    os.environ.setdefault("PYTHONUTF8", "1")
    parser = argparse.ArgumentParser(description="医药问数离线演示启动器")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument(
        "--check", action="store_true", help="仅检查运行环境，不启动服务器"
    )
    parser.add_argument(
        "--no-install", action="store_true", help="离线模式，依赖缺失时直接报错"
    )
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("请使用 Python 3.12 或更新版本运行启动器")
    if not 1 <= args.port <= 65535:
        parser.error("端口需在 1～65535 之间")
    base = Path(__file__).resolve().parent
    env = Path(os.environ.get("MEDICAL_RAG_VENV", str(base / ".venv"))).resolve()
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    if Path(sys.prefix).resolve() != env:
        return subprocess.call([str(python), str(__file__), *sys.argv[1:]], cwd=base)
    missing = [
        name
        for name in ("fastapi", "uvicorn", "multipart", "openpyxl")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        if args.no_install:
            parser.error("缺少依赖：" + ", ".join(missing) + "；请先联网运行启动器安装")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(base / "requirements.lock"),
            ],
            check=True,
        )
    from server.config import DATA_DIR, AS_OF_DATE

    print(
        f"离线规则演示 | Python {sys.version.split()[0]} | 数据目录 {DATA_DIR} | 参考日 {AS_OF_DATE}",
        flush=True,
    )
    if args.check:
        return 0
    import uvicorn

    print(f"浏览器访问 http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run("server.app:app", host="127.0.0.1", port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
