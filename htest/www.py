"""联合仿真里宿主机那一头的 HTTP 服务：WWW_ROOT 下的文件原样给出，/ 回一页认得出的文字。
只绑在测试用的 TAP 网卡的地址上，宿主机不替仿真里的网段转发出网，测试一个包也不出本机。
用法：WWW_ROOT=<目录> python3 -m uvicorn www:app --app-dir htest --host <TAP 的地址> --port 8080
"""
import os
import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

ROOT = pathlib.Path(os.environ["WWW_ROOT"]).resolve()
PAGE = "to2610 lab page\n"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/", response_class=PlainTextResponse)
def index() -> str:
    return PAGE


@app.get("/{name}")
def file(name: str) -> FileResponse:
    p = (ROOT / name).resolve()
    if p.parent != ROOT or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)
