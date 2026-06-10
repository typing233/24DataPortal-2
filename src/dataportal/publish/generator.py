"""Deployment artifact generator - orchestrates Dockerfile, compose, and platform configs."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from dataportal.publish.dockerfile import generate_dockerfile, generate_entrypoint_script
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

        # Dockerfile
        dockerfile = generate_dockerfile(
            port=self.port,
            include_data=self.include_data,
            sources=self.sources,
            config_path=self.config_path,
        )
        df_path = self.output_dir / "Dockerfile"
        df_path.write_text(dockerfile)
        generated["Dockerfile"] = str(df_path)

        # Entrypoint script (handles migration, secrets, config)
        entrypoint = generate_entrypoint_script(port=self.port)
        ep_path = self.output_dir / "entrypoint.sh"
        ep_path.write_text(entrypoint)
        ep_path.chmod(0o755)
        generated["entrypoint.sh"] = str(ep_path)

        # .dockerignore
        dockerignore = self._generate_dockerignore()
        di_path = self.output_dir / ".dockerignore"
        di_path.write_text(dockerignore)
        generated[".dockerignore"] = str(di_path)

        # Secrets template
        secrets_template = self._generate_secrets_template()
        secrets_path = self.output_dir / "secrets.env.example"
        secrets_path.write_text(secrets_template)
        generated["secrets.env.example"] = str(secrets_path)

        # docker-compose.yml
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

        # Copy data files
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

        # Copy config
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

        # Logs viewing script
        logs_script = self._generate_logs_script()
        logs_path = self.output_dir / "logs.sh"
        logs_path.write_text(logs_script)
        logs_path.chmod(0o755)
        generated["logs.sh"] = str(logs_path)

        return generated

    def _generate_dockerignore(self) -> str:
        return """__pycache__
*.pyc
*.pyo
.git
.venv
venv
.env
secrets.env
*.egg-info
.pytest_cache
node_modules
.mypy_cache
"""

    def _generate_secrets_template(self) -> str:
        return """# DataPortal Secrets Configuration
# Copy to secrets.env and fill in values. NEVER commit secrets.env to git.
# Mount as /run/secrets/dataportal.env in Docker, or set as env vars in Cloud Run.

# Write API authentication tokens (comma-separated)
DP_AUTH_TOKENS=token1,token2

# Site configuration (optional - can also be in config.json)
DP_SITE_TITLE=My DataPortal

# Enable write API
DP_WRITE_API_ENABLED=true

# Plugin signing keys (base64-encoded Ed25519 public keys, comma-separated)
# DP_TRUSTED_KEYS=base64key1,base64key2
"""

    def _generate_logs_script(self) -> str:
        if self.platform == "render":
            return """#!/bin/bash
# View DataPortal logs on Render
echo "Render logs are available at:"
echo "  https://dashboard.render.com/ -> Your Service -> Logs"
echo ""
echo "Or via the Render CLI:"
echo "  render logs --service dataportal --tail"
"""
        elif self.platform == "cloudrun":
            return """#!/bin/bash
# View DataPortal logs on Cloud Run
set -e
SERVICE_NAME="${1:-dataportal}"
REGION="${2:-us-central1}"

echo "Streaming logs for $SERVICE_NAME..."
echo "Press Ctrl+C to stop."
echo ""

gcloud run services logs read "$SERVICE_NAME" \\
    --region="$REGION" \\
    --limit=100

echo ""
echo "For live tail:"
echo "  gcloud run services logs tail $SERVICE_NAME --region=$REGION"
"""
        else:
            return """#!/bin/bash
# View DataPortal Docker logs
set -e

echo "Streaming DataPortal logs..."
echo "Press Ctrl+C to stop."
echo ""

docker compose logs -f --tail=100 dataportal
"""

    def _generate_rollback_script(self) -> str:
        if self.platform == "render":
            return """#!/bin/bash
# Render rollback
set -e
echo "Render Rollback Options:"
echo ""
echo "1. Via Dashboard:"
echo "   https://dashboard.render.com/ -> Service -> Events -> Deploy previous commit"
echo ""
echo "2. Via API:"
echo "   SERVICE_ID=<your-service-id>"
echo "   API_KEY=<your-api-key>"
echo "   COMMIT_SHA=<previous-commit>"
echo ""
echo "   curl -X POST https://api.render.com/v1/services/\\$SERVICE_ID/deploys \\"
echo "     -H 'Authorization: Bearer \\$API_KEY' \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"commitId\": \"'\\$COMMIT_SHA'\"}'  "
echo ""
echo "3. Via Git (recommended):"
echo "   git revert HEAD"
echo "   git push  # Render auto-deploys"
"""
        elif self.platform == "cloudrun":
            return """#!/bin/bash
# Cloud Run rollback
set -e

SERVICE_NAME="${1:-dataportal}"
REGION="${2:-us-central1}"

echo "=== Current revisions ==="
gcloud run revisions list --service="$SERVICE_NAME" --region="$REGION" --limit=5 --format="table(name,active,createTime)"

echo ""
read -p "Enter revision name to rollback to: " REVISION

if [ -n "$REVISION" ]; then
    echo "Rolling back to $REVISION..."
    gcloud run services update-traffic "$SERVICE_NAME" \\
        --region="$REGION" \\
        --to-revisions="$REVISION=100"
    echo "Rollback complete. Traffic now routing to $REVISION."
else
    echo "No revision specified, aborting."
fi
"""
        else:
            return """#!/bin/bash
# Docker rollback using image tags
set -e

IMAGE_NAME="${1:-dataportal}"

echo "=== Available image versions ==="
docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"

echo ""
PREVIOUS_TAG="${2:-}"

if [ -z "$PREVIOUS_TAG" ]; then
    read -p "Enter tag to rollback to: " PREVIOUS_TAG
fi

if [ -n "$PREVIOUS_TAG" ]; then
    echo "Stopping current container..."
    docker compose down

    echo "Tagging $IMAGE_NAME:$PREVIOUS_TAG as latest..."
    docker tag "$IMAGE_NAME:$PREVIOUS_TAG" "$IMAGE_NAME:latest"

    echo "Starting previous version..."
    docker compose up -d

    echo "Rollback complete. Verify with: docker compose logs -f"
else
    echo "No tag specified, aborting."
fi
"""
