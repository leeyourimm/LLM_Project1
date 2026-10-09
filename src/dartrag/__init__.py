"""DART 공시 분석 서비스."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dartrag")
except PackageNotFoundError:  # 설치하지 않고 소스에서 바로 실행할 때
    __version__ = "0.0.0"
