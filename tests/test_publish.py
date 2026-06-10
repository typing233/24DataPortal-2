"""Tests for the publish/deploy command."""
import json
import tempfile
from pathlib import Path

import pytest

from dataportal.publish.generator import PublishGenerator
from dataportal.publish.dockerfile import generate_dockerfile
from dataportal.publish.compose import generate_compose
from dataportal.publish.render import generate_render_yaml
from dataportal.publish.cloudrun import generate_cloudbuild_yaml, generate_deploy_script


class TestDockerfileGeneration:
    def test_basic_dockerfile(self):
        result = generate_dockerfile()
        assert "FROM python:3.12-slim" in result
        assert "EXPOSE 8001" in result
        assert "HEALTHCHECK" in result
        assert "appuser" in result

    def test_custom_port(self):
        result = generate_dockerfile(port=9000)
        assert "EXPOSE 9000" in result
        assert "9000" in result

    def test_with_data(self):
        result = generate_dockerfile(include_data=True)
        assert "COPY data/" in result

    def test_without_data(self):
        result = generate_dockerfile(include_data=False)
        assert "COPY data/" not in result

    def test_with_config(self):
        result = generate_dockerfile(config_path="config.json")
        assert "COPY config.json" in result
        assert "DATAPORTAL_CONFIG" in result


class TestComposeGeneration:
    def test_basic_compose(self):
        result = generate_compose()
        assert "dataportal:" in result
        assert "8001:8001" in result
        assert "healthcheck:" in result

    def test_custom_port(self):
        result = generate_compose(port=3000)
        assert "3000:3000" in result


class TestRenderGeneration:
    def test_render_yaml(self):
        result = generate_render_yaml()
        assert "type: web" in result
        assert "dataportal" in result
        assert "healthCheckPath: /health" in result
        assert "plan: free" in result


class TestCloudRunGeneration:
    def test_cloudbuild_yaml(self):
        result = generate_cloudbuild_yaml()
        assert "gcr.io/$PROJECT_ID/dataportal" in result
        assert "gcloud" in result
        assert "--allow-unauthenticated" in result

    def test_deploy_script(self):
        result = generate_deploy_script()
        assert "#!/bin/bash" in result
        assert "gcloud run deploy" in result
        assert "gcloud builds submit" in result


class TestPublishGenerator:
    def test_docker_platform(self, tmp_path):
        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="docker",
        )
        generated = gen.generate()
        assert "Dockerfile" in generated
        assert "docker-compose.yml" in generated
        assert ".dockerignore" in generated
        assert "rollback.sh" in generated
        assert (tmp_path / "deploy" / "Dockerfile").exists()

    def test_render_platform(self, tmp_path):
        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="render",
        )
        generated = gen.generate()
        assert "render.yaml" in generated
        assert "Dockerfile" in generated

    def test_cloudrun_platform(self, tmp_path):
        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="cloudrun",
        )
        generated = gen.generate()
        assert "cloudbuild.yaml" in generated
        assert "deploy-cloudrun.sh" in generated
        # Check script is executable
        script = tmp_path / "deploy" / "deploy-cloudrun.sh"
        assert script.stat().st_mode & 0o111

    def test_all_platforms(self, tmp_path):
        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="all",
        )
        generated = gen.generate()
        assert "render.yaml" in generated
        assert "cloudbuild.yaml" in generated
        assert "Dockerfile" in generated

    def test_include_data(self, tmp_path):
        # Create a source file
        source_dir = tmp_path / "data"
        source_dir.mkdir()
        (source_dir / "test.sqlite").write_bytes(b"fake db")

        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="docker",
            include_data=True,
            sources=[str(source_dir)],
        )
        generated = gen.generate()
        data_dir = tmp_path / "deploy" / "data"
        assert data_dir.exists()
        assert (data_dir / "test.sqlite").exists()

    def test_include_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"site": {"title": "Test"}}')

        gen = PublishGenerator(
            output_dir=str(tmp_path / "deploy"),
            platform="docker",
            config_path=str(config_file),
        )
        generated = gen.generate()
        assert "config.json" in generated
        assert (tmp_path / "deploy" / "config.json").exists()
