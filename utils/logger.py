"""统一日志配置 (loguru)。"""
from loguru import logger
import sys


def setup_logging(level: str = "INFO", project: str = "herb_recognition"):
    """配置全局 logger，返回 loguru 实例。"""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            f"<green>{{time:YYYY-MM-DD HH:mm:ss}}</green> | "
            f"<cyan>{project}</cyan> | "
            f"<level>{{level: <8}}</level> | "
            f"<level>{{message}}</level>"
        ),
    )
    return logger
