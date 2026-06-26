"""
差旅出行助手 — 独立 Agent 入口
================================

【首次环境配置】
  cd travel_assistant
  python -m venv .venv
  .venv\\Scripts\\activate          # Windows
  pip install -r requirements.txt
  # 配置 .env：DASHSCOPE_API_KEY、MODEL、OPENWEATHER_API_KEY

【日常启动 — 前端模式（推荐）】
  终端1：python travel_agent.py --api    # http://localhost:8001
  终端2：cd front/mcp_agent && npm run dev   # http://localhost:5173
  浏览器打开前端，注册/登录后与「差旅出行助手」对话

【Docker / Sealos 部署】
  见 deploy/sealos.md；生产环境需配置 JWT_SECRET、DATA_DIR=/app/data

【CLI 调试（可选）】
  python travel_agent.py

【原通用 Agent（可选，与差旅互不影响）】
  python api_server.py   # :8000
  python client.py
"""

import argparse
import asyncio
import contextvars
import json
import logging
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_community.chat_models import ChatTongyi
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent

import app_paths
import auth
import conversation_store
import date_context
import user_preference

# 当前会话 thread_id（LangGraph checkpoint）与 user_id（偏好/文件）
_current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "thread_id", default="travel-1"
)
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default="cli-user"
)
# 当前轮用户消息，供偏好写入校验
_current_user_message: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_message", default=""
)

OUTPUT_DIR = app_paths.OUTPUT_DIR
TRAVEL_PROMPT_PATH = Path("travel_prompt.txt")
CLI_USER_ID = "cli-user"

# Agent 自动续跑：检测 LLM 提前收尾的「请稍候」类话术
CONTINUE_PROMPT = "请继续完成尚未完成的查询，直接输出完整行程计划。不要再说请稍候或稍后回复。"
INTERIM_MARKERS = ("请稍候", "30秒", "稍后为您", "正在查询中", "完成后为您", "预计30秒", "马上为您")
MAX_AUTO_CONTINUE = 2
RECURSION_LIMIT = 50
MAX_PREFERENCE_VALUE_LEN = 80

# 工具名 → 前端进度文案
TOOL_STATUS_LABELS: Dict[str, str] = {
    "save_user_preference": "正在保存偏好…",
    "delete_user_preference": "正在删除偏好…",
    "write_travel_plan": "正在保存行程…",
    "query_weather": "正在查询天气…",
    "maps_around_search": "正在搜索周边酒店…",
    "maps_direction_driving": "正在规划路线…",
    "maps_text_search": "正在搜索地点…",
    "maps_search_detail": "正在查询地点详情…",
    "get-current-date": "正在获取日期…",
    "get-stations-code-in-city": "正在查询车站…",
    "get-station-code-of-citys": "正在查询车站…",
    "get-station-code-by-names": "正在查询车站…",
    "get-station-by-telecode": "正在查询车站…",
    "get-tickets": "正在查询车次…",
    "get-interline-tickets": "正在查询中转车次…",
    "get-train-route-stations": "正在查询经停站…",
}

HOTEL_BRANDS = (
    "汉庭", "全季", "如家", "亚朵", "锦江之星", "7天", "维也纳",
    "希尔顿", "万豪", "洲际", "华住", "桔子", "麗枫", "宜必思",
)

# 发散词：若出现在 value 但不在用户原话中，拒绝写入
DIVERGENCE_KEYWORDS = (
    "紫外线消毒", "高分床品", "智能马桶", "非遗传承人", "非遗工坊",
    "梅子酒", "手写食谱", "打卡式", "标准化微笑", "医疗级",
    "臭氧消毒", "窗景天花板", "阿妈", "乳扇",
)

# ────────────────────────────
# 环境配置
# ────────────────────────────


class Configuration:
    """读取 .env 与 servers_config.json"""

    def __init__(self) -> None:
        load_dotenv()
        self.api_key: str = os.getenv("DASHSCOPE_API_KEY") or ""
        self.model: str = os.getenv("MODEL") or "qwen-plus"
        if not self.api_key:
            raise ValueError("❌ 未找到 DASHSCOPE_API_KEY，请在 .env 中配置")

    @staticmethod
    def load_servers(file_path: str = "servers_config.json") -> Dict[str, Any]:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f).get("mcpServers", {})


