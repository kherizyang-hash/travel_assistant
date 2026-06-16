import os
import json
import httpx
from typing import Any
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# 加载环境变量
load_dotenv()

# 初始化 MCP 服务器。创建了一个 MCP 服务器对象，名字叫 "WeatherServer"。
mcp = FastMCP("WeatherServer")

# OpenWeather API 配置
OPENWEATHER_API_BASE = "https://api.openweathermap.org/data/2.5/weather"
API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
USER_AGENT = "weather-app/1.0"

# 异步函数。异步的意思是“不阻塞其他任务”。    返回值要么是字典，要么是 None
async def fetch_weather(city: str) -> dict[str, Any] | None:
    """
    从 OpenWeather API 获取天气信息。
    :param city: 城市名称（需使用英文，如 Beijing）
    :return: 天气数据字典；若出错返回包含 error 信息的字典
    """
    params = {
        "q": city,
        "appid": API_KEY,
        "units": "metric",
        "lang": "zh_cn"
    }
    headers = {"User-Agent": USER_AGENT}

    # 自动创建异步请求客户端、使用它发请求，代码块结束后自动关闭客户端，不用手动写关闭逻辑。
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(OPENWEATHER_API_BASE, params=params, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()  # 返回字典类型
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP 错误: {e.response.status_code}"}
        except Exception as e:
            return {"error": f"请求失败: {str(e)}"}

def format_weather(data: dict[str, Any] | str) -> str:
    """
    将天气数据格式化为易读文本。
    :param data: 天气数据（可以是字典或 JSON 字符串）
    :return: 格式化后的天气信息字符串
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)  # 如果是字符串，转成字典
        except Exception as e:
            return f"无法解析天气数据: {e}"

    if "error" in data:
        return f"⚠️ {data['error']}"
    #     如果 fetch_weather 返回了 {"error": "..."}，这里就直接返回错误提示，不再继续格式化。

    city = data.get("name", "未知")   ## 如果取不到，默认"未知"
    country = data.get("sys", {}).get("country", "未知")
    temp = data.get("main", {}).get("temp", "N/A")
    humidity = data.get("main", {}).get("humidity", "N/A")
    wind_speed = data.get("wind", {}).get("speed", "N/A")
    weather_list = data.get("weather", [{}])
    description = weather_list[0].get("description", "未知")

    return (
        f"🌍 {city}, {country}\n"
        f"🌡 温度: {temp}°C\n"
        f"💧 湿度: {humidity}%\n"
        f"🌬 风速: {wind_speed} m/s\n"
        f"🌤 天气: {description}\n"
    )

@mcp.tool()   #把函数“注册”成 MCP 工具
async def query_weather(city: str) -> str:
    """
    输入指定城市的英文名称，返回今日天气查询结果。
    :param city: 城市名称（需使用英文）
    :return: 格式化后的天气信息
    """
    data = await fetch_weather(city)
    return format_weather(data)

# 展示一个 MCP 服务器可以注册多个工具。
@mcp.tool()
async def get_weather_tips(season: str) -> str:
    """
    获取指定季节的天气贴士。
    这是演示同一个 MCP Server 可以包含多个 Tool 的例子。
    :param season: 季节名称 (spring, summer, autumn, winter)
    """
    tips = {
        "spring": "🌸 春季多风，注意防风保暖，预防花粉过敏。",
        "summer": "☀️ 夏季炎热，注意防暑降温，多喝水。",
        "autumn": "🍁 秋季干燥，注意补水润肺，早晚温差大。",
        "winter": "❄️ 冬季寒冷，注意防寒保暖，预防感冒。"
    }
    return tips.get(season.lower(), "❓ 未知季节，请注意身体健康。")

if __name__ == "__main__":
    # 以标准 I/O 方式运行 MCP 服务器
    mcp.run(transport='stdio')
