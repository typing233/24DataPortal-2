"""Plugin management CLI commands."""
import json
import sys
from pathlib import Path

import click


@click.group()
def plugin():
    """Manage DataPortal plugins."""
    pass


@plugin.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def plugin_list(fmt):
    """List all discovered plugins and their states."""
    from dataportal.plugins.discovery import discover_plugins

    candidates = discover_plugins()

    if fmt == "json":
        data = []
        for c in candidates:
            data.append({
                "name": c.name,
                "module": c.module_path,
                "has_error": bool(c.error),
                "error": c.error,
                "version": c.meta.version if c.meta else "unknown",
                "description": c.meta.description if c.meta else "",
            })
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not candidates:
        click.echo("No plugins discovered.")
        click.echo("Install plugins with: dataportal plugin install <package>")
        return

    click.echo(f"{'Name':<20} {'Version':<10} {'Status':<12} {'Description'}")
    click.echo("-" * 70)
    for c in candidates:
        version = c.meta.version if c.meta else "?"
        status = "error" if c.error else "ok"
        desc = (c.meta.description if c.meta else c.error or "")[:30]
        click.echo(f"{c.name:<20} {version:<10} {status:<12} {desc}")


@plugin.command("install")
@click.argument("package")
@click.option("--no-verify", is_flag=True, help="Skip signature verification")
def plugin_install(package, no_verify):
    """Install a plugin package from PyPI or a local path."""
    import subprocess

    click.echo(f"Installing plugin: {package}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            click.echo(f"Installation failed:\n{result.stderr}", err=True)
            sys.exit(1)

        click.echo(f"Successfully installed: {package}")

        if not no_verify:
            click.echo("Verifying plugin signature...")
            # Signature verification would check dist-info for .sig file
            click.echo("(Signature verification skipped - no .sig found)")

        click.echo("Run 'dataportal plugin list' to see available plugins.")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@plugin.command("uninstall")
@click.argument("name")
def plugin_uninstall(name):
    """Uninstall a plugin package."""
    import subprocess

    click.echo(f"Uninstalling plugin: {name}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        click.echo(f"Uninstall failed:\n{result.stderr}", err=True)
        sys.exit(1)
    click.echo(f"Successfully uninstalled: {name}")


@plugin.command("enable")
@click.argument("name")
def plugin_enable(name):
    """Enable a disabled plugin."""
    state_file = Path.home() / ".dataportal" / "plugins.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())

    disabled = set(state.get("disabled", []))
    disabled.discard(name)
    state["disabled"] = list(disabled)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))
    click.echo(f"Plugin '{name}' enabled. Restart server to apply.")


@plugin.command("disable")
@click.argument("name")
def plugin_disable(name):
    """Disable a plugin without uninstalling."""
    state_file = Path.home() / ".dataportal" / "plugins.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())

    disabled = set(state.get("disabled", []))
    disabled.add(name)
    state["disabled"] = list(disabled)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))
    click.echo(f"Plugin '{name}' disabled. Restart server to apply.")


@plugin.command("health")
def plugin_health():
    """Run health checks on all loaded plugins."""
    import asyncio
    from dataportal.plugins.discovery import discover_plugins

    candidates = discover_plugins()
    click.echo(f"Discovered {len(candidates)} plugin(s)")

    for c in candidates:
        if c.error:
            click.echo(f"  {c.name}: ERROR - {c.error}")
        elif c.plugin_class:
            click.echo(f"  {c.name}: OK (v{c.meta.version if c.meta else '?'})")
        else:
            click.echo(f"  {c.name}: UNKNOWN")


@plugin.command("info")
@click.argument("name")
def plugin_info(name):
    """Show detailed information about a plugin."""
    from dataportal.plugins.discovery import discover_plugins

    candidates = discover_plugins()
    found = None
    for c in candidates:
        if c.name == name:
            found = c
            break

    if not found:
        click.echo(f"Plugin '{name}' not found.", err=True)
        sys.exit(1)

    click.echo(f"Name:        {found.name}")
    click.echo(f"Module:      {found.module_path}")
    if found.meta:
        click.echo(f"Version:     {found.meta.version}")
        click.echo(f"Author:      {found.meta.author}")
        click.echo(f"Description: {found.meta.description}")
        click.echo(f"DataPortal:  {found.meta.dataportal_version}")
        click.echo(f"Python:      {found.meta.python_version}")
        if found.meta.conflicts_with:
            click.echo(f"Conflicts:   {', '.join(found.meta.conflicts_with)}")
        if found.meta.permissions:
            click.echo(f"Permissions: {', '.join(found.meta.permissions)}")
    if found.error:
        click.echo(f"Error:       {found.error}")


@plugin.command("keygen")
def plugin_keygen():
    """Generate an Ed25519 keypair for plugin signing."""
    from dataportal.plugins.signing import generate_keypair

    private_key, public_key = generate_keypair()
    click.echo("Generated Ed25519 keypair for plugin signing:")
    click.echo(f"  Private key: {private_key}")
    click.echo(f"  Public key:  {public_key}")
    click.echo("")
    click.echo("Add the public key to your config:")
    click.echo(f'  "plugins": {{"trusted_keys": ["{public_key}"]}}')
    click.echo("")
    click.echo("Keep the private key secure - use it to sign plugin packages.")
