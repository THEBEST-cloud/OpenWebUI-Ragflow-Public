"""
title: RAGFlow Pipeline (No Citation, Safe for Formulas)
date: 2025-12-15
version: 1.4
license: MIT
description: Connects Open WebUI to RAGFlow, filters out system tasks, streams TEXT ONLY, removes citations safely (keeps formulas/indexing). Uses overlap (LCS-style suffix/prefix) to avoid missing chars.
requirements: requests, pydantic
"""

import json
import re
import logging
from typing import List, Union, Generator, Iterator

import requests
from pydantic import BaseModel, Field

# 设置日志
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class Pipeline:
    class Valves(BaseModel):
        RAGFLOW_API_BASE_URL: str = Field(
            default="http://YOUR_RAGFLOW_HOST:PORT",
            description="RAGFlow API 的基础地址 (例如 http://IP:PORT/api/v1)",
        )
        RAGFLOW_API_KEY: str = Field(
            default="ragflow-YOUR_API_KEY",
            description="RAGFlow 的 API Key",
        )
        ASSISTANT_NAME: str = Field(
            default="碳通量专家",
            description="RAGFlow 中创建的 Assistant (助手) 名称",
        )
        sessionName: str = Field(
            default="OpenWebui测试",
            description="RAGFlow 会话名称",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.assistant_id = None
        self.session_map = {}
        self.last_signature = {}
        self.debug = False  # 建议线上 False

    async def on_shutdown(self):
        pass

    async def on_startup(self):
        pass

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.valves.RAGFLOW_API_KEY}",
        }

    def _get_api_url(self, endpoint: str) -> str:
        base = self.valves.RAGFLOW_API_BASE_URL.rstrip("/")
        # 避免重复 /api/v1
        if base.endswith("/api/v1") and endpoint.startswith("/api/v1"):
            endpoint = endpoint[7:]
        return f"{base}{endpoint}"

    def _find_assistant_by_name(self):
        logger.info(f"Searching assistant by name: {self.valves.ASSISTANT_NAME}")
        url = self._get_api_url("/api/v1/chats")
        params = {"page": 1, "page_size": 30, "name": self.valves.ASSISTANT_NAME}
        try:
            r = requests.get(url, headers=self._headers(), params=params, timeout=30)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("code") == 0:
                    items = resp.get("data", [])
                    if items:
                        aid = items[0].get("id")
                        logger.info(f"Found assistant ID: {aid}")
                        return aid
            logger.error(f"[RAGFlow] List chats failed: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"[RAGFlow] Connection exception in find_assistant: {e}")
        return None

    def _create_session(self, assistant_id, session_name):
        logger.info(f"Creating session '{session_name}' for assistant {assistant_id}")
        url = self._get_api_url(f"/api/v1/chats/{assistant_id}/sessions")
        payload = {"name": session_name}
        try:
            r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("code") == 0:
                    sid = resp.get("data", {}).get("id")
                    logger.info(f"Session created successfully: {sid}")
                    return sid
            logger.error(f"[RAGFlow] Create session failed: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"[RAGFlow] Session creation exception: {e}")
        return None

    def _get_session_by_name(self, assistant_id, session_name):
        logger.info(f"Looking up session '{session_name}' for assistant {assistant_id}")
        url = self._get_api_url(f"/api/v1/chats/{assistant_id}/sessions")
        params = {"page": 1, "page_size": 30, "name": session_name}
        try:
            r = requests.get(url, headers=self._headers(), params=params, timeout=30)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("code") == 0:
                    items = resp.get("data", [])
                    if items:
                        sid = items[0].get("id")
                        logger.info(f"Found existing session ID: {sid}")
                        return sid
            logger.error(f"[RAGFlow] List sessions failed: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"[RAGFlow] Session list exception: {e}")
        return None

    # ---------------------- 核心：只删引用，不删公式 ----------------------
    _pat_ragflow_ids = re.compile(r"\[IDs?:\s*[0-9,\s]+\]")

    # 纯数字引用：仅在句末/标点/换行前删除（避免删 A[1]=..., x[1]y 等索引）
    _pat_numeric_cites = re.compile(
        r"(?:\[\d{1,4}(?:\s*,\s*\d{1,4})*\])+"          # [1] / [1, 2] / [1][2]
        r"(?=\s*(?:$|[，。,.;；:：!?！？\n]))"            # 后面必须是结尾/标点/换行
    )

    # 末尾半个 Markdown/HTML 标记缓冲：``` / ```text / <think...
    _pat_tail_buffer = re.compile(r"(`{1,3}(?:t(?:e(?:x(?:t)?)?)?)?|<[^>]{0,30})$")

    @classmethod
    def _strip_citations_only(cls, text: str) -> str:
        if not text:
            return text
        text = cls._pat_ragflow_ids.sub("", text)
        text = cls._pat_numeric_cites.sub("", text)
        # 清理因删引用产生的多余空格/空行
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    @classmethod
    def _trim_incomplete_tail(cls, raw: str) -> str:
        """截掉末尾可能被切断的标记：```、<...、以及未闭合的 [ ..."""
        if not raw:
            return raw

        # 1) 处理末尾未闭合的 '['（最近一个 '[' 在最近一个 ']' 之后）
        lb = raw.rfind("[")
        rb = raw.rfind("]")
        if lb > rb:
            raw = raw[:lb]

        # 2) 处理末尾反引号/未闭合 HTML
        m = cls._pat_tail_buffer.search(raw)
        if m:
            raw = raw[: m.start()]

        return raw

    @staticmethod
    def _suffix_prefix_overlap(prev: str, curr: str, max_check: int = 6000) -> int:
        """
        LCS-style（受限版）：找 curr 的前缀与 prev 的后缀最大重叠长度。
        用于 curr 不是 prev 的严格前缀扩展时，尽量避免“吞字”。
        """
        if not prev or not curr:
            return 0

        prev_tail = prev[-max_check:]
        max_k = min(len(prev_tail), len(curr))

        # 从大到小找重叠
        for k in range(max_k, 0, -1):
            if curr.startswith(prev_tail[-k:]):
                return k
        return 0

    def pipe(
        self, user_message: str, model_id: str, messages: List[dict], body: dict
    ) -> Union[str, Generator, Iterator]:

        clean_query = user_message or ""
        logger.info(f"Pipe called with query: {clean_query[:100]}")

        # 拦截系统任务
        if clean_query and "Task:" in clean_query:
            if (
                "Suggest" in clean_query
                or "follow-up" in clean_query
                or ("Generate" in clean_query and "title" in clean_query)
                or ("Generate" in clean_query and "tags" in clean_query)
            ):
                logger.info("Ignoring system task")
                return

        # 初始化 Assistant ID
        if not self.assistant_id:
            self.assistant_id = self._find_assistant_by_name()
            if not self.assistant_id:
                err_msg = f"Error: 无法找到名为 '{self.valves.ASSISTANT_NAME}' 的助手，请检查 RAGFlow 设置。"
                logger.error(err_msg)
                yield err_msg
                return

        # 会话
        body = body or {}
        owa_chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "default"
        session_id = self.session_map.get(owa_chat_id)

        if not session_id:
            session_name = self.valves.sessionName
            session_id = self._get_session_by_name(self.assistant_id, session_name)
            if not session_id:
                session_id = self._create_session(self.assistant_id, session_name)
            if session_id:
                self.session_map[owa_chat_id] = session_id
                logger.info(f"Mapped OpenWebUI chat_id '{owa_chat_id}' to RAGFlow session '{session_id}'")
            else:
                logger.error("Failed to retrieve or create session.")
                yield "Error: 无法获取或创建会话。"
                return

        # 防重复（保留你的逻辑）
        sig = (self.assistant_id, session_id, clean_query)
        if self.last_signature.get(owa_chat_id) == sig:
            return
        self.last_signature[owa_chat_id] = sig

        # 调 RAGFlow
        url = self._get_api_url(f"/api/v1/chats/{self.assistant_id}/completions")
        payload = {"question": clean_query, "session_id": session_id, "stream": True}

        logger.info(f"Sending stream request to RAGFlow: {url}")

        # 关键：用“原始累计文本”的重叠来计算 delta；再对 delta 做清洗（删引用），避免因清洗导致的长度变化吞字
        prev_raw = ""

        try:
            req = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                stream=True,
                timeout=(10, 300),  # (connect, read)
            )

            if req.status_code != 200:
                logger.error(f"RAGFlow API returned error: {req.status_code} {req.text}")
                yield f"**RAGFlow API Error**: {req.status_code}\n{req.text}"
                return

            for line in req.iter_lines():
                if not line:
                    continue

                try:
                    decoded_line = line.decode("utf-8", errors="ignore")
                    json_str = decoded_line[5:].strip() if decoded_line.startswith("data:") else decoded_line
                    if not json_str:
                        continue

                    json_data = json.loads(json_str)
                    inner_data = json_data.get("data")

                    # 结束信号
                    if inner_data is True:
                        break

                    if not isinstance(inner_data, dict):
                        continue

                    raw_answer = inner_data.get("answer", "")
                    if raw_answer is None:
                        raw_answer = ""

                    # 截掉末尾不稳定部分，避免前端渲染炸裂
                    curr_raw = self._trim_incomplete_tail(raw_answer)
                    if not curr_raw:
                        continue

                    # 计算 raw_delta（优先严格前缀；否则用后缀-前缀重叠）
                    if prev_raw and curr_raw.startswith(prev_raw):
                        raw_delta = curr_raw[len(prev_raw):]
                    else:
                        overlap = self._suffix_prefix_overlap(prev_raw, curr_raw)
                        raw_delta = curr_raw[overlap:] if overlap > 0 else curr_raw

                    prev_raw = curr_raw
                    if not raw_delta:
                        continue

                    # 去掉 Markdown 围栏（仅对 delta，不影响公式）
                    raw_delta = raw_delta.replace("```text", "").replace("```", "")

                    # 只删引用（安全）
                    out_delta = self._strip_citations_only(raw_delta)
                    if out_delta:
                        yield out_delta

                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON chunk: {line}")
                except Exception as e:
                    logger.error(f"Error processing stream chunk: {e}")

        except Exception as e:
            logger.error(f"Pipeline Connection Error: {e}")
            yield f"**Pipeline Connection Error**: {e}"
