# -*- coding: utf-8 -*-
"""LLM tool for submitting ComfyUI workflow tasks."""

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core import plugin as runtime


@dataclass
class ComfyUIExecuteTool(FunctionTool[AstrAgentContext]):
    """
    鎵ц鎸囧畾鐨?ComfyUI 宸ヤ綔娴併€傚伐浣滄祦鍚嶇О闇€涓?list_workflows 杩斿洖鐨?name 涓€鑷淬€?
    鏂囨湰鍙傛暟閫氳繃 texts 浼犲叆锛涘浘鐗囦粠褰撳墠浼氳瘽娑堟伅涓嚜鍔ㄦ彁鍙栵紱鑻ュ伐浣滄祦闇€瑕佸浘鑰屾秷鎭棤鍥撅紝鍙紶 image_urls锛堝崰浣嶇锛夛紝鎻掍欢浼氫笅杞藉苟杞?base64 娉ㄥ叆銆?
    鈿狅笍 閲嶈锛氬鏋滈渶瑕佺敓鎴愬寮犲浘鐗囷紙濡?N 寮狅級锛屽繀椤昏皟鐢ㄦ湰宸ュ叿 N 娆★紙姣忔鐢熸垚涓€寮狅級锛屾墍鏈変换鍔′細骞惰鎵ц銆?
    姣忔璋冪敤浼氳繑鍥炰竴涓?task_id锛屼箣鍚庣敤 comfyui_query_wait锛堜紶鍏?session_tag锛夋壒閲忔煡璇㈡墍鏈変换鍔＄殑缁撴灉銆?
    """

    name: str = "comfyui_execute"
    description: str = (
        "Execute a ComfyUI workflow task. After submitting, you MUST immediately call comfyui_query_wait "
        "with the returned task_id/session_tag and must not reply to the user before waiting for the result. "
        "Generated media will be sent automatically by the plugin after comfyui_query_wait completes."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workflow_name": {
                    "type": "string",
                    "description": "Exact workflow name (e.g. from comfyui_list_workflows).",
                },
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Text inputs for the workflow. Content must follow the workflow description from comfyui_list_workflows.",
                },
                "videos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of video filenames (.mp4) on server for video workflows.",
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Image source(s) when message has none. Prefer HTTP URL or local path (plugin data dir or data/agent/comfyui/input). Do not paste raw base64.",
                },
                "session_tag": {
                    "type": "string",
                    "description": "REQUIRED. The sender's QQ number (the person who sent the command). This is used to track all tasks for this user. Example: '123456789'. Do not use your own QQ number, use the sender's QQ number.",
                },
            },
            "required": ["workflow_name", "session_tag"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        # 鏃ュ織鑴辨晱锛氫笉杈撳嚭 base64锛岄伩鍏嶈繘鍏?LLM 鎴栨棩蹇楃暀瀛?
        runtime.logger.info(
            "[ComfyUI Tool] comfyui_execute called: workflow_name=%r, texts=%r, videos=%r, image_urls=%s",
            kwargs.get("workflow_name"),
            kwargs.get("texts"),
            kwargs.get("videos"),
            runtime._sanitize_image_urls_for_log(kwargs.get("image_urls")),
        )
        workflow_name = (kwargs.get("workflow_name") or "").strip()
        texts_raw = kwargs.get("texts") or []
        texts = [str(t) for t in (texts_raw if isinstance(texts_raw, list) else [texts_raw])]
        videos_raw = kwargs.get("videos") or []
        videos = [str(v) for v in (videos_raw if isinstance(videos_raw, list) else [videos_raw])]
        image_urls_arg = kwargs.get("image_urls") or []
        # 鑷姩鑾峰彇鍙戦€佽€呯殑 QQ 鍙蜂綔涓?session_tag
        sender_id = runtime._get_sender_id_from_context(context.context)
        session_tag = (kwargs.get("session_tag") or "").strip()
        if not session_tag and sender_id:
            session_tag = sender_id
            runtime.logger.info("[ComfyUI Tool] Auto-filled session_tag with sender_id: %s", session_tag)
        if isinstance(image_urls_arg, str):
            image_urls_arg = [image_urls_arg]
        image_urls_arg = [str(u) for u in image_urls_arg if isinstance(u, str)]
        if not workflow_name:
            return "缺少工作流名称。"
        if not session_tag:
            return "无法识别发送者标识，无法登记 ComfyUI 任务。"
        config = runtime._plugin_config
        if not config:
            return "插件配置不可用。"
        server_ip, client_id = runtime._get_server_config(config)
        wf_dir = runtime._get_workflow_dir()
        ctx = getattr(context.context, "context", None)
        event = getattr(ctx, "event", None) if ctx else None
        submit = await runtime._submit_comfyui_workflow(
            context.context,
            workflow_name,
            texts,
            videos,
            image_urls_arg,
            session_tag,
            event,
        )
        if not submit.get("ok"):
            return submit.get("message", "执行失败。")
        all_uuids = submit.get("all_task_ids", [])
        uuid_list_str = ", ".join(f'"{u}"' for u in all_uuids)
        prompt_id = submit["prompt_id"]
        return (
            f"Workflow '{workflow_name}' submitted. Task ID (prompt_id): {prompt_id}. "
            f"You have {len(all_uuids)} task(s) with session_tag '{session_tag}'. All task IDs: [{uuid_list_str}]. "
            f"IMPORTANT: You MUST immediately call comfyui_query_wait with session_tag='{session_tag}' and task_ids=['{prompt_id}'] to wait for the result. "
            "Do not reply to the user before calling comfyui_query_wait."
            + submit.get("desc_reminder", "")
        )

__all__ = ["ComfyUIExecuteTool"]
