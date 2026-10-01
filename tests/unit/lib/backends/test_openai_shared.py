"""OpenAI 共享重试与客户端工厂行为。"""

from unittest.mock import MagicMock

from openai import InternalServerError

from lib.backends.openai_shared import create_openai_client, should_retry_openai_text_generation
from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL


class TestCreateOpenAIClientBaseURL:
    """base_url 唯一来源为 DB 配置：空值兜死官方端点，环境变量不得静默覆盖路由。"""

    def test_empty_base_url_ignores_env_var(self, monkeypatch):
        # AsyncOpenAI 对空 base_url 会回落读 OPENAI_BASE_URL，工厂须显式兜死官方值
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url=None)
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_whitespace_base_url_ignores_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="   ")
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_explicit_base_url_preserved(self, monkeypatch):
        # 显式 base_url 原样透传，不被环境变量或官方默认值篡改
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="https://vllm.internal:8000/v1")
        assert str(client.base_url).rstrip("/") == "https://vllm.internal:8000/v1"


class TestOpenAIRetry:
    def test_generation_524_is_not_retried(self):
        error = InternalServerError(
            message="Error code: 524",
            response=MagicMock(status_code=524, headers={}),
            body=None,
        )
        assert should_retry_openai_text_generation(error) is False

    def test_other_transient_server_errors_still_retry(self):
        error = InternalServerError(
            message="Error code: 502",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        assert should_retry_openai_text_generation(error) is True
