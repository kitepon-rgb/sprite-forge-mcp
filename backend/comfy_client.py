"""Async ComfyUI client: drives the GPU box over HTTP + WebSocket.

One shared WS connection (single client_id) listens for execution/progress events
and fans them out to per-prompt asyncio.Queues so SSE endpoints can stream live
progress to the browser. Completion is detected via WS, with /history as fallback.
"""
from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any, AsyncGenerator

import httpx
import websockets

from . import config


class ComfyClient:
    def __init__(self, base_url: str = config.COMFY_URL):
        self.base = base_url.rstrip("/")
        self.ws_url = self.base.replace("http", "ws", 1) + "/ws"
        self.client_id = uuid.uuid4().hex
        self._http = httpx.AsyncClient(timeout=60.0)
        self._queues: dict[str, asyncio.Queue] = {}     # prompt_id -> event queue
        self._done: dict[str, asyncio.Event] = {}       # prompt_id -> completion
        self._subscribers: set[asyncio.Queue] = set()   # global SSE progress subscribers
        self._ws_task: asyncio.Task | None = None
        self._cur_prompt: str | None = None             # prompt being executed (WS context)

    async def start(self):
        if self._ws_task is None:
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def aclose(self):
        if self._ws_task:
            self._ws_task.cancel()
        await self._http.aclose()

    # ---- global progress subscription (SSE) ----
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- WS event hub ----
    async def _ws_loop(self):
        url = f"{self.ws_url}?clientId={self.client_id}"
        while True:
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue  # preview image blobs; ignore
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        self._dispatch(msg)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(1.0)  # reconnect with backoff

    def _dispatch(self, msg: dict):
        mtype = msg.get("type")
        data = msg.get("data", {}) or {}
        pid = data.get("prompt_id") or self._cur_prompt
        if mtype == "execution_start":
            self._cur_prompt = data.get("prompt_id")
            pid = self._cur_prompt
        if pid and pid in self._queues:
            self._queues[pid].put_nowait({"type": mtype, "data": data})
        # fan out to global SSE subscribers (live progress bar in the WebUI)
        if self._subscribers:
            ev = {"type": mtype, "data": data}
            for q in list(self._subscribers):
                try:
                    q.put_nowait(ev)
                except Exception:
                    pass  # subscriber backed up -> drop (progress is best-effort)
        # completion: execution_success/error/interrupted, OR the classic
        # {executing, node: null} signal — emitted AFTER /history is persisted, so it
        # side-steps the race where execution_success fires before outputs are written.
        done = (mtype in ("execution_success", "execution_error", "execution_interrupted")
                or (mtype == "executing" and data.get("node") is None))
        if done:
            tgt = data.get("prompt_id") or pid
            ev = self._done.get(tgt)
            if ev:
                ev.set()

    # ---- REST ----
    async def system_stats(self) -> dict:
        r = await self._http.get(f"{self.base}/system_stats")
        r.raise_for_status()
        return r.json()

    async def object_info(self, node: str | None = None) -> dict:
        path = "/object_info" + (f"/{node}" if node else "")
        r = await self._http.get(f"{self.base}{path}")
        r.raise_for_status()
        return r.json()

    async def upload_image(self, data: bytes, name: str) -> str:
        files = {"image": (name, data, "image/png")}
        r = await self._http.post(f"{self.base}/upload/image",
                                  files=files, data={"overwrite": "true"})
        r.raise_for_status()
        return r.json()["name"]

    async def get_image(self, filename: str, subfolder: str = "", ftype: str = "output") -> bytes:
        r = await self._http.get(f"{self.base}/view",
                                 params={"filename": filename, "subfolder": subfolder, "type": ftype})
        r.raise_for_status()
        return r.content

    async def submit(self, workflow: dict) -> str:
        body = {"prompt": workflow, "client_id": self.client_id}
        r = await self._http.post(f"{self.base}/prompt", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI /prompt rejected: {r.status_code} {r.text[:600]}")
        pid = r.json()["prompt_id"]
        self._queues[pid] = asyncio.Queue()
        self._done[pid] = asyncio.Event()
        return pid

    async def events(self, prompt_id: str) -> AsyncGenerator[dict, None]:
        """Yield live progress events for a prompt (for SSE)."""
        q = self._queues.get(prompt_id)
        if q is None:
            return
        while True:
            ev = await q.get()
            yield ev
            if ev["type"] in ("execution_success", "execution_error", "execution_interrupted"):
                return

    async def wait(self, prompt_id: str, timeout: float = 300.0) -> dict:
        ev = self._done.get(prompt_id)
        try:
            if ev:
                await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        # /history can lag the completion event (outputs are written just after
        # execution_success) -> poll briefly until outputs OR an error appear, so a
        # successful generation is never misread as "produced no image".
        hist: dict = {}
        for _ in range(40):
            r = await self._http.get(f"{self.base}/history/{prompt_id}")
            r.raise_for_status()
            hist = r.json().get(prompt_id, {})
            status = hist.get("status") or {}
            if hist.get("outputs") or status.get("status_str") == "error":
                break
            await asyncio.sleep(0.15)
        self._queues.pop(prompt_id, None)
        self._done.pop(prompt_id, None)
        return hist

    @staticmethod
    def outputs_images(hist: dict) -> list[dict]:
        imgs = []
        for _nid, out in (hist.get("outputs") or {}).items():
            for im in out.get("images", []) or []:
                imgs.append(im)
        return imgs

    async def run(self, workflow: dict, timeout: float = 300.0) -> dict:
        """Submit + wait, return history entry."""
        pid = await self.submit(workflow)
        return await self.wait(pid, timeout=timeout)
