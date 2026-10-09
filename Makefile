.PHONY: install up up-search down migrate collect test lint lock

install:
	pip install -e ".[dev]"

up:
	docker compose -f infra/docker-compose.yml up -d

up-search:
	docker compose -f infra/docker-compose.yml --profile search up -d

down:
	docker compose -f infra/docker-compose.yml --profile search down

migrate:
	dartrag migrate

collect:
	dartrag collect

test:
	pytest -q

lint:
	ruff check . && ruff format --check .

# 의존성 잠금 파일(requirements/) 다시 만들기. uv 필요. 배포 이미지·CI 와 같은 Linux x86_64, Python 3.12 기준
# PyTorch 와 CUDA 패키지는 배포 이미지가 CPU 전용 색인에서 따로 받으므로 runtime.txt 에서 뺀다
LOCK_OPTS = --python-version 3.12 --python-platform x86_64-manylinux_2_28 --quiet
TORCH_PKGS = torch triton cuda-bindings cuda-pathfinder cuda-toolkit \
	nvidia-cublas nvidia-cuda-cupti nvidia-cuda-nvrtc nvidia-cuda-runtime nvidia-cudnn-cu13 \
	nvidia-cufft nvidia-cufile nvidia-curand nvidia-cusolver nvidia-cusparse \
	nvidia-cusparselt-cu13 nvidia-nccl-cu13 nvidia-nvjitlink nvidia-nvshmem-cu13 nvidia-nvtx

lock:
	uv pip compile pyproject.toml --extra ops --extra embed $(LOCK_OPTS) --generate-hashes \
		$(addprefix --no-emit-package ,$(TORCH_PKGS)) $(UPGRADE) -o requirements/runtime.txt
	uv pip compile pyproject.toml --extra dev $(LOCK_OPTS) --generate-hashes $(UPGRADE) -o requirements/dev.txt
	uv pip compile pyproject.toml --extra ops --extra embed $(LOCK_OPTS) --no-header --no-annotate \
		--constraints requirements/runtime.txt | grep '^torch==' > requirements/torch.txt
	@# 새 CUDA 패키지가 생기면 runtime.txt 에 섞여 들어가므로 TORCH_PKGS 에 더하라고 알린다
	@! grep -E '^(torch|triton|cuda-|nvidia-)' requirements/runtime.txt || \
		(echo "위 패키지를 TORCH_PKGS 에 더하세요" && exit 1)
