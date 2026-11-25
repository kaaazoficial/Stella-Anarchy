from __future__ import annotations

import ast
import math
import operator
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ALLOWED_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


class CalculationError(ValueError):
    pass


def calculate(expression: str) -> str:
    """Evaluate a deliberately small mathematical grammar; never execute Python."""
    source = str(expression or "").strip()
    if not source or len(source) > 160:
        raise CalculationError("Escribe una operación de hasta 160 caracteres.")
    try:
        root = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise CalculationError("La operación no tiene un formato matemático válido.") from exc
    nodes = list(ast.walk(root))
    if len(nodes) > 40:
        raise CalculationError("La operación es demasiado compleja.")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = float(node.value)
        elif isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
            value = _ALLOWED_NAMES[node.id]
        elif isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise CalculationError("El exponente máximo permitido es 12.")
            value = _ALLOWED_BINARY[type(node.op)](left, right)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            value = _ALLOWED_UNARY[type(node.op)](visit(node.operand))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCTIONS and not node.keywords:
            args = [visit(arg) for arg in node.args]
            if node.func.id in {"abs", "round", "sqrt", "sin", "cos", "tan", "log", "log10", "floor", "ceil"} and len(args) not in ({1, 2} if node.func.id == "round" else {1}):
                raise CalculationError("Cantidad de argumentos no válida.")
            if node.func.id in {"min", "max"} and not 1 <= len(args) <= 8:
                raise CalculationError("min y max aceptan entre uno y ocho valores.")
            if node.func.id == "round" and len(args) == 2:
                if not args[1].is_integer():
                    raise CalculationError("El número de decimales debe ser entero.")
                value = round(args[0], int(args[1]))
            else:
                value = _ALLOWED_FUNCTIONS[node.func.id](*args)
        else:
            raise CalculationError("Solo se permiten números, operaciones y funciones matemáticas básicas.")
        if not math.isfinite(float(value)) or abs(float(value)) > 1e100:
            raise CalculationError("El resultado queda fuera del rango permitido.")
        return float(value)

    try:
        result = visit(root)
    except CalculationError:
        raise
    except (ArithmeticError, ValueError, TypeError, OverflowError) as exc:
        raise CalculationError("No se pudo calcular esa operación; revisa los valores.") from exc
    if result.is_integer() and abs(result) < 1e16:
        return str(int(result))
    return f"{result:.12g}"


def datetime_text(zone_name: str = "America/Santiago") -> str:
    name = str(zone_name or "").strip() or "America/Santiago"
    if len(name) > 80 or name.startswith(("/", ".")) or ".." in name:
        raise ValueError("Indica una zona IANA válida, por ejemplo America/Santiago.")
    try:
        now = datetime.now(ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("No reconozco esa zona horaria. Usa el formato Area/Ciudad, por ejemplo Europe/Madrid.") from exc
    return f"🕒 {now:%A %d-%m-%Y · %H:%M:%S} ({name}, UTC{now:%z})"
