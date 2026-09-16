"""atomic — command-line interface.

v0.0 exposes a single command: ``atomic explain <path>``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree as RichTree

from atomic_flow.config import Settings
from atomic_flow.core.encoder import get_encoder
from atomic_flow.tree.builder import Tree, build_tree
from atomic_flow.tree.node import Node

app = typer.Typer(
    name="atomic",
    help="atomic_flow — hierarchical code understanding for LLM context.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def explain(
    path: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, readable=True
    ),
    top: int = typer.Option(15, "--top", "-n", help="Show top-N nodes by fan-in"),
    encode: bool = typer.Option(
        True, "--encode/--no-encode", help="Run the encoder on all nodes"
    ),
    show_tree: bool = typer.Option(
        True, "--tree/--no-tree", help="Print the tree structure"
    ),
) -> None:
    """Build a tree from PATH and print statistics."""
    settings = Settings.load()
    _setup_logging(settings.log_level)

    console.rule(f"[bold]atomic explain[/]  {path}")

    # --- 1. Build ---
    with console.status("Building tree..."):
        tree = build_tree(path, settings)

    stats = tree.stats()
    console.print(
        f"[green]✓[/] Tree built: "
        f"[bold]{stats['total']}[/] nodes, "
        f"{stats['by_kind']}"
    )

    # --- 2. Encode ---
    if encode:
        encoder = get_encoder(settings.encoder)
        console.print(f"[green]✓[/] Encoder: [bold]{encoder.name}[/] (dim={encoder.dim})")

        with console.status("Encoding nodes..."):
            texts = [_node_text(n, tree) for n in tree.nodes]
            matrix = encoder.encode(texts)

        for i, node in enumerate(tree.nodes):
            node.embedding = matrix[i]

        console.print(
            f"[green]✓[/] Embedded [bold]{len(tree.nodes)}[/] nodes"
        )

    # --- 3. Structure ---
    if show_tree:
        console.rule("[bold]Tree[/]")
        _print_tree(tree)

    # --- 4. Top nodes ---
    console.rule(f"[bold]Top {top} nodes by fan-in[/]")
    _print_top(tree, top)

    # --- 5. Stats ---
    console.rule("[bold]Summary[/]")
    _print_summary(tree)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, tree: Tree) -> str:
    """Text representation fed to the encoder."""
    if node.kind.value == "file":
        return node.name
    return node.signature or node.name


def _print_tree(tree: Tree) -> None:
    rich = RichTree(f"[bold]{tree.root.name}[/]")

    file_nodes = [n for n in tree.nodes if n.kind.value == "file"]
    for file_node in file_nodes:
        fbranch = rich.add(f"[cyan]{file_node.file}[/]")
        _attach_children(fbranch, tree, file_node.id)


def _attach_children(branch, tree: Tree, parent_id: str) -> None:
    for child in tree.children_of(parent_id):
        label = _label(child)
        sub = branch.add(label)
        _attach_children(sub, tree, child.id)


def _label(node: Node) -> str:
    tag = {
        "class": "[magenta]class[/]",
        "function": "[yellow]def[/]",
        "method": "[yellow]  def[/]",
    }.get(node.kind.value, node.kind.value)
    return f"{tag} {node.name}  [dim]({node.span}L)[/]"


def _print_top(tree: Tree, n: int) -> None:
    ranked = sorted(tree.nodes, key=lambda x: x.fan_in, reverse=True)[:n]

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=3)
    table.add_column("Kind", width=10)
    table.add_column("Name")
    table.add_column("File", style="cyan")
    table.add_column("Lines", justify="right")
    table.add_column("Embed", justify="center")

    for i, node in enumerate(ranked, 1):
        table.add_row(
            str(i),
            node.kind.value,
            node.name,
            node.file,
            f"{node.start_line}-{node.end_line}",
            "✓" if node.embedding is not None else "·",
        )

    console.print(table)


def _print_summary(tree: Tree) -> None:
    stats = tree.stats()
    total_lines = sum(n.span for n in tree.nodes if n.kind.value == "file")
    console.print(f"Total nodes : [bold]{stats['total']}[/]")
    console.print(f"By kind     : {stats['by_kind']}")
    console.print(f"With embed  : [bold]{stats['with_embedding']}[/]")
    console.print(f"Total lines : [bold]{total_lines}[/]")


if __name__ == "__main__":
    app()