def load_base_prompt() -> str:
    """加载差旅助手基础提示词。"""
    with open(TRAVEL_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def build_system_content(base_prompt: str, user_id: str) -> str:
    """组装完整 System Prompt：基础 + 日期上下文 + 用户偏好。"""
    parts = [
        base_prompt,
        "---",
        date_context.get_date_context(),
        "---",
        user_preference.get_preference_context(user_id),
    ]
    return "\n\n".join(parts)


# ────────────────────────────
# 本地 LangChain 工具
# ────────────────────────────


def _validate_preference(key: str, value: str) -> Optional[str]:
    """校验偏好 key/value，失败返回错误信息。"""
    if key not in user_preference.ALLOWED_PREFERENCE_KEYS:
        return f"⚠️ 不支持的偏好类型「{key}」，请使用白名单 key"

    value = value.strip()
    if not value:
        return "⚠️ 偏好内容不能为空"

    if len(value) > MAX_PREFERENCE_VALUE_LEN:
        return f"⚠️ 偏好描述过长（>{MAX_PREFERENCE_VALUE_LEN}字），请用简短原话"

    user_msg = _current_user_message.get() or ""
    for keyword in DIVERGENCE_KEYWORDS:
        if keyword in value and keyword not in user_msg:
            return "⚠️ 偏好包含用户未明确提到的内容，请仅记录用户原话"

    return None


def _save_user_preference(key: str, value: str) -> str:
    """保存用户偏好到 SQLite。"""
    err = _validate_preference(key, value)
    if err:
        logging.warning("偏好校验拒绝: key=%s, value=%s", key, value)
        return err

    user_id = _current_user_id.get()
    ok = user_preference.save_preference(user_id, key, value.strip())
    if ok:
        logging.info("✅ 偏好已保存: user_id=%s, %s=%s", user_id, key, value)
        return f"✅ 已记住您的偏好：{key} = {value.strip()}"
    logging.warning("⚠️ 偏好保存失败: user_id=%s, %s=%s", user_id, key, value)
    return "⚠️ 偏好保存失败（数据库可能被其他程序占用，请关闭 Navicat 等工具后重试）"


def _delete_user_preference(key: str) -> str:
    """删除用户偏好。"""
    if key not in user_preference.ALLOWED_PREFERENCE_KEYS:
        return f"⚠️ 不支持的偏好类型「{key}」，请使用白名单 key"

    user_id = _current_user_id.get()
    if not user_preference.get_preference(user_id, key):
        label = user_preference.PREFERENCE_LABELS.get(key, key)
        return f"⚠️ 暂无「{label}」相关偏好记录"

    if user_preference.delete_preference(user_id, key):
        logging.info("✅ 偏好已删除: user_id=%s, key=%s", user_id, key)
        label = user_preference.PREFERENCE_LABELS.get(key, key)
        return f"✅ 已删除偏好：{label}"
    return "⚠️ 偏好删除失败"


def _user_output_dir() -> Path:
    user_id = _current_user_id.get()
    out = OUTPUT_DIR / user_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _sanitize_filename(name: str) -> str:
    """去除 Windows 文件名非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', "", name).strip() or "行程"


def _extract_destination_from_plan(content: str) -> str:
    """从行程 Markdown 提取目的地城市名。"""
    route_match = re.search(
        r"[\u4e00-\u9fff]+[→\-][\u4e00-\u9fff]+", content[:800]
    )
    if route_match:
        parts = re.split(r"[→\-]", route_match.group(0))
        if len(parts) >= 2:
            dest = parts[-1].strip()
            dest = re.sub(r"[·\s].*", "", dest)
            if dest:
                return dest

    dest_match = re.search(
        r"目的地[：:]\s*([\u4e00-\u9fff]{2,8})|"
        r"([\u4e00-\u9fff]{2,8})\s*[·•]\s*\d+日",
        content[:800],
    )
    if dest_match:
        return dest_match.group(1) or dest_match.group(2)

    title_match = re.search(r"^#\s*.*?([\u4e00-\u9fff]{2,8})", content, re.M)
    if title_match:
        return title_match.group(1)

    return "行程"


def _extract_action_from_plan(content: str) -> str:
    """判断出差或旅行。"""
    head = content[:500]
    if "出差" in head:
        return "出差"
    if any(kw in head for kw in ("旅行", "攻略", "游玩", "深度游")):
        return "旅行"
    return "行程"


def _build_trip_filename(content: str, output_dir: Path) -> str:
    """生成文件名：{目的地}{动作}_{YYYYMMDD}_{HHMM}.md"""
    dest = _sanitize_filename(_extract_destination_from_plan(content))
    action = _extract_action_from_plan(content)
    time_part = datetime.now().strftime("%Y%m%d_%H%M")
    base = f"{dest}{action}_{time_part}.md"

    if not (output_dir / base).exists():
        return base

    for i in range(2, 100):
        candidate = f"{dest}{action}_{time_part}_{i}.md"
        if not (output_dir / candidate).exists():
            return candidate
    return base


def _extract_preferences_from_plan(content: str) -> Dict[str, str]:
    """从行程 Markdown 提取客观偏好（不含语义类 travel_style 等）。"""
    prefs: Dict[str, str] = {}

    for brand in HOTEL_BRANDS:
        if brand in content:
            prefs["hotel_brand"] = brand
            break

    budget_match = re.search(r"¥(\d+)\s*[-~–]\s*¥?(\d+)", content)
    if not budget_match:
        budget_match = re.search(r"(\d+)\s*[-~–]\s*(\d+)\s*元\s*/?\s*晚", content)
    if budget_match:
        prefs["budget_range"] = f"{budget_match.group(1)}-{budget_match.group(2)}元/晚"

    score_match = re.search(r"(\d+\.?\d*)\s*分", content)
    if score_match:
        prefs["hotel_quality"] = f"评分{score_match.group(1)}分以上"

    route_match = re.search(r"[\u4e00-\u9fff]+[→\-]([\u4e00-\u9fff]+)", content[:800])
    if route_match:
        dest = route_match.group(1).strip()
        dest = re.sub(r"[·\s（(].*", "", dest)
        if dest and len(dest) <= 10:
            prefs["last_destination"] = dest

    transport_match = re.search(r"##\s*[✈️🚄]\s*(航班|高铁|火车)", content)
    if transport_match:
        prefs["transport_mode"] = transport_match.group(1)

    return prefs


def _sync_preferences_from_plan(content: str) -> None:
    """保存行程后自动同步客观偏好到 SQLite。"""
    user_id = _current_user_id.get()
    extracted = _extract_preferences_from_plan(content)
    for key, value in extracted.items():
        if user_preference.save_preference(user_id, key, value):
            logging.info("✅ 行程偏好已同步: user_id=%s, %s=%s", user_id, key, value)


def _write_travel_plan(content: str) -> str:
    """将行程单保存为 Markdown 文件。"""
    output_dir = _user_output_dir()
    filename = _build_trip_filename(content, output_dir)
    filepath = output_dir / filename
    try:
        filepath.write_text(content, encoding="utf-8")
        _sync_preferences_from_plan(content)
        return (
            f"✅ 行程单已保存：{filename}\n"
            "请向用户确认保存成功，并提示通过页面左下角「历史文件」查看/下载。\n"
            "禁止承诺发送微信/邮件、导出 PDF/Excel、生成可视化地图等未实现能力。"
        )
    except OSError as e:
        return f"⚠️ 保存失败：{e}"


def create_local_tools() -> list:
    """创建偏好保存与行程写入工具。"""
    save_tool = StructuredTool.from_function(
        func=_save_user_preference,
        name="save_user_preference",
        description=(
            "保存用户的差旅/旅行偏好。用户一旦表达偏好时必须立即调用。"
            "key 必须为白名单：travel_style, accommodation, cuisine_preference, "
            "hotel_brand, hotel_quality, room_view, budget_range, departure_time, "
            "transport_mode, last_destination。"
            "value 必须为用户原话或轻度概括，禁止扩写。"
        ),
    )
    delete_tool = StructuredTool.from_function(
        func=_delete_user_preference,
        name="delete_user_preference",
        description=(
            "删除用户已保存的单条偏好。当用户说取消/删除/忘掉某偏好时必须调用。"
            "key 必须为白名单：travel_style, accommodation, cuisine_preference, "
            "hotel_brand, hotel_quality, room_view, budget_range, departure_time, "
            "transport_mode, last_destination。"
        ),
    )
    write_tool = StructuredTool.from_function(
        func=_write_travel_plan,
        name="write_travel_plan",
        description=(
            "将行程规划保存为 Markdown 文件。"
            "当用户说保存/确认保存/直接生成行程单，且对话中已有可保存的行程内容时立即调用。"
            "参数 content 为完整的 Markdown 格式行程单。"
            "保存成功后勿向用户推销未实现功能（如发微信、PDF、可视化地图）。"
        ),
    )
    return [save_tool, delete_tool, write_tool]


def make_dynamic_prompt(base_prompt: str):
    """每次调用前注入最新日期与偏好到系统消息。"""

    def dynamic_prompt(state: dict) -> list:
        user_id = _current_user_id.get()
        system_content = build_system_content(base_prompt, user_id)
        return [SystemMessage(content=system_content)] + state["messages"]

    return dynamic_prompt


def ensure_output_dir(user_id: str) -> Path:
    """确保用户 output 目录存在。"""
    out = OUTPUT_DIR / user_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_safe_filepath(filename: str, user_id: str) -> Path:
    """校验文件名并返回安全路径。"""
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise ValueError("非法文件名")
    filepath = ensure_output_dir(user_id) / safe_name
    if not filepath.is_file():
        raise FileNotFoundError("文件不存在")
    return filepath


def _attachment_disposition(filename: str) -> str:
    """生成支持中文文件名的 Content-Disposition（HTTP 头仅允许 latin-1）。"""
    try:
        filename.encode("latin-1")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        encoded = quote(filename, safe="")
        return f'attachment; filename="download"; filename*=UTF-8\'\'{encoded}'


def is_interim_response(content: str) -> bool:
    """判断回复是否为「请稍候」类中间状态。"""
    if not content:
        return False
    return any(marker in content for marker in INTERIM_MARKERS)


def _tool_status_message(tool_name: str) -> str:
    """将工具名转为用户可读的进度文案。"""
    if tool_name in TOOL_STATUS_LABELS:
        return TOOL_STATUS_LABELS[tool_name]
    name = tool_name.lower()
    if any(k in name for k in ("ticket", "12306", "interline")):
        return "正在查询车次…"
    if any(k in name for k in ("station", "train")):
        return "正在查询车站…"
    if "flight" in name or "variflight" in name:
        return "正在查询航班…"
    if tool_name.startswith("maps_"):
        return "正在查询地图…"
    if "weather" in name:
        return "正在查询天气…"
    if "date" in name:
        return "正在获取日期…"
    return "正在处理查询…"


def _tool_done_message(tool_name: str) -> str:
    """工具完成时的思考行文案。"""
    start = _tool_status_message(tool_name)
    if start.endswith("…"):
        return start[:-1] + "完成"
    return f"{start}完成"


def _extract_chunk_text(chunk: Any) -> str:
    """从流式 chunk 中提取文本。"""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _extract_final_content(output: Any) -> str:
    """从 chain output 提取最终 AI 回复。"""
    if isinstance(output, dict):
        messages = output.get("messages", [])
        if messages:
            return messages[-1].content or ""
    return ""


# ────────────────────────────
# Agent 初始化
# ────────────────────────────

checkpointer = None
_checkpointer_cm = None
mcp_client: Optional[MultiServerMCPClient] = None
agent = None


async def init_agent() -> None:
    """连接 MCP 并创建差旅 Agent。"""
    global mcp_client, agent, checkpointer, _checkpointer_cm

    app_paths.ensure_data_dirs()
    user_preference.init_preference_schema()
    auth.init_auth_schema()
    conversation_store.init_conversation_schema()

    cfg = Configuration()
    os.environ["DASHSCOPE_API_KEY"] = cfg.api_key
    servers_cfg = Configuration.load_servers()

    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(app_paths.CHECKPOINTS_DB))
    checkpointer = await _checkpointer_cm.__aenter__()
    await checkpointer.setup()

    mcp_client = MultiServerMCPClient(servers_cfg)
    mcp_tools = await mcp_client.get_tools()
    local_tools = create_local_tools()
    all_tools = mcp_tools + local_tools

    logging.info(f"✅ 已加载 {len(mcp_tools)} 个 MCP 工具 + {len(local_tools)} 个本地工具")
    logging.info(f"   工具列表：{[t.name for t in all_tools]}")

    # 通义千问 streaming=True 在 tool_calls 场景会触发 IndexError
    model = ChatTongyi(model=cfg.model, streaming=False)
    base_prompt = load_base_prompt()

    agent = create_react_agent(
        model=model,
        tools=all_tools,
        prompt=make_dynamic_prompt(base_prompt),
        checkpointer=checkpointer,
    )


async def _run_agent_once(
    message: str,
    thread_id: str,
    on_event: Optional[Any] = None,
) -> str:
    """执行一轮 Agent，可选回调 SSE 事件。"""
    run_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    inputs = {"messages": [HumanMessage(content=message)]}
    final_content = ""

    async for event in agent.astream_events(inputs, run_config, version="v2"):
        kind = event.get("event", "")
        name = event.get("name", "")

        if kind == "on_tool_start" and on_event:
            msg = _tool_status_message(name)
            await on_event({"type": "tool_start", "tool": name, "message": msg})
        elif kind == "on_tool_end" and on_event:
            msg = _tool_done_message(name)
            await on_event({"type": "tool_end", "tool": name, "message": msg})
        elif kind == "on_chat_model_end":
            output = event.get("data", {}).get("output")
            if output:
                text = _extract_chunk_text(output)
                if text:
                    final_content = text
        elif kind == "on_chain_end":
            output = event.get("data", {}).get("output")
            text = _extract_final_content(output)
            if text:
                final_content = text

    return final_content


async def invoke_agent(
    message: str,
    thread_id: str,
    user_id: str,
) -> str:
    """调用 Agent 并返回回复文本；若检测到中间状态则自动续跑。"""
    if agent is None:
        raise RuntimeError("Agent 未初始化")

    _current_thread_id.set(thread_id)
    _current_user_id.set(user_id)
    _current_user_message.set(message)
    content = await _run_agent_once(message, thread_id)

    for i in range(MAX_AUTO_CONTINUE):
        if not is_interim_response(content):
            break
        logging.info(f"检测到中间状态回复，自动续跑 ({i + 1}/{MAX_AUTO_CONTINUE})")
        content = await _run_agent_once(CONTINUE_PROMPT, thread_id)

    return content


async def stream_agent(
    message: str,
    thread_id: str,
    user_id: str,
) -> AsyncIterator[Dict[str, Any]]:
    """流式推送 Agent 进度（SSE 数据源）。"""
    if agent is None:
        raise RuntimeError("Agent 未初始化")

    _current_thread_id.set(thread_id)
    _current_user_id.set(user_id)
    _current_user_message.set(message)
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(payload: Dict[str, Any]) -> None:
        await queue.put(payload)

    async def run() -> None:
        try:
            await queue.put({"type": "status", "message": "正在分析您的需求…"})
            content = await _run_agent_once(message, thread_id, on_event=on_event)

            for i in range(MAX_AUTO_CONTINUE):
                if not is_interim_response(content):
                    break
                await queue.put({"type": "status", "message": "正在继续完成查询…"})
                content = await _run_agent_once(CONTINUE_PROMPT, thread_id, on_event=on_event)

            await queue.put({
                "type": "done",
                "content": content,
                "status": "success" if content else "empty",
            })
        except Exception as exc:
            logging.error(traceback.format_exc())
            await queue.put({
                "type": "error",
                "error": str(exc),
                "status": "error",
            })
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        await task


async def cleanup() -> None:
    """释放 MCP 连接与 checkpoint。"""
    global mcp_client, checkpointer, _checkpointer_cm
    if mcp_client is not None:
        await mcp_client.cleanup()
        mcp_client = None
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None
        checkpointer = None


def extract_ui_messages(state: Any) -> List[Dict[str, str]]:
    """从 checkpoint state 提取可展示的 user/ai 消息。"""
    if not state or not getattr(state, "values", None):
        return []
    result: List[Dict[str, str]] = []
    for msg in state.values.get("messages", []):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                result.append({"role": "user", "content": content})
        elif isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            if isinstance(msg.content, list):
                parts = []
                for item in msg.content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and item.get("text"):
                        parts.append(item["text"])
                content = "".join(parts)
            if content and content.strip():
                result.append({"role": "ai", "content": content})
    return result


async def get_thread_messages(thread_id: str) -> List[Dict[str, str]]:
    """读取指定 thread 的对话历史。"""
    if agent is None or checkpointer is None:
        return []
    config = {"configurable": {"thread_id": thread_id}}
    state = await agent.aget_state(config)
    return extract_ui_messages(state)


async def delete_thread_checkpoint(thread_id: str) -> None:
    """删除 LangGraph checkpoint 中的 thread。"""
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(thread_id)


# ────────────────────────────
# CLI 模式
# ────────────────────────────


async def run_chat_loop() -> None:
    """CLI 交互循环。"""
    await init_agent()
    thread_id = "travel-1"
    user_id = CLI_USER_ID

    print("\n🧳 差旅出行助手已启动，输入 'quit' 退出")
    print(f"   会话 ID：{thread_id}（CLI 模式 user_id={user_id}）")
    print(f"   今天：{date_context.get_date_context().splitlines()[1]}\n")

    try:
        while True:
            user_input = input("你: ").strip()
            if user_input.lower() == "quit":
                break
            if not user_input:
                continue
            try:
                reply = await invoke_agent(user_input, thread_id, user_id)
                print(f"\n助手: {reply}\n")
            except Exception as exc:
                print(f"\n⚠️ 出错: {exc}\n")
    finally:
        await cleanup()
        print("🧹 资源已清理，Bye!")


# ────────────────────────────
# API 模式
# ────────────────────────────


def run_api(host: str = "0.0.0.0", port: int = 8001) -> None:
    """启动 FastAPI 服务。"""
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse
    from pydantic import BaseModel

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_agent()
        yield
        await cleanup()

    app = FastAPI(lifespan=lifespan, title="差旅出行助手 API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class ChatRequest(BaseModel):
        message: str
        thread_id: str

    class ChatResponse(BaseModel):
        content: str
        status: str = "success"
        error: Optional[str] = None

    class FileInfo(BaseModel):
        name: str
        size: int
        modified_at: str

    class FileContent(BaseModel):
        name: str
        content: str

    class PreferencesResponse(BaseModel):
        user_id: str
        preferences: Dict[str, str]
        count: int
        source: str = "preferences.db (SQLite)"

    class ConversationItem(BaseModel):
        thread_id: str
        title: str
        created_at: str
        updated_at: str

    class ConversationCreateResponse(BaseModel):
        thread_id: str
        title: str

    class MessagesResponse(BaseModel):
        thread_id: str
        messages: List[Dict[str, str]]

    def _verify_thread(thread_id: str, user: auth.UserInfo) -> None:
        try:
            conversation_store.require_conversation(thread_id, user.user_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="对话不存在或无权访问")

    @app.post("/auth/register", response_model=auth.AuthResponse)
    async def register(request: auth.RegisterRequest):
        user = auth.register_user(request.email, request.password)
        token = auth.create_access_token(user.user_id, user.email)
        return auth.AuthResponse(
            access_token=token,
            user_id=user.user_id,
            email=user.email,
        )

    @app.post("/auth/login", response_model=auth.AuthResponse)
    async def login(request: auth.LoginRequest):
        user = auth.authenticate_user(request.email, request.password)
        if not user:
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        token = auth.create_access_token(user.user_id, user.email)
        return auth.AuthResponse(
            access_token=token,
            user_id=user.user_id,
            email=user.email,
        )

    @app.get("/auth/me", response_model=auth.UserInfo)
    async def me(user: auth.UserInfo = Depends(auth.get_current_user)):
        return user

    @app.get("/travel/conversations", response_model=List[ConversationItem])
    async def list_conversations(user: auth.UserInfo = Depends(auth.get_current_user)):
        return conversation_store.list_conversations(user.user_id)

    @app.post("/travel/conversations", response_model=ConversationCreateResponse)
    async def create_conversation(user: auth.UserInfo = Depends(auth.get_current_user)):
        conv = conversation_store.create_conversation(user.user_id)
        return ConversationCreateResponse(
            thread_id=conv["thread_id"],
            title=conv["title"],
        )

    @app.get("/travel/conversations/{thread_id}/messages", response_model=MessagesResponse)
    async def get_conversation_messages(
        thread_id: str,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        _verify_thread(thread_id, user)
        messages = await get_thread_messages(thread_id)
        return MessagesResponse(thread_id=thread_id, messages=messages)

    @app.delete("/travel/conversations/{thread_id}")
    async def remove_conversation(
        thread_id: str,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        _verify_thread(thread_id, user)
        await delete_thread_checkpoint(thread_id)
        conversation_store.delete_conversation(thread_id, user.user_id)
        return {"status": "success", "thread_id": thread_id}

    @app.get("/travel/preferences", response_model=PreferencesResponse)
    async def get_preferences(user: auth.UserInfo = Depends(auth.get_current_user)):
        prefs = user_preference.get_all_preferences(user.user_id)
        return PreferencesResponse(
            user_id=user.user_id,
            preferences=prefs,
            count=len(prefs),
        )

    @app.delete("/travel/preferences/{key}")
    async def delete_preference_key(
        key: str,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        if key not in user_preference.ALLOWED_PREFERENCE_KEYS:
            raise HTTPException(status_code=400, detail="不支持的偏好 key")
        if not user_preference.delete_preference(user.user_id, key):
            raise HTTPException(status_code=404, detail="偏好不存在")
        return {"status": "success", "key": key}

    @app.delete("/travel/preferences")
    async def delete_all_preferences(user: auth.UserInfo = Depends(auth.get_current_user)):
        count = user_preference.delete_all_preferences(user.user_id)
        return {"status": "success", "deleted": count}

    @app.post("/travel/chat", response_model=ChatResponse)
    async def travel_chat(
        request: ChatRequest,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        if agent is None:
            raise HTTPException(status_code=500, detail="Agent 未初始化")
        _verify_thread(request.thread_id, user)
        try:
            content = await invoke_agent(
                request.message, request.thread_id, user.user_id
            )
            conversation_store.touch_conversation(
                request.thread_id, user.user_id, request.message
            )
            return ChatResponse(content=content)
        except Exception as e:
            logging.error(traceback.format_exc())
            return ChatResponse(content="", status="error", error=str(e))

    @app.post("/travel/chat/stream")
    async def travel_chat_stream(
        request: ChatRequest,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        if agent is None:
            raise HTTPException(status_code=500, detail="Agent 未初始化")
        _verify_thread(request.thread_id, user)

        async def event_generator():
            async for payload in stream_agent(
                request.message, request.thread_id, user.user_id
            ):
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            conversation_store.touch_conversation(
                request.thread_id, user.user_id, request.message
            )

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/files", response_model=List[FileInfo])
    async def list_files(user: auth.UserInfo = Depends(auth.get_current_user)):
        output_dir = ensure_output_dir(user.user_id)
        files: List[FileInfo] = []
        for entry in output_dir.iterdir():
            if entry.is_file():
                stat = entry.stat()
                files.append(
                    FileInfo(
                        name=entry.name,
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    )
                )
        files.sort(key=lambda f: f.modified_at, reverse=True)
        return files

    @app.get("/files/{filename}/content", response_model=FileContent)
    async def get_file_content(
        filename: str,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        try:
            filepath = resolve_safe_filepath(filename, user.user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="非法文件名")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")
        try:
            content = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="文件非文本格式，无法预览")
        return FileContent(name=filepath.name, content=content)

    @app.get("/files/{filename}")
    async def get_file(
        filename: str,
        download: bool = False,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        try:
            filepath = resolve_safe_filepath(filename, user.user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="非法文件名")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")

        if download:
            return FileResponse(
                filepath,
                media_type="application/octet-stream",
                headers={"Content-Disposition": _attachment_disposition(filepath.name)},
            )

        try:
            content = filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileResponse(
                filepath,
                media_type="application/octet-stream",
                headers={"Content-Disposition": _attachment_disposition(filepath.name)},
            )
        return FileContent(name=filepath.name, content=content)

    @app.delete("/files/{filename}")
    async def delete_file(
        filename: str,
        user: auth.UserInfo = Depends(auth.get_current_user),
    ):
        try:
            filepath = resolve_safe_filepath(filename, user.user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="非法文件名")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文件不存在")
        try:
            filepath.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"删除失败：{e}")
        return {"status": "success", "name": filepath.name}

    import uvicorn

    uvicorn.run(app, host=host, port=port)


# ────────────────────────────
# 入口
# ────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="差旅出行助手")
    parser.add_argument("--api", action="store_true", help="以 FastAPI 模式运行（端口 8001）")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API 绑定地址")
    parser.add_argument("--port", type=int, default=8001, help="API 模式端口（默认 8001）")
    args = parser.parse_args()

    if args.api:
        run_api(host=args.host, port=args.port)
    else:
        asyncio.run(run_chat_loop())
