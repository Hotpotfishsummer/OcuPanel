"""执行独立 Agent;每个任务只有请求内上下文。"""
import sys
from typing import Any, Sequence
from .specialist import SpecialistAgent
from concurrent.futures import ThreadPoolExecutor

class ParallelOrchestrator:
    def run(
        self,
        agents: Sequence[SpecialistAgent],
        context: str,
        evidence: dict[str, list[dict[str, Any]]],
        numbers: dict[str, int],
    ) -> dict[str, str]:
        if not agents or len({a.name for a in agents}) != len(agents):
            raise ValueError("Agent names must be nonempty and unique")

        def execute(agent: SpecialistAgent) -> tuple[str, str | None]:
            try:
                return agent.name, agent.execute(context, evidence.get(agent.name, []), numbers)
            except Exception as exc:  # noqa: BLE001
                # 某个专科超时/输出损坏不该让整份报告一起失败：
                # 记一笔后跳过，其余专科的结论仍然可以汇总。
                print(f"[AGENT] {agent.name} 失败，已跳过：{exc}"[:300], file=sys.stderr, flush=True)
                return agent.name, None

        with ThreadPoolExecutor(max_workers=len(agents)) as pool:
            results = dict(pool.map(execute, agents))
        reports = {name: text for name, text in results.items() if text}
        failed = [name for name, text in results.items() if not text]
        if not reports:
            raise RuntimeError(f"所有专科分析均失败：{', '.join(failed)}")
        if failed:
            print(f"[AGENT] 缺失专科：{', '.join(failed)}", file=sys.stderr, flush=True)
        return reports
