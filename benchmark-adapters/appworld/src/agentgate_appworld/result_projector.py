"""Structured projection for common AppWorld API result shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class AppWorldResultProjector:
    def __init__(
        self,
        *,
        collection_keys: tuple[str, ...] = ("items", "results", "data"),
        max_items: int = 5,
    ) -> None:
        self.collection_keys = collection_keys
        self.max_items = max_items

    def project(self, payload: object, required_fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {"value": payload}
        output: dict[str, Any] = {}
        keep = {
            "error",
            "errors",
            "message",
            "status",
            "success",
            "page",
            "page_size",
            "next_page",
            "next_page_token",
            "total",
            *required_fields,
        }
        for key, value in payload.items():
            name = str(key)
            if name in self.collection_keys and isinstance(value, Sequence) and not isinstance(value, str):
                output[name] = [_project_item(item, required_fields) for item in value[: self.max_items]]
                output[f"{name}_returned"] = len(value)
            elif name in keep or name.endswith("_id"):
                output[name] = value
        if not output:
            output["available_fields"] = sorted(str(key) for key in payload)
        return output


def _project_item(item: object, required_fields: tuple[str, ...]) -> object:
    if not isinstance(item, Mapping):
        return item
    keep = {"id", "name", "title", "status", *required_fields}
    return {str(key): value for key, value in item.items() if str(key) in keep or str(key).endswith("_id")}


__all__ = ["AppWorldResultProjector"]
