"""外部 LLM 客户端封装（OpenAI 兼容 /chat/completions 接口，默认对接智谱 GLM）。

配置优先级（环境变量 > 配置文件 > 内置默认值）:
    ZHIPU_API_KEY / LLM_API_KEY   API Key（敏感信息：仅从环境变量读取，绝不写入代码/配置文件；
                                  ZHIPU_API_KEY 优先）
    LLM_BASE_URL                  接口地址，默认 https://open.bigmodel.cn/api/paas/v4
    LLM_MODEL                     模型名，默认 glm-4.7-flash
    LLM_CONFIG                    llm_config.yaml 路径（可选），其中 api_key 建议留空走环境变量

依赖 httpx（fastapi 自带依赖），无需新增第三方包。
"""
import os
import time
from typing import Dict, List, Optional

import httpx
import yaml

_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_DEFAULT_MODEL = "glm-4-flash"
# 限流/服务端临时错误码：自动退避重试（演示高峰期可显著降低降级概率）
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_DELAY = 3.0  # 基础退避秒数，第 n 次重试等待 delay * n


class LLMError(Exception):
    """LLM 调用异常。业务层捕获后降级返回本地结构化结果，不抛 500。"""


def _load_yaml_config() -> Dict:
    """加载 llm_config.yaml 中的 llm 段；文件缺失时返回空 dict。"""
    path = os.environ.get("LLM_CONFIG", "experiments/configs/llm_config.yaml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("llm", {}) or {}
    except Exception:
        return {}


class LLMClient:
    """调用 OpenAI 兼容接口的多轮对话客户端。

    用法:
        llm = LLMClient()
        if llm.available:
            answer = llm.chat([{"role": "user", "content": "你好"}])
    """

    def __init__(self, base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 api_key: Optional[str] = None,
                 timeout: float = 60.0,
                 temperature: float = 0.7,
                 max_tokens: int = 1024):
        cfg = _load_yaml_config()
        # 环境变量优先，其次构造参数，其次配置文件，最后内置默认
        self.base_url = (os.environ.get("LLM_BASE_URL")
                         or base_url or cfg.get("base_url") or _DEFAULT_BASE_URL)
        self.model = (os.environ.get("LLM_MODEL")
                      or model or cfg.get("model") or _DEFAULT_MODEL)
        self.api_key = (os.environ.get("ZHIPU_API_KEY")
                        or os.environ.get("LLM_API_KEY")
                        or api_key or cfg.get("api_key") or "")
        self.timeout = float(os.environ.get("LLM_TIMEOUT")
                             or cfg.get("timeout") or timeout)
        self.temperature = float(os.environ.get("LLM_TEMPERATURE")
                                 or cfg.get("temperature") or temperature)
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS")
                              or cfg.get("max_tokens") or max_tokens)

    @property
    def available(self) -> bool:
        """是否已配置 API Key，可发起真实调用。"""
        return bool(self.api_key.strip() and self.base_url.strip())

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """多轮对话。返回 LLM 回答文本；未配置或调用失败时抛 LLMError。"""
        if not self.api_key:
            raise LLMError("未配置 ZHIPU_API_KEY / LLM_API_KEY 环境变量"
                           "（可执行 $env:ZHIPU_API_KEY=\"...\" 设置）")
        if not self.base_url:
            raise LLMError("未配置 LLM_BASE_URL")
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        last_error: Optional[LLMError] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = httpx.post(url, headers=headers, json=payload,
                                  timeout=self.timeout)
                if resp.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise LLMError(f"LLM 请求超时（{self.timeout:.0f}s），请稍后重试。")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (attempt + 1))
                    continue
                raise LLMError(
                    f"LLM 接口返回错误 {e.response.status_code}: {e.response.text[:200]}")
            except httpx.HTTPError as e:
                raise LLMError(f"LLM 请求失败: {e}")
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError(f"LLM 响应解析失败: {e}")
