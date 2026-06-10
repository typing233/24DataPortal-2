"""Publish/deploy CLI command."""
import click


@click.command()
@click.option(
    "--platform", "-t",
    type=click.Choice(["docker", "render", "cloudrun", "all"]),
    default="docker",
    help="Target deployment platform",
)
@click.option("--output-dir", "-o", default="./deploy", help="Output directory for artifacts")
@click.option("--port", "-p", default=8001, help="Application port")
@click.option("--include-data/--no-include-data", default=True, help="Include data files in build")
@click.option("--config", "-c", default=None, help="Path to config.json to include")
@click.argument("sources", nargs=-1)
def publish(platform, output_dir, port, include_data, config, sources):
    """Generate deployment artifacts for cloud platforms.

    Creates Dockerfile, docker-compose.yml, and platform-specific configs
    for one-click deployment to Render, Cloud Run, or self-hosted Docker.

    SOURCES: Data files/directories to include in the deployment.
    """
    from dataportal.publish.generator import PublishGenerator

    generator = PublishGenerator(
        output_dir=output_dir,
        platform=platform,
        port=port,
        include_data=include_data,
        config_path=config,
        sources=list(sources),
    )

    click.echo(f"Generating deployment artifacts for: {platform}")
    click.echo(f"Output directory: {output_dir}")

    generated = generator.generate()

    click.echo(f"\nGenerated {len(generated)} file(s):")
    for name, path in generated.items():
        click.echo(f"  {name:<25} -> {path}")

    click.echo(f"\nNext steps:")
    if platform == "docker":
        click.echo(f"  cd {output_dir}")
        click.echo(f"  docker compose up --build")
    elif platform == "render":
        click.echo(f"  1. Push render.yaml to your Git repository")
        click.echo(f"  2. Connect the repo to Render")
        click.echo(f"  3. Render auto-deploys from render.yaml blueprint")
    elif platform == "cloudrun":
        click.echo(f"  cd {output_dir}")
        click.echo(f"  ./deploy-cloudrun.sh")
    elif platform == "all":
        click.echo(f"  Artifacts for all platforms generated in {output_dir}")

    click.echo(f"\nFor TLS: both Render and Cloud Run provide free automatic TLS.")
    click.echo(f"For rollback: ./rollback.sh")
