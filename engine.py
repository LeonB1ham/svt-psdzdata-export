from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

SWE_NAME_RE = re.compile(
    r"^(?P<kind>[a-z0-9]+)_(?P<ident>[0-9a-fA-F]+)\.(?P<ext>[^.]+)\.(?P<ver>\d{3}_\d{3}_\d{3})$",
    re.IGNORECASE,
)

SWE_KIND_DIRS = (
    "blup",
    "btld",
    "cafd",
    "fafp",
    "flsl",
    "flup",
    "gwtb",
    "ibad",
    "swfk",
    "swfl",
    "tlrt",
)

FLASH_CLASSES = frozenset({"BTLD", "SWFL", "SWFK", "CAFD", "GWTB", "FLSL", "FLUP", "BLUP", "IBAD", "FAFP", "TLRT"})
HARDWARE_CLASSES = frozenset({"HWEL", "HWAP", "HWSE"})


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _pad3(value: str) -> str:
    raw = (value or "").strip()
    if raw.isdigit():
        return f"{int(raw):03d}"
    return raw.zfill(3) if raw else "000"


@dataclass(frozen=True)
class PartId:
    process_class: str
    ident: str
    main: str
    sub: str
    patch: str

    @property
    def kind(self) -> str:
        return self.process_class.lower()

    @property
    def version(self) -> str:
        return f"{self.main}_{self.sub}_{self.patch}"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.ident.lower(), self.version)

    @property
    def label(self) -> str:
        return f"{self.process_class} {self.ident} {self.version}"


@dataclass
class EcuEntry:
    index: int
    base_variant: str
    name_bntn: str
    address: int | None
    parts: list[PartId] = field(default_factory=list)

    @property
    def address_hex(self) -> str:
        if self.address is None:
            return "--"
        return f"{self.address:02X}"

    @property
    def title(self) -> str:
        name = self.name_bntn or self.base_variant or f"ECU {self.index + 1}"
        return f"{self.address_hex}  {self.base_variant}  {name}".strip()

    @property
    def folder_name(self) -> str:
        raw = f"{self.address_hex}_{self.base_variant}_{self.name_bntn or 'ecu'}"
        cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", raw).strip(" ._")
        return cleaned or f"ecu_{self.index + 1}"


@dataclass
class SvtDocument:
    path: Path
    ecus: list[EcuEntry]

    @property
    def parts(self) -> list[tuple[EcuEntry, PartId]]:
        return [(ecu, part) for ecu in self.ecus for part in ecu.parts]


@dataclass
class SweIndex:
    swe_root: Path
    files: dict[tuple[str, str, str], list[Path]]
    versions: dict[tuple[str, str], list[str]]
    file_count: int
    kind_counts: dict[str, int]


@dataclass
class PartMatch:
    ecu: EcuEntry
    part: PartId
    files: list[Path]
    other_versions: list[str]

    @property
    def found(self) -> bool:
        return bool(self.files)

    @property
    def status(self) -> str:
        if self.files:
            return "found"
        if self.other_versions:
            return "other"
        return "missing"


def parse_svt(path: str | Path) -> SvtDocument:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8-sig") as handle:
        root = ET.parse(handle).getroot()

    ecus: list[EcuEntry] = []
    for node in root.iter():
        if _local(node.tag) != "ecu":
            continue
        base = (node.get("baseVariant") or "").strip()
        name = (node.get("nameBNTN") or "").strip()
        if not base and not name:
            continue
        address = _ecu_address(node)
        parts = [_parse_part(part_node) for part_node in node.iter() if _local(part_node.tag) == "partIdentification"]
        parts = [part for part in parts if part is not None]
        ecus.append(
            EcuEntry(
                index=len(ecus),
                base_variant=base,
                name_bntn=name,
                address=address,
                parts=parts,
            )
        )
    if not ecus:
        raise ValueError("no_ecu")
    return SvtDocument(path=file_path, ecus=ecus)


def _ecu_address(ecu: ET.Element) -> int | None:
    for node in ecu.iter():
        if _local(node.tag) != "diagnosticAddress":
            continue
        raw = (node.get("physicalOffset") or node.get("offset") or "").strip()
        if not raw:
            raw = (node.text or "").strip()
        if not raw:
            continue
        try:
            return int(raw, 10) if raw.isdigit() else int(raw, 0)
        except ValueError:
            continue
    return None


def _parse_part(node: ET.Element) -> PartId | None:
    process_class = _child_text(node, "processClass").upper()
    ident = _child_text(node, "id").strip()
    if not process_class or not ident:
        return None
    return PartId(
        process_class=process_class,
        ident=ident.upper(),
        main=_pad3(_child_text(node, "mainVersion")),
        sub=_pad3(_child_text(node, "subVersion")),
        patch=_pad3(_child_text(node, "patchVersion")),
    )


def resolve_swe_root(psdz_path: str | Path) -> Path:
    path = Path(psdz_path)
    if not path.exists():
        raise FileNotFoundError(path)

    candidates = [
        path / "psdzdata" / "swe",
        path / "swe",
        path,
    ]
    if path.name.lower() == "psdzdata":
        candidates.insert(0, path / "swe")

    for candidate in candidates:
        if candidate.is_dir() and _looks_like_swe(candidate):
            return candidate.resolve()

    nested = path / "psdzdata"
    if nested.is_dir():
        return resolve_swe_root(nested)
    raise FileNotFoundError(path)


