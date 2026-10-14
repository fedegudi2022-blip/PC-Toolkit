"""Recuento de comprobaciones compartido por la suite y las guardas de documentación.

Vive aquí y no en `smoke_test.py` porque las guardas de documentación también suman a
este recuento: el total que citan las guías es el de la pasada completa.
"""

from __future__ import annotations

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """Anota el resultado de una comprobación."""
    CHECKS.append((name, bool(condition), detail))


def report() -> int:
    """Imprime el resumen y devuelve el código de salida."""
    failures = [name for name, ok, _ in CHECKS if not ok]
    for name, ok, detail in CHECKS:
        mark = "OK  " if ok else "FALLO"
        print(f"[{mark}] {name}" + (f"  ->  {detail}" if detail else ""))
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} comprobaciones correctas")
    if failures:
        print("Fallos: " + ", ".join(failures))
        return 1
    return 0
