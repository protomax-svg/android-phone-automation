"""Parse the Android accessibility hierarchy (uiautomator XML) into elements."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from lxml import etree

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class UIElement:
    index: int
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    package: str
    bounds: tuple[int, int, int, int]  # left, top, right, bottom
    clickable: bool
    scrollable: bool
    focused: bool
    enabled: bool
    checkable: bool
    checked: bool

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id.split("/")[-1]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["center"] = self.center
        return d

    def __str__(self) -> str:
        flags = "".join(
            f for f, on in (("C", self.clickable), ("S", self.scrollable), ("F", self.focused)) if on
        )
        cls = self.class_name.rsplit(".", 1)[-1]
        return f"#{self.index:<3} [{flags:<3}] {cls:<18} {self.label[:50]!r:<52} @{self.center}"


def parse_hierarchy(xml: str, only_meaningful: bool = True) -> list[UIElement]:
    root = etree.fromstring(xml.encode("utf-8"))
    out: list[UIElement] = []
    for node in root.iter("node"):
        a = node.attrib
        m = _BOUNDS.match(a.get("bounds", ""))
        bounds = tuple(int(v) for v in m.groups()) if m else (0, 0, 0, 0)
        el = UIElement(
            index=len(out),
            text=a.get("text", ""),
            content_desc=a.get("content-desc", ""),
            resource_id=a.get("resource-id", ""),
            class_name=a.get("class", ""),
            package=a.get("package", ""),
            bounds=bounds,  # type: ignore[arg-type]
            clickable=a.get("clickable") == "true",
            scrollable=a.get("scrollable") == "true",
            focused=a.get("focused") == "true",
            enabled=a.get("enabled") == "true",
            checkable=a.get("checkable") == "true",
            checked=a.get("checked") == "true",
        )
        if only_meaningful and not (el.text or el.content_desc or el.clickable or el.scrollable):
            continue
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue  # zero-size / off-screen
        el.index = len(out)
        out.append(el)
    return out