def _looks_like_swe(path: Path) -> bool:
    names = {child.name.lower() for child in path.iterdir() if child.is_dir()}
    if names.intersection(SWE_KIND_DIRS):
        return True
    return any(SWE_NAME_RE.match(child.name) for child in path.iterdir() if child.is_file())


def build_index(swe_root: str | Path, progress=None) -> SweIndex:
    root = Path(swe_root)
    files: dict[tuple[str, str, str], list[Path]] = defaultdict(list)
    versions: dict[tuple[str, str], set[str]] = defaultdict(set)
    kind_counts: dict[str, int] = defaultdict(int)
    counted = 0

    scan_dirs = [child for child in sorted(root.iterdir()) if child.is_dir()]
    if any(child.is_file() for child in root.iterdir()):
        scan_dirs.append(root)

    for folder in scan_dirs:
        kind_hint = folder.name.lower() if folder != root else ""
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                parsed = SWE_NAME_RE.match(entry.name)
                if not parsed:
                    continue
                kind = parsed.group("kind").lower()
                ident = parsed.group("ident").lower()
                version = parsed.group("ver")
                file_path = Path(entry.path)
                files[(kind, ident, version)].append(file_path)
                versions[(kind, ident)].add(version)
                kind_counts[kind] += 1
                counted += 1
                if progress and counted % 2500 == 0:
                    progress(counted, kind_hint or kind)
    return SweIndex(
        swe_root=root,
        files={key: sorted(paths) for key, paths in files.items()},
        versions={key: sorted(values) for key, values in versions.items()},
        file_count=counted,
        kind_counts=dict(kind_counts),
    )


def match_svt(doc: SvtDocument, index: SweIndex, include_classes: set[str] | None = None) -> list[PartMatch]:
    wanted = {item.upper() for item in include_classes} if include_classes else None
    results: list[PartMatch] = []
    for ecu, part in doc.parts:
        if wanted is not None and part.process_class not in wanted:
            continue
        files = list(index.files.get(part.key, []))
        others = [
            version
            for version in index.versions.get((part.kind, part.ident.lower()), [])
            if version != part.version
        ]
        results.append(PartMatch(ecu=ecu, part=part, files=files, other_versions=others))
    return results


def unique_source_files(matches: list[PartMatch]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for match in matches:
        for file_path in match.files:
            resolved = file_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(file_path)
    return out


def destination_for(file_path: Path, match: PartMatch, dest_root: Path, layout: str) -> Path:
    name = file_path.name
    if layout == "flat":
        return dest_root / name
    if layout == "ecu":
        return dest_root / match.ecu.folder_name / name
    if layout == "swe":
        return dest_root / match.part.kind / name
    return dest_root / "psdzdata" / "swe" / match.part.kind / name


def export_matches(
    matches: list[PartMatch],
    dest_root: str | Path,
    layout: str = "psdzdata",
    progress=None,
) -> tuple[int, int, list[str]]:
    dest = Path(dest_root)
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    errors: list[str] = []
    planned: list[tuple[Path, Path]] = []
    seen_dest: set[Path] = set()

    for match in matches:
        if not match.files:
            continue
        for source in match.files:
            target = destination_for(source, match, dest, layout)
            resolved = target.resolve() if target.parent.exists() else target
            if resolved in seen_dest or target in seen_dest:
                continue
            seen_dest.add(resolved)
            planned.append((source, target))

    total = len(planned)
    for index, (source, target) in enumerate(planned, start=1):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size == source.stat().st_size:
                skipped += 1
            else:
                shutil.copy2(source, target)
                copied += 1
        except OSError as exc:
            errors.append(f"{source.name}: {exc}")
        if progress:
            progress(index, total, source.name)
    write_manifest(dest / "svt_export_manifest.txt", matches, copied, skipped, errors)
    return copied, skipped, errors


def write_manifest(
    path: Path,
    matches: list[PartMatch],
    copied: int,
    skipped: int,
    errors: list[str],
) -> None:
    lines = [
        "SVT PSdZData Export",
        f"Copied: {copied}",
        f"Skipped existing: {skipped}",
        f"Errors: {len(errors)}",
        "",
        "FOUND",
        "-----",
    ]
    for match in matches:
        if not match.found:
            continue
        names = ", ".join(item.name for item in match.files)
        lines.append(f"{match.ecu.title} | {match.part.label} | {names}")
    lines.extend(["", "MISSING", "-------"])
    for match in matches:
        if match.found:
            continue
        extra = f" | other versions: {', '.join(match.other_versions)}" if match.other_versions else ""
        lines.append(f"{match.ecu.title} | {match.part.label}{extra}")
    if errors:
        lines.extend(["", "ERRORS", "------", *errors])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(matches: list[PartMatch]) -> dict[str, int]:
    found = sum(1 for item in matches if item.found)
    other = sum(1 for item in matches if item.status == "other")
    missing = sum(1 for item in matches if item.status == "missing")
    files = sum(len(item.files) for item in matches)
    unique = len(unique_source_files(matches))
    return {
        "parts": len(matches),
        "found": found,
        "other": other,
        "missing": missing,
        "files": files,
        "unique_files": unique,
    }
