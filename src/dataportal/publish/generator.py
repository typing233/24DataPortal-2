"""Deployment artifact generator - orchestrates Dockerfile, compose, and platform configs."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from dataportal.publish.dockerfile import generate_dockerfile
from dataportal.publish.compose import generate_compose
from dataportal.publish.render import generate_render_yaml
from dataportal.publish.cloudrun import generate_cloudbuild_yaml, generate_deploy_script


class PublishGenerator:
    def __init__(
        self,
        output_dir: str,
        platform: str = "docker",
        port: int = 8001,
        include_data: bool = True,
        project_dir: str | None = None,
        config_path: str | None = None,
        sources: list[str] | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.platform = platform
        self.port = port
        self.include_data = include_data
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.config_path = config_path
        self.sources = sources or []

    def generate(self) -> dict[str, str]:
        """Generate all deployment artifacts. Returns dict of filename -> path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        generated = {}

        # Always generate Dockerfile
        dockerfile = generate_dockerfile(
            port=self.port,
            include_data=self.include_data,
            sources=self.sources,
            config_path=self.config_path,
        )
        df_path = self.output_dir / "Dockerfile"
        df_path.write_text(dockerfile)
        generated["Dockerfile"] = str(df_path)

        # .dockerignore
        dockerignore = self._generate_dockerignore()
        di_path = self.output_dir / ".dockerignore"
        di_path.write_text(dockerignore)
        generated[".dockerignore"] = str(di_path)

        # docker-compose.yml (always useful)
        compose = generate_compose(port=self.port)
        compose_path = self.output_dir / "docker-compose.yml"
        compose_path.write_text(compose)
        generated["docker-compose.yml"] = str(compose_path)

        # Platform-specific
        if self.platform in ("render", "all"):
            render_yaml = generate_render_yaml(port=self.port)
            rp = self.output_dir / "render.yaml"
            rp.write_text(render_yaml)
            generated["render.yaml"] = str(rp)

        if self.platform in ("cloudrun", "all"):
            cloudbuild = generate_cloudbuild_yaml()
            cb_path = self.output_dir / "cloudbuild.yaml"
            cb_path.write_text(cloudbuild)
            generated["cloudbuild.yaml"] = str(cb_path)

            deploy_script = generate_deploy_script(port=self.port)
            ds_path = self.output_dir / "deploy-cloudrun.sh"
            ds_path.write_text(deploy_script)
            ds_path.chmod(0o755)
            generated["deploy-cloudrun.sh"] = str(ds_path)

        # Copy data files if requested
        if self.include_data and self.sources:
            data_dir = self.output_dir / "data"
            data_dir.mkdir(exist_ok=True)
            for source in self.sources:
                src_path = Path(source)
                if src_path.exists():
                    if src_path.is_file():
                        shutil.copy2(src_path, data_dir / src_path.name)
                    elif src_path.is_dir():
                        for f in src_path.iterdir():
                            if not f.name.startswith(".") and f.is_file():
                                shutil.copy2(f, data_dir / f.name)

        # Copy config if present
        if self.config_path:
            config_src = Path(self.config_path)
            if config_src.exists():
                shutil.copy2(config_src, self.output_dir / "config.json")
                generated["config.json"] = str(self.output_dir / "config.json")

        # Rollback script
        rollback = self._generate_rollback_script()
        rollback_path = self.output_dir / "rollback.sh"
        rollback_path.write_text(rollback)
        rollback_path.chmod(0o755)
        generated["rollback.sh"] = str(rollback_path)

        return generated

    def _generate_dockerignore(self) -> str:
        return """__pycache__
*.pyc
*.pyo
.git
.venv
venv
.env
*.egg-info
.pytest_cache
node_modules
.mypy_cache
"""

    def _generate_rollback_script(self) -> str:
        if self.platform == "render":
            return """#!/bin/bash
# Render rollback - use the Render dashboard or API
echo "To rollback on Render:"
echo "  1. Go to your Render dashboard"
echo "  2. Select the service"
echo "  3. Click 'Manual Deploy' and select a previous commit"
echo ""
echo "Or use the Render API:"
echo "  curl -X POST https://api.render.com/v1/services/SERVICE_ID/deploys \\\\
    -H 'Authorization: Bearer YOUR_API_KEY' \\\\
    -H 'Content-Type: application/json' \\\\
    -d '{\"commitId\": \"PREVIOUS_COMMIT_SHA\"}'
"
"""
        elif self.platform == "cloudrun":
            return """#!/bin/bash
# Cloud Run rollback
set -e

SERVICE_NAME="${1:-dataportal}"
REGION="${2:-us-central1}"

echo "Listing recent revisions..."
gcloud run revisions list --service=$SERVICE_NAME --region=$REGION --limit=5

echo ""
echo "To rollback to a specific revision:"
echo "  gcloud run services update-traffic $SERVICE_NAME --region=$REGION --to-revisions=REVISION_NAME=100"
"""
        else:
            return """#!/bin/bash
# Docker rollback
set -e

IMAGE_NAME="${1:-dataportal}"
PREVIOUS_TAG="${2:-previous}"

echo "Stopping current container..."
docker compose down

echo "Switching to previous version..."
docker tag $IMAGE_NAME:$PREVIOUS_TAG $IMAGE_NAME:latest

echo "Starting previous version..."
docker compose up -d

echo "Rollback complete. Check logs with: docker compose logs -f"
"""
