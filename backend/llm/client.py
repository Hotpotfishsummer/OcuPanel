from __future__ import annotations
import os
import sys
import time
import concurrent.futures
import threading
import openai
from utils import config
DEFAULT_MODEL = config.DEFAULT_MODEL
def _log_call(t0: float, messages: list[dict], reply: str, exc: BaseException | None = None) -> None:
    """每次模型调用打一行耗时。报告链路要串行十几次调用，
    出问题时必须能从日志看出是哪一次、多大的输入、卡了多久。"""
    elapsed = time.time() - t0
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    if exc is not None:
        print(f"[LLM] {elapsed:.1f}s in={chars} FAILED {type(exc).__name__}", file=sys.stderr, flush=True)
    else:
        print(f"[LLM] {elapsed:.1f}s in={chars} out={len(reply)}", file=sys.stderr, flush=True)


def resolve_model(model: str | None = None) -> str:
    """模型名：显式入参 > 环境变量 OPENAI_MODEL > 默认值。统一由 config 读取。"""
    return model or config.OPENAI_MODEL or DEFAULT_MODEL

def resolve_base_url() -> str | None:
    """OpenAI 兼容端点。None 表示官方 OpenAI API。"""
    return config.OPENAI_BASE_URL or None

def thinking_control(base_url: str | None = None) -> dict[str, dict[str, str]] | None:
    """DeepSeek 的 thinking 模式默认开启，会让单次生成慢 2-3 倍。

    LLM_DISABLE_THINKING=1 时通过 extra_body 关掉；其他厂商不支持该字段，
    因此只对 DeepSeek 端点生效，其余情况返回 None 不影响正常调用。
    base_url: 用户自定义端点也要照此判断，不能只看全局配置。
    """
    if not config.LLM_DISABLE_THINKING:
        return None
    base = base_url if base_url is not None else resolve_base_url()
    if "deepseek" not in (base or "").lower():
        return None
    return {"thinking": {"type": "disabled"}}


def reasoning_kwargs() -> dict[str, str]:
    """推理强度参数。默认空 = 不传；配了 LLM_REASONING_EFFORT 才附上。

    报告流水线一条链路要串行调用十几次 LLM，默认强度的隐藏思考会把单次
    耗时从几秒拉到一两分钟。非推理端点一般忽略未知字段，因此可以安全带上。
    """
    effort = config.LLM_REASONING_EFFORT
    return {"reasoning_effort": effort} if effort else {}

