# -*- coding: utf-8 -*-
"""LLM tool for listing configured workflows."""

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..commands.comfyui import _format_workflow_input_requirements
from ..core import plugin as runtime


@dataclass
class ComfyUIListWorkflowsTool(FunctionTool[AstrAgentContext]):
    """查询当前可用的 ComfyUI 工作流列表及说明，供 LLM 选择工作流时使用。"""

    name: str = "comfyui_list_workflows"
    description: str = "列出所有可用的 ComfyUI 工作流名称及说明。参数要求以工作流说明为准。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        runtime.logger.info("[ComfyUI Tool] comfyui_list_workflows called with args: %s", kwargs)
        config = runtime._plugin_config
        if not config:
            return "插件配置不可用。"
        server_ip, _ = runtime._get_server_config(config)
        active_port = runtime._get_active_comfyui_port(config)
        wf_dir = runtime._get_workflow_dir()
        workflows = runtime._filter_workflows_for_port(runtime._list_workflows_in_configured_dir(wf_dir), active_port)
        workflows = [w for w in workflows if runtime._workflow_is_available(w, workflows)]
        if not workflows:
            return f"当前 ComfyUI 接口「{active_port['name']}」没有可用工作流。请使用 /comfyui_port 切换接口，或调整该接口的可用工作流配置。"
        
        lines = [
            f"Current ComfyUI: {active_port['name']} ({server_ip})",
            "调用规则：texts、image_urls、videos 均按编号顺序传入；跳过可缺省输入时必须传空字符串占位，例如 texts:[\"正向词\", \"\", \"赛博朋克\"] 或 image_urls:[\"\", \"https://...\"]。",
            "Available workflows:",
        ]
        for w in workflows:
            name = w["name"]
            lines.append("")
            lines.append(f"Workflow: {name}")
            lines.append(_format_workflow_input_requirements(w))
        
        return "\n".join(lines)


__all__ = ["ComfyUIListWorkflowsTool"]
