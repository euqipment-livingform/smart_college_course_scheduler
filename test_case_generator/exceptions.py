"""生成器异常定义。"""


class GenerationError(Exception):
    """生成过程中的可恢复错误。"""


class InfeasibleError(GenerationError):
    """结构性不可行，通常需要整体重试或调整参数。"""
