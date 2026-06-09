# -*- coding: utf-8 -*-
"""LLM tool for checking ComfyUI queue status."""

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core import plugin as runtime


@dataclass
class ComfyUIStatusTool(FunctionTool[AstrAgentContext]):
    """
    查询 ComfyUI 队列状态。
    查询运行中/等待中的任务数量；任务结果等待由 comfyui_query_wait 通过 WebSocket 处理。
    """

    name: str = "comfyui_status"
    description: str = "查询 ComfyUI 队列状态，包括运行中/等待中的任务数量。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        runtime.logger.info("[ComfyUI Tool] comfyui_status called with args: %s", kwargs)
        config = runtime._plugin_config
        if not config:
            return "插件配置不可用。"
        wait_threshold = runtime._get_wait_threshold(config)
        server_ip, _ = runtime._get_server_config(config)
        session_key = runtime._get_session_key(context.context)
        pending = runtime._session_pending.get(session_key) or runtime._session_pending.get("default")
        if pending and pending.get("prompt_id") and server_ip:
            output_rules = pending.get("output_rules")
            remaining = await runtime._estimate_remaining_seconds(server_ip, pending["prompt_id"])
            if remaining == 0:
                url, ftype, texts = await runtime._get_result_for_prompt(server_ip, pending["prompt_id"], output_rules)
                texts = runtime._filter_generated_texts_for_delivery(texts)
                for k in list(runtime._session_pending.keys()):
                    if runtime._session_pending.get(k) == pending:
                        runtime._session_pending.pop(k, None)
                runtime._task_registry.pop(pending.get("prompt_id"), None)
                if isinstance(url, dict):
                    images = url.get("images") or []
                    videos = url.get("videos") or []
                    if images:
                        runtime._session_image_url_queue.setdefault(session_key, []).extend(images)
                    if videos:
                        runtime._session_video_url_queue.setdefault(session_key, []).extend(videos)
                    media_text = " Media is queued for automatic sending by the plugin. Reply with normal text only." if (images or videos) else ""
                    return f"Task completed. Output: {ftype}.{media_text} Queue: 0 running, 0 pending."
                if url:
                    if ftype == "image":
                        if runtime._is_local_image_url(url, server_ip):
                            runtime._session_image_url_queue.setdefault(session_key, []).append(url)
                            return (
                                "Task completed. Output: image. Image is queued for automatic sending by the plugin. "
                                "Do NOT call send_message_to_user with image_url, do NOT use a markdown image URL. "
                                "Reply with normal text only. Queue: 0 running, 0 pending."
                            )
                        session_id = runtime._get_session_id_from_context(context.context)
                        if session_id:
                            await runtime._send_image_to_session(session_id, url, "图好了～")
                        return "Task completed. Output: image. Image has been sent to the user. Queue: 0 running, 0 pending."
                    if ftype == "video":
                        runtime._session_video_url_queue.setdefault(session_key, []).append(url)
                        return (
                            "Task completed. Output: video. Video is queued for automatic sending by the plugin. "
                            "Do NOT call send_message_to_user with video_url. Reply with normal text only. Queue: 0 running, 0 pending."
                        )
                    return f"Task completed. Output: {ftype}. Queue: 0 running, 0 pending."
                return "Task completed (no output file). Queue: 0 running, 0 pending."
            if remaining < wait_threshold:
                client_id = pending.get("client_id", "")
                url, ftype, texts = await runtime._wait_for_completion(
                    server_ip, client_id, pending["prompt_id"], timeout=remaining + 120, output_rules=output_rules
                )
                texts = runtime._filter_generated_texts_for_delivery(texts)
                for k in list(runtime._session_pending.keys()):
                    if runtime._session_pending.get(k) == pending:
                        runtime._session_pending.pop(k, None)
                runtime._task_registry.pop(pending.get("prompt_id"), None)
                if isinstance(url, dict):
                    images = url.get("images") or []
                    videos = url.get("videos") or []
                    if images:
                        runtime._session_image_url_queue.setdefault(session_key, []).extend(images)
                    if videos:
                        runtime._session_video_url_queue.setdefault(session_key, []).extend(videos)
                    media_text = " Media is queued for automatic sending by the plugin. Reply with normal text only." if (images or videos) else ""
                    return f"Task completed. Output: {ftype}.{media_text} Queue: 0 running, 0 pending."
                if url:
                    if ftype == "image":
                        if runtime._is_local_image_url(url, server_ip):
                            runtime._session_image_url_queue.setdefault(session_key, []).append(url)
                            return (
                                "Task completed. Output: image. Image is queued for automatic sending by the plugin. "
                                "Do NOT call send_message_to_user with image_url, do NOT use a markdown image URL. "
                                "Reply with normal text only. Queue: 0 running, 0 pending."
                            )
                        session_id = runtime._get_session_id_from_context(context.context)
                        if session_id:
                            await runtime._send_image_to_session(session_id, url, "图好了～")
                        return "Task completed. Output: image. Image has been sent to the user. Queue: 0 running, 0 pending."
                    if ftype == "video":
                        runtime._session_video_url_queue.setdefault(session_key, []).append(url)
                        return (
                            "Task completed. Output: video. Video is queued for automatic sending by the plugin. "
                            "Do NOT call send_message_to_user with video_url. Reply with normal text only. Queue: 0 running, 0 pending."
                        )
                    return f"Task completed. Output: {ftype}. Queue: 0 running, 0 pending."
                return "Task finished. Queue: 0 running, 0 pending."
            await runtime.asyncio.sleep(wait_threshold)
            running, pending_count = await runtime._get_queue_status(server_ip)
            remaining_after = await runtime._estimate_remaining_seconds(server_ip, pending["prompt_id"])
            return (
                f"ComfyUI queue: {running} running, {pending_count} pending. "
                f"Your task estimated remaining: about {remaining_after} seconds. Call again to re-check."
            )
        running, pending_count = await runtime._get_queue_status(server_ip)
        if running < 0:
            return "ComfyUI server unreachable. Please check server_ip and network."
        return f"ComfyUI queue: {running} running, {pending_count} pending."


__all__ = ["ComfyUIStatusTool"]
