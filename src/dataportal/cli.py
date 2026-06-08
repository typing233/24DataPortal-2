"""CLI entry point for dataportal."""
import click
import uvicorn


@click.group()
def main():
    """DataPortal - Interactive data exploration portal."""
    pass


@main.command()
@click.argument("sources", nargs=-1, required=True)
@click.option("--port", "-p", default=8001, help="Port to serve on")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--config", "-c", default=None, help="Path to config JSON file")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
def serve(sources, port, host, config, reload):
    """Start the data portal web server.

    SOURCES: SQLite files, CSV files, or directories to serve.
    """
    import os
    os.environ["DATAPORTAL_SOURCES"] = "|".join(sources)
    if config:
        os.environ["DATAPORTAL_CONFIG"] = config

    uvicorn.run(
        "dataportal.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
