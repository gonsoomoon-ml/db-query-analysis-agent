"""공통 설정 헬퍼."""
import os


def demo_user() -> str:
    """멀티시연자 prefix — DEMO_USER env (없으면 'ubuntu'). Redis 키/에이전트명 네임스페이스용."""
    return os.environ.get("DEMO_USER") or "ubuntu"
