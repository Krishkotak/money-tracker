"""Deterministic adapters for the supported bank and card statements."""

from pathlib import Path
from typing import Callable, Dict

from ..models import ParsedStatement
from .hsbc import parse_hsbc
from .kotak import parse_kotak
from .sbi import parse_sbi


Parser = Callable[[Path], ParsedStatement]
PARSERS: Dict[str, Parser] = {
    "hsbc": parse_hsbc,
    "kotak": parse_kotak,
    "sbi": parse_sbi,
}


def parse_statement(path: Path, source: str):
    try:
        parser = PARSERS[source.lower()]
    except KeyError as error:
        raise ValueError("Source must be one of: hsbc, kotak, sbi") from error
    return parser(Path(path))
