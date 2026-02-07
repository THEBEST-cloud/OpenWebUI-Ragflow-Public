"""
title: RAGFlow Pipeline 带引用（修复版）
date: 2025-12-15
version: 1.3
license: MIT
description: Connects Open WebUI to RAGFlow, filters out system suggestion tasks, and handles streaming with robust citations.
requirements: requests, pydantic
"""

import requests
import json
import re
import logging
from typing import List, Union, Generator, Iterator
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
        self.debug = True

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
                else:
                    logger.error(f"[RAGFlow] List chats failed: {resp}")
            else:
                logger.error(f"[RAGFlow] HTTP error: {r.status_code} {r.text}")
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
                else:
                    logger.error(f"[RAGFlow] Create session failed: {resp}")
            else:
                logger.error(f"[RAGFlow] HTTP error creating session: {r.status_code} {r.text}")
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
                else:
                    logger.error(f"[RAGFlow] List sessions failed: {resp}")
            else:
                logger.error(f"[RAGFlow] HTTP error listing sessions: {r.status_code} {r.text}")
        except Exception as e:
            logger.error(f"[RAGFlow] Session list exception: {e}")
        return None

    def pipe(
        self, user_message: str, model_id: str, messages: List[dict], body: dict
    ) -> Union[str, Generator, Iterator]:

        if self.debug:
            logger.debug(f"messages: {messages}")
            logger.debug(f"model_id: {model_id}")
            logger.debug(f"user_message: {user_message}")
            logger.debug(f"body: {body}")

        clean_query = user_message or ""
        logger.info(f"Pipe called with query: {clean_query[:100]}")

        # 只识别 RAGFlow 输出的 [ID:数字] 或 [IDs:数字1, 数字2]
        id_ref_pattern = re.compile(r"\[IDs?:\s*([0-9,\s]+)\]")
        # 尾部未完整输出（避免半个 [ID: / 半个 ```）
        danger_tail_pattern = re.compile(r"(?:\[(?:I(?:D(?::\d*)?)?)?|`{1,3}\w*)$")

        # 拦截系统任务
        if clean_query and "Task:" in clean_query:
            if (
                "Suggest" in clean_query
                or "follow-up" in clean_query
                or ("Generate" in clean_query and "title" in clean_query)
                or ("Generate" in clean_query and "tags" in clean_query)
            ):
                logger.info(f"[Pipeline] Ignored system task: {clean_query[:50]}...")
                return

        # 初始化 assistant_id
        if not self.assistant_id:
            self.assistant_id = self._find_assistant_by_name()
            if not self.assistant_id:
                err_msg = f"Error: 无法找到名为 '{self.valves.ASSISTANT_NAME}' 的助手，请检查 RAGFlow 设置。"
                logger.error(err_msg)
                yield err_msg
                return

        # session
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

        # request
        url = self._get_api_url(f"/api/v1/chats/{self.assistant_id}/completions")
        sig = (self.assistant_id, session_id, clean_query)
        self.last_signature[owa_chat_id] = sig

        payload = {"question": clean_query, "session_id": session_id, "stream": True}
        logger.info(f"Sending stream request to RAGFlow: {url}")

        def longest_common_prefix(a: str, b: str) -> int:
            i = 0
            n = min(len(a), len(b))
            while i < n and a[i] == b[i]:
                i += 1
            return i

        def truncate(text: str, max_len: int = 900) -> str:
            if not isinstance(text, str):
                text = str(text)
            if len(text) <= max_len:
                return text
            return text[:max_len] + "…"

        # 关键：chunk_idx -> cite_no（连续编号 1,2,3…）
        cite_map = {}  # type: dict[int, int]

        def alloc_cite_no(chunk_idx: int) -> int:
            if chunk_idx not in cite_map:
                cite_map[chunk_idx] = len(cite_map) + 1
            return cite_map[chunk_idx]

        try:
            req = requests.post(url, headers=self._headers(), json=payload, stream=True, timeout=300)

            if req.status_code != 200:
                logger.error(f"RAGFlow API returned error: {req.status_code} {req.text}")
                yield f"**RAGFlow API Error**: {req.status_code}\n{req.text}"
                return

            previous_cleaned_text = ""
            all_retrieved_chunks = []
            chunks_loaded = False
            sent_chunk_indices = set()  # 已发送过 citation event 的 chunk_idx
            detected_ids = set()        # 已在文本中出现过的 chunk_idx（累计）

            for line in req.iter_lines():
                if not line:
                    continue

                try:
                    decoded_line = line.decode("utf-8", errors="ignore")

                    if self.debug:
                        logger.debug("-" * 30)
                        logger.debug(decoded_line)
                        logger.debug("-" * 30)

                    # SSE 可能带 data:
                    json_str = decoded_line[5:].strip() if decoded_line.startswith("data:") else decoded_line
                    if not json_str:
                        continue

                    json_data = json.loads(json_str)

                    # 有些流式结束会发 data:true
                    if json_data.get("data") is True:
                        break

                    inner_data = json_data.get("data", {})
                    if not isinstance(inner_data, dict):
                        continue

                    # 先拿 chunks（引用源）
                    reference_data = inner_data.get("reference", {})
                    if (not chunks_loaded) and reference_data and reference_data.get("chunks"):
                        all_retrieved_chunks = reference_data.get("chunks", [])
                        chunks_loaded = True
                        logger.info(f"Loaded {len(all_retrieved_chunks)} reference chunks.")

                    # 文本（RAGFlow 通常是累计文本）
                    current_text_buffer = inner_data.get("answer", "") or ""
                    if self.debug:
                        logger.debug(f"current_text_buffer: {current_text_buffer}")

                    if not current_text_buffer:
                        # 没新文本但 chunks 刚到：允许后续补发 citation（一般不会发生）
                        if not all_retrieved_chunks:
                            continue

                    # 尾部是半个引用/半个代码块：等下一帧
                    if danger_tail_pattern.search(current_text_buffer):
                        continue

                    # 从当前 buffer 中抽取所有 [ID:x] 引用
                    referenced_chunk_idxs = set()
                    for m in id_ref_pattern.finditer(current_text_buffer):
                        raw_ids = m.group(1)
                        ids = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
                        for idx in ids:
                            referenced_chunk_idxs.add(idx)

                    # 如果已经出现引用，但 chunks 还没到：先不吐字，避免 WebUI 显示 undefined
                    if referenced_chunk_idxs and not chunks_loaded:
                        continue

                    # 累计记录
                    for idx in referenced_chunk_idxs:
                        detected_ids.add(idx)
                        alloc_cite_no(idx)

                    # 将 [ID:x] / [IDs:x,y] 替换成 WebUI 可识别的连续编号 [1][2]…
                    def replace_id_refs(match):
                        raw_ids = match.group(1)
                        id_list = [int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit()]
                        if not id_list:
                            return match.group(0)
                        nums = [alloc_cite_no(i) for i in id_list]
                        # 多个引用用 [1][2] 的形式，最兼容
                        return "".join(f"[{n}]" for n in nums)

                    modified_answer = id_ref_pattern.sub(replace_id_refs, current_text_buffer)
                    current_cleaned_text = modified_answer.replace("```text", "").replace("```", "")

                    # 先发 citation event（按 cite_no 顺序），再发文本 delta
                    pending_citation_events = []
                    if chunks_loaded and all_retrieved_chunks:
                        # 只为“已经在文本中出现过”的 detected_ids 发 event
                        to_send = [idx for idx in detected_ids if idx not in sent_chunk_indices]
                        to_send = [idx for idx in to_send if 0 <= idx < len(all_retrieved_chunks)]
                        to_send.sort(key=lambda i: cite_map.get(i, 10**9))

                        for chunk_idx in to_send:
                            chunk = all_retrieved_chunks[chunk_idx]
                            title = str(chunk.get("document_name") or "Unknown")
                            url_content = chunk.get("url") or ""
                            raw_content = chunk.get("content") or chunk.get("content_with_weight") or ""
                            similarity = chunk.get("similarity", "")

                            # 显示内容：建议不要太长
                            display_content = truncate(raw_content, 900)
                            if similarity != "":
                                display_content = f"(similarity={similarity})\n{display_content}"

                            # 为了避免 OpenWebUI 显示 undefined：尽量同时提供 title/source/name
                            unique_name = f"{title} (chunk {chunk_idx})"

                            event_payload = {
                                "event": {
                                    "type": "citation",
                                    "data": {
                                        "document": [display_content],
                                        "metadata": [{
                                            "title": title,
                                            "source": title,
                                            "name": unique_name,
                                            "url": url_content,
                                        }],
                                        "source": {
                                            "title": title,
                                            "name": unique_name,
                                            "url": url_content,
                                        },
                                    },
                                }
                            }

                            pending_citation_events.append(event_payload)
                            sent_chunk_indices.add(chunk_idx)

                    if self.debug:
                        logger.debug(f"pending_citation_events: {pending_citation_events}")

                    for event in pending_citation_events:
                        yield event

                    # 再发文本 delta（避免重复输出）
                    lcp = longest_common_prefix(previous_cleaned_text, current_cleaned_text)
                    delta = current_cleaned_text[lcp:]
                    previous_cleaned_text = current_cleaned_text

                    if self.debug:
                        logger.debug(f"DEBUG: LCP length: {lcp}")
                        logger.debug(f"DEBUG: Delta: {delta}")

                    if delta:
                        yield delta

                except Exception as e:
                    logger.error(f"Error processing stream chunk: {e}")

        except Exception as e:
            logger.error(f"Pipeline Connection Error: {e}")
            yield f"**Pipeline Connection Error**: {e}"


if __name__ == "__main__":
    test_pipe = Pipeline()
    user_message = "在中国内陆水体中，小型水体为何是二氧化碳排放的热点？请解释其机理。"
    messages = []
    model_id = "ragflow_citation"
    body = {
        "stream": False,
        "model": "ragflow_citation",
        "messages": [],
        "user": {
            "name": "test_user",
            "id": "test_id",
            "email": "test@example.com",
            "role": "admin",
        },
    }

    generator = test_pipe.pipe(user_message, model_id, messages, body)

    print("--- 开始流式输出 ---")
    for chunk in generator:
        # print(chunk, end="\n", flush=True)
        print()
    print("\n--- 结束 ---")
