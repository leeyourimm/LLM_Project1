"""DB 스키마 파일 찾기. 배포 이미지(설치한 패키지)에서도 마이그레이션이 적용돼야 한다."""

import tomllib
from pathlib import Path

import pytest

from dartrag.db import Repository, repository

ROOT = Path(__file__).resolve().parents[1]


def test_migrate_fails_loudly_without_schema_files(tmp_path, monkeypatch):
    # 예전에는 스키마 파일을 못 찾으면 아무것도 적용하지 않고 조용히 끝났다
    monkeypatch.setattr(repository, "SCHEMA_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="스키마"):
        Repository(conn=None).migrate()


def test_schema_is_packaged_where_migrate_looks():
    # 저장소에서 돌릴 때는 infra/db 를 쓴다
    assert repository.SCHEMA_DIR == ROOT / "infra" / "db"
    assert len(list(repository.SCHEMA_DIR.glob("*.sql"))) > 0
    # wheel 에는 같은 파일이 dartrag/db/schema 로 들어가고, 설치된 패키지는 그곳을 본다
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["force-include"]["infra/db"] == "dartrag/db/schema"
    packaged = Path(repository.__file__).resolve().parent / "schema"
    assert packaged.relative_to(ROOT / "src").as_posix() == "dartrag/db/schema"