def describe_llm_error(
    exc: BaseException,
    base_url: str | None = None,
    from_user_config: bool = False,
) -> str:
    """把 SDK 抛出的嵌套异常压成一行可读信息，并附上排查提示。

    from_user_config=True 表示这次调用用的是某个用户在「个人设置」里填的 key，
    提示语必须指向那里的配置，而不是 backend/.env —— 否则用户会去改错地方。
    """
    chain = []
    cur = exc
    while cur is not None and len(chain) < 4:
        text = str(cur).strip()
        name = type(cur).__name__
        if text:
            chain.append(f"{name}: {text}")
        else:
            chain.append(name)
        cur = cur.__cause__ or cur.__context__
        if cur is chain:
            break

    detail = " | ".join(chain) if chain else type(exc).__name__
    joined = detail.lower()

    # 提示语里的「去哪儿改配置」随调用来源变化
    where = "「个人设置 → LLM API 配置」中填写的" if from_user_config else "backend/.env 中的"
    endpoint = base_url or resolve_base_url()

    hints = []
    if "certificate_verify_failed" in joined or "self-signed certificate" in joined:
        hints.append(
            "TLS 证书校验失败：本机代理/杀软可能在做 HTTPS 中间人。"
            "可先关闭代理直连，或设置 SSL_CERT_FILE 指向代理根证书。"
        )
    if "connection error" in joined or "connect" in joined:
        hints.append(
            f"无法建立连接：确认接口地址 {endpoint} 可访问；"
            "若 shell 里残留 HTTPS_PROXY/HTTP_PROXY，请先清空（Remove-Item Env:HTTPS_PROXY）。"
        )
    if "401" in joined or "authentication" in joined or "invalid_api_key" in joined:
        hints.append(f"鉴权失败：检查{where} API Key 是否正确、是否已过期。")
    if "404" in joined or ("model" in joined and "not exist" in joined):
        hints.append(
            f"模型名或接口地址可能不对：当前模型在 {endpoint} 上不存在，"
            "核对「接口地址 + 模型」是否为同一家服务商。"
        )
    if "402" in joined or "insufficient balance" in joined:
        hints.append("账户余额不足，请到服务商控制台充值。")

    if hints:
        detail += "  → " + " ".join(hints)
    return detail


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize model parameters

        base_url: 用户自己配置的接口地址。为 None 时回落到环境变量里的全局配置，
        这样老的单用户模式（直接用 .env 里的 key）仍然跑得通。
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or None
        self.model = resolve_model(model)
        self.base_url = base_url if base_url is not None else resolve_base_url()
        self.system_prompt = {"role": "system", "content": "You are a helpful assistant."}
        self.chat_history = []
        self.messages = [self.system_prompt]
        # 同一用户的综合报告与后续追问可能并发，历史列表的读改写要串行化
        self._state_lock = threading.Lock()
        # 一条报告流水线会串行/并发发起十几次调用。每次都新建 openai 客户端
        # 会不断开新连接、留下未关闭的 socket，网关侧也更容易出现连接抖动。
        # 这里按实例惰性复用一个客户端，httpx 本身是线程安全的。
        self._client_lock = threading.Lock()
        self._client: openai.OpenAI | None = None

    def _create(self, client: "openai.OpenAI", kwargs: dict):
        """带硬性看门狗的 create：超过 LLM_CALL_TIMEOUT 就放弃等待。

        只用传输层 timeout 不够：网关偶发会挂住一次请求，而 SDK 的
        自动重试会把 120s 再翻一倍（实测一次调用 277.8s）。看门狗把
        等待截断在上限内，被放弃的线程自行结束，不阻塞后续阶段。
        """
        budget = config.LLM_CALL_TIMEOUT
        if not budget or budget <= 0:
            return client.chat.completions.create(**kwargs)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-call")
        future = pool.submit(client.chat.completions.create, **kwargs)
        try:
            return future.result(timeout=budget)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"LLM 调用超过 {budget:.0f}s 仍未返回，已放弃本次等待（网关长尾）"
            ) from None
        finally:
            pool.shutdown(wait=False)

    def _client_for_call(self) -> "openai.OpenAI":
        with self._client_lock:
            if self._client is None:
                # 本地网关不需要 Key；但 OpenAI SDK 会把空串塞进
                # "Authorization: Bearer "，httpx 0.28 直接判为非法头值。
                # 没有 Key 时给一个占位符，服务端会忽略。
                self._client = openai.OpenAI(
                    api_key=self.api_key or "no-key",
                    base_url=self.base_url,
                    timeout=config.LLM_TIMEOUT,
                    max_retries=config.LLM_MAX_RETRIES,
                )
            return self._client

    def chat_with_history(
        self,
        history: list[dict],
        user_message: str,
        *,
        system: str | None = None,
    ) -> str:
        """根据调用方提供的消息和可选系统指令发起无状态请求。"""
        messages = [{"role": "system", "content": system}] if system else []
        for item in history:
            role = item.get("role")
            if role in ("user", "assistant") and item.get("content"):
                messages.append({"role": role, "content": item["content"]})
        messages.append({"role": "user", "content": user_message})

        client = self._client_for_call()
        kwargs = {
            "model": self.model, "messages": messages, "temperature": 0.7,
            "max_tokens": config.CHAT_MAX_OUTPUT_TOKENS,
            **reasoning_kwargs(),
        }
        extra = thinking_control(self.base_url)
        if extra:
            kwargs["extra_body"] = extra
        t0 = time.time()
        try:
            response = self._create(client, kwargs)
            _log_call(t0, messages, response.choices[0].message.content or "")
            return response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - 让上层统一转成可读错误
            _log_call(t0, messages, "", exc)
            raise RuntimeError(
                describe_llm_error(exc, self.base_url, from_user_config=True)
            ) from exc

    def chat_with_gpt(
        self,
        user_message: str,
        conversation: bool = False,
        reset: bool = True,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Talk to GPT with optional context reset

        system / temperature: 供校验智能体使用。校验与重写需要独立的系统提示词
        （"你是校对员"而不是"你是医学助手"）和接近 0 的温度，否则同一个角色
        既写又判，等于自证清白。
        """
        t0 = time.time()
        try:
            sys_prompt = self.system_prompt if not system else {"role": "system", "content": system}
            if reset:
                messages = [sys_prompt, {"role": "user", "content": user_message}]
            else:
                with self._state_lock:
                    self.messages.append({"role": "user", "content": user_message})
                    messages = list(self.messages)

            client = self._client_for_call()
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7 if temperature is None else temperature,
                "max_tokens": config.LLM_MAX_OUTPUT_TOKENS,
                **reasoning_kwargs(),
            }
            extra = thinking_control(self.base_url)
            if extra:
                kwargs["extra_body"] = extra
            response = self._create(client, kwargs)
            reply = response.choices[0].message.content or ""
            _log_call(t0, messages, reply)

            if not reset:
                with self._state_lock:
                    self.messages.append({"role": "assistant", "content": reply})

            if (conversation):
                with self._state_lock:
                    self.chat_history.append({"role": "user", "content": user_message})
                    self.chat_history.append({"role": "assistant", "content": reply})

            return reply
        except Exception as e:
            # 保留异常链，否则 UI 只能看到 "Connection error." 这类无信息量的外壳
            detail = describe_llm_error(e, self.base_url, from_user_config=True)
            _log_call(t0, locals().get("messages") or [], "", e)
            print(f"[LLM] 错误详情：{detail[:200]}", file=sys.stderr, flush=True)
            print(f"[LLM ERROR] {detail}", file=sys.stderr)
            return f"Error: {detail}"

    def reset_conversation(self) -> None:
        """清空对话上下文。

        原来只清 chat_history（PDF 用的那份），self.messages 仍留着上一轮的
        问答，重置后模型其实还能"记得"之前的内容，属于隐性 bug，一并清掉。
        """
        with self._state_lock:
            self.chat_history.clear()
            self.messages = [self.system_prompt]
