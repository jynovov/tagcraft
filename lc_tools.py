# -*- coding: utf-8 -*-
"""可选的 LangChain 适配层。

只依赖 langchain-core（不引入 langchain / langgraph）：
  · 用 @tool 从 Python 函数签名自动生成工具 schema，替代手写 AGENT_TOOLS
  · 用 Pydantic 模型校验模型最终输出的 JSON（等价于结构化输出的校验部分）

没装 langchain-core 时 HAS_LC 为 False，server.py 自动退回原来的实现，
所以零依赖环境（教师包、离线机器）不受影响。
"""

try:
    from pydantic import BaseModel, ConfigDict, Field
    from langchain_core.tools import StructuredTool
    from langchain_core.utils.function_calling import convert_to_openai_tool

    import langchain_core

    VERSION = getattr(langchain_core, "__version__", "?")
    HAS_LC = True
except Exception:  # noqa: BLE001 - 缺依赖时静默降级
    HAS_LC = False
    VERSION = ""
    BaseModel = object


if HAS_LC:

    class TagResult(BaseModel):
        """一次生成结果的最终结构。"""

        model_config = ConfigDict(extra="ignore")

        positive: list[str] = Field(default_factory=list)
        negative: list[str] = Field(default_factory=list)
        notes: str = ""


def build_payload(funcs):
    """把 (工具名, 带类型注解和 docstring 的函数) 变成 OpenAI tools 载荷。"""
    if not HAS_LC:
        return None
    out = []
    for name, fn in funcs:
        st = StructuredTool.from_function(
            func=fn, name=name, description=(fn.__doc__ or "").strip())
        out.append(convert_to_openai_tool(st))
    return out


def validate_tag_dict(data):
    """校验并规范化 tag 结果；不合法时返回 None，让上层触发重写。"""
    if not HAS_LC or not isinstance(data, dict):
        return data
    # 兼容模型偶尔返回字符串数组而不是对象的情况
    try:
        return TagResult.model_validate(data).model_dump()
    except Exception:  # noqa: BLE001
        return None
