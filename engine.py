from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
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
# Kinds that normally ship as a .bin + .xml pair.
PAIRED_KINDS = frozenset({"btld", "swfl", "swfk"})

# TAL actions that need SWE files from PSdZData.
TAL_FILE_ACTIONS = ("blFlash", "swDeploy", "cdDeploy", "ibaDeploy", "swfkDeploy", "gwtbDeploy")
SGBMID_RE = re.compile(
    r"\b(?P<cls>[A-Z]{4})[_-](?P<ident>[0-9A-Fa-f]{8})[_-](?P<main>\d{1,3})[_-](?P<sub>\d{1,3})[_-](?P<patch>\d{1,3})\b"
)

TAL_ACTION_RE = re.compile(r"(Deploy|Flash|Backup|Restore|Install|Deinstall|Delete|Update)$")

CHANGE_SAME = "same"
CHANGE_UPDATE = "update"
CHANGE_NEW = "new"


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
    action: str = field(default="", compare=False, hash=False)

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
        if self.name_bntn:
            return f"{self.address_hex}  {self.base_variant}  {self.name_bntn}".strip()
        return f"{self.address_hex}  {self.base_variant or f'ECU {self.index + 1}'}".strip()

    @property
    def folder_name(self) -> str:
        raw = f"{self.address_hex}_{self.base_variant}_{self.name_bntn or 'ecu'}"
        cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", raw).strip(" ._")
        return cleaned or f"ecu_{self.index + 1}"


@dataclass
class SvtDocument:
    path: Path
    ecus: list[EcuEntry]
    source: str = "svt"
    istep: str = ""

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
    change: str = ""
    ist_version: str = ""
    override_version: str = ""

    @property
    def found(self) -> bool:
        return bool(self.files)

    @property
    def status(self) -> str:
        if self.files and self.override_version:
            return "override"
        if self.files:
            return "found"
        if self.other_versions:
            return "other"
        return "missing"

    @property
    def export_version(self) -> str:
        return self.override_version or self.part.version

    @property
    def incomplete(self) -> str:
        """'' if fine, else the missing extension of a .bin/.xml pair."""
        if not self.files or self.part.kind not in PAIRED_KINDS:
            return ""
        exts = {_file_ext(path) for path in self.files}
        if "bin" in exts and "xml" not in exts:
            return "xml"
        if "xml" in exts and "bin" not in exts:
            return "bin"
        return ""


def _file_ext(path: Path) -> str:
    parsed = SWE_NAME_RE.match(path.name)
    return parsed.group("ext").lower() if parsed else ""


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
    return SvtDocument(path=file_path, ecus=ecus, source="svt", istep=_find_istep(root))


def _find_istep(root: ET.Element) -> str:
    """Best effort: an I-Step such as G030-24-07-550 anywhere in attributes or text."""
    pattern = re.compile(r"\b[A-Z]\d{3}-\d{2}-\d{2}-\d{3}\b")
    preferred = ""
    for node in root.iter():
        name = _local(node.tag).lower()
        values = list(node.attrib.items())
        if node.text and node.text.strip():
            values.append((name, node.text.strip()))
        for key, value in values:
            hit = pattern.search(value or "")
            if not hit:
                continue
            key_low = key.lower()
            if "target" in key_low or "soll" in key_low or "target" in name:
                return hit.group(0)
            if not preferred:
                preferred = hit.group(0)
    return preferred


def detect_xml_kind(path: str | Path) -> str:
    """'tal' or 'svt' by looking at the XML tags."""
    try:
        for _event, node in ET.iterparse(str(path), events=("start",)):
            name = _local(node.tag).lower()
            if name in ("tal", "talline"):
                return "tal"
            if name in ("svt", "standardsvt", "ecu"):
                return "svt"
    except ET.ParseError:
        return "svt"
    return "svt"


def parse_tal(path: str | Path) -> SvtDocument:
    """Read a PSdZ/E-Sys TAL. Each talLine becomes an ECU; parts carry the TAL action."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8-sig") as handle:
        root = ET.parse(handle).getroot()

    ecus: list[EcuEntry] = []
    by_key: dict[tuple, EcuEntry] = {}
    for line in root.iter():
        if _local(line.tag) != "talLine":
            continue
        base = (line.get("baseVariant") or _child_text(line, "baseVariant") or "").strip()
        address = _ecu_address(line)
        if address is None:
            raw = (line.get("diagAddress") or line.get("diagnosticAddress") or "").strip()
            if raw:
                try:
                    address = int(raw, 16)
                except ValueError:
                    address = None
        key = (address, base)
        ecu = by_key.get(key)
        if ecu is None:
            ecu = EcuEntry(index=len(ecus), base_variant=base, name_bntn=(line.get("nameBNTN") or "").strip(), address=address)
            by_key[key] = ecu
            ecus.append(ecu)
        for action_node in list(line):
            action = _local(action_node.tag)
            if action in ("baseVariant", "diagnosticAddress", "ecu"):
                continue
            seen: set[tuple] = {p.key for p in ecu.parts if p.action == action}
            for part in _parts_in(action_node):
                part = replace(part, action=action)
                if part.key in seen:
                    continue
                seen.add(part.key)
                ecu.parts.append(part)
            if not any(p.action == action for p in ecu.parts) and TAL_ACTION_RE.search(action):
                ecu.parts.append(
                    PartId(process_class="", ident="", main="", sub="", patch="", action=action)
                )
    ecus = [ecu for ecu in ecus if ecu.parts]
    if not ecus:
        raise ValueError("no_ecu")
    return SvtDocument(path=file_path, ecus=ecus, source="tal", istep=_find_istep(root))


def _parts_in(node: ET.Element) -> list[PartId]:
    parts: list[PartId] = []
    for child in node.iter():
        if _local(child.tag) == "partIdentification":
            part = _parse_part(child)
            if part is not None:
                parts.append(part)
    if parts:
        return parts
    # Fallback: SGBMIDs written as text, e.g. SWFL_00001A50_019_000_001
    for child in node.iter():
        values = list(child.attrib.values())
        if child.text:
            values.append(child.text)
        for value in values:
            for hit in SGBMID_RE.finditer(value or ""):
                parts.append(
                    PartId(
                        process_class=hit.group("cls").upper(),
                        ident=hit.group("ident").upper(),
                        main=_pad3(hit.group("main")),
                        sub=_pad3(hit.group("sub")),
                        patch=_pad3(hit.group("patch")),
                    )
                )
    return parts


def load_document(path: str | Path) -> SvtDocument:
    return parse_tal(path) if detect_xml_kind(path) == "tal" else parse_svt(path)


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
        if not part.process_class:
            # TAL action without files (idBackup, fscDeploy, ...)
            results.append(PartMatch(ecu=ecu, part=part, files=[], other_versions=[]))
            continue
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


def _ecu_key(ecu: EcuEntry) -> tuple:
    return (ecu.address, ecu.base_variant.upper())


def match_diff(
    ist: SvtDocument,
    soll: SvtDocument,
    index: SweIndex,
    include_classes: set[str] | None = None,
) -> list[PartMatch]:
    """Match the target (Soll) SVT and mark what changes against the current (Ist) SVT."""
    ist_parts: dict[tuple, dict[tuple[str, str], str]] = {}
    for ecu in ist.ecus:
        table = ist_parts.setdefault(_ecu_key(ecu), {})
        for part in ecu.parts:
            table[(part.process_class, part.ident)] = part.version
    results = match_svt(soll, index, include_classes)
    for match in results:
        before = ist_parts.get(_ecu_key(match.ecu), {}).get((match.part.process_class, match.part.ident))
        if before is None:
            match.change = CHANGE_NEW
        elif before == match.part.version:
            match.change = CHANGE_SAME
            match.ist_version = before
        else:
            match.change = CHANGE_UPDATE
            match.ist_version = before
    return results


def nearest_version(match: PartMatch) -> str:
    """Closest version below the wanted one, else the closest above."""
    want = match.part.version
    lower = [v for v in match.other_versions if v < want]
    if lower:
        return max(lower)
    higher = [v for v in match.other_versions if v > want]
    return min(higher) if higher else ""


def apply_override(match: PartMatch, index: SweIndex, version: str) -> None:
    """Use another version of the same part (version='' resets)."""
    if not version or version == match.part.version:
        match.override_version = ""
        match.files = list(index.files.get(match.part.key, []))
        return
    match.override_version = version
    match.files = list(index.files.get((match.part.kind, match.part.ident.lower(), version), []))


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


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(source: Path, target: Path, use_hash: bool) -> bool:
    if not target.exists():
        return False
    if target.stat().st_size != source.stat().st_size:
        return False
    if not use_hash:
        return True
    try:
        if os.path.samefile(source, target):
            return True
    except OSError:
        pass
    return file_sha1(source) == file_sha1(target)


def export_matches(
    matches: list[PartMatch],
    dest_root: str | Path,
    layout: str = "psdzdata",
    progress=None,
    use_hash: bool = False,
    hardlink: bool = False,
    make_zip: bool = False,
    all_matches: list[PartMatch] | None = None,
    meta: dict | None = None,
) -> tuple[int, int, list[str]]:
    dest = Path(dest_root)
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    linked = 0
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
            if _same_file(source, target, use_hash):
                skipped += 1
            else:
                if target.exists():
                    target.unlink()
                done = False
                if hardlink:
                    try:
                        os.link(source, target)
                        linked += 1
                        done = True
                    except OSError:
                        done = False
                if not done:
                    shutil.copy2(source, target)
                copied += 1
        except OSError as exc:
            errors.append(f"{source.name}: {exc}")
        if progress:
            progress(index, total, source.name)
    write_manifest(
        dest / "svt_export_manifest.txt",
        all_matches if all_matches is not None else matches,
        copied,
        skipped,
        errors,
        exported=matches,
        linked=linked,
        meta=meta,
    )
    if make_zip:
        try:
            zip_folder(dest)
        except OSError as exc:
            errors.append(f"zip: {exc}")
    return copied, skipped, errors


def zip_folder(folder: Path) -> Path:
    """Pack folder into <folder>.zip next to it."""
    folder = Path(folder)
    archive = folder.with_name(folder.name + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder).as_posix())
    return archive


def write_manifest(
    path: Path,
    matches: list[PartMatch],
    copied: int,
    skipped: int,
    errors: list[str],
    exported: list[PartMatch] | None = None,
    linked: int = 0,
    meta: dict | None = None,
) -> None:
    from version import APP_VERSION

    exported_ids = {id(item) for item in (exported if exported is not None else matches)}
    lines = [f"SVT PSdZData Export {APP_VERSION}"]
    for key, value in (meta or {}).items():
        if value:
            lines.append(f"{key}: {value}")
    lines.append(f"Copied: {copied}")
    if linked:
        lines.append(f"  of them hard links: {linked}")
    lines += [
        f"Skipped existing: {skipped}",
        f"Errors: {len(errors)}",
        "",
        "EXPORTED",
        "--------",
    ]

    def describe(match: PartMatch) -> str:
        text = f"{match.ecu.title} | {match.part.label}"
        if match.part.action:
            text += f" | {match.part.action}"
        if match.change == CHANGE_UPDATE:
            text += f" | Ist {match.ist_version}"
        elif match.change == CHANGE_NEW:
            text += " | new"
        return text

    for match in matches:
        if not match.found or id(match) not in exported_ids:
            continue
        names = ", ".join(item.name for item in match.files)
        extra = f" | REPLACED by {match.override_version}" if match.override_version else ""
        warn = f" | WARNING: .{match.incomplete} missing" if match.incomplete else ""
        lines.append(f"{describe(match)} | {names}{extra}{warn}")
    lines.extend(["", "MISSING", "-------"])
    for match in matches:
        if match.found or not match.part.process_class:
            continue
        if match.change == CHANGE_SAME:
            continue
        extra = f" | other versions: {', '.join(match.other_versions)}" if match.other_versions else ""
        lines.append(f"{describe(match)}{extra}")
    if errors:
        lines.extend(["", "ERRORS", "------", *errors])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def missing_matches(matches: list[PartMatch], include_other: bool = True) -> list[PartMatch]:
    """Parts whose exact files are not in PSdZData, plus found parts with half of a bin/xml pair."""
    out = []
    for match in matches:
        if not match.part.process_class or match.change == CHANGE_SAME:
            continue
        if match.part.process_class in HARDWARE_CLASSES:
            continue
        if match.found and not match.override_version:
            if match.incomplete:
                out.append(match)
            continue
        if match.other_versions and not include_other:
            continue
        out.append(match)
    return out


def missing_report(
    matches: list[PartMatch],
    fmt: str = "forum",
    include_other: bool = True,
    car: str = "",
    istep: str = "",
    psdz_name: str = "",
) -> str:
    """Text for a forum post, a plain file list or CSV."""
    from version import APP_VERSION

    items = missing_matches(matches, include_other)

    def names(match: PartMatch) -> list[str]:
        kind = match.part.kind
        ident = match.part.ident.lower()
        ver = match.part.version
        if match.found and match.incomplete and not match.override_version:
            return [f"{kind}_{ident}.{match.incomplete}.{ver}"]
        if kind == "cafd":
            return [f"cafd_{ident}.caf.{ver}"]
        if kind in PAIRED_KINDS:
            return [f"{kind}_{ident}.bin.{ver}", f"{kind}_{ident}.xml.{ver}"]
        return [f"{kind}_{ident}.*.{ver}"]

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["address", "base_variant", "ecu", "class", "id", "version", "action", "other_versions", "files"])
        for match in items:
            writer.writerow([
                match.ecu.address_hex,
                match.ecu.base_variant,
                match.ecu.name_bntn,
                match.part.process_class,
                match.part.ident,
                match.part.version,
                match.part.action,
                " ".join(match.other_versions),
                " ".join(names(match)),
            ])
        return buffer.getvalue()

    if fmt == "names":
        out: list[str] = []
        for match in items:
            out.extend(names(match))
        return "\n".join(out) + ("\n" if out else "")

    where = f" ({psdz_name} doesn't have them)" if psdz_name else ""
    lines = [f"Looking for these SWE files{where}:", ""]
    head = []
    if car:
        head.append(f"Car: {car}")
    if istep:
        head.append(f"I-Step target: {istep}")
    if head:
        lines += ["  ".join(head), ""]
    if not items:
        lines.append("-- nothing missing --")
    for match in items:
        tag = f"[{match.ecu.address_hex} {match.ecu.base_variant}]"
        lines.append(f"{tag} {match.part.label}")
        for name in names(match):
            lines.append(f"  {name}")
        if match.override_version:
            lines.append(f"  (using {match.override_version} for now)")
        elif match.other_versions:
            lines.append(f"  (have: {', '.join(match.other_versions)})")
    lines += ["", f"Generated by SVT-PSdZData Export {APP_VERSION}", "github.com/LeonB1ham/svt-psdzdata-export"]
    return "\n".join(lines) + "\n"


def full_report_csv(matches: list[PartMatch]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "address", "base_variant", "ecu", "action", "class", "id", "version", "ist_version",
        "change", "status", "export_version", "incomplete", "other_versions", "files",
    ])
    for match in matches:
        writer.writerow([
            match.ecu.address_hex,
            match.ecu.base_variant,
            match.ecu.name_bntn,
            match.part.action,
            match.part.process_class,
            match.part.ident,
            match.part.version,
            match.ist_version,
            match.change,
            match.status if match.part.process_class else "no_files",
            match.export_version if match.found else "",
            match.incomplete,
            " ".join(match.other_versions),
            " ".join(path.name for path in match.files),
        ])
    return buffer.getvalue()


def batch_name(svt_path: Path, used: set[str]) -> str:
    """Folder name for one car: the SVT's folder, else its file name."""
    raw = svt_path.parent.name or svt_path.stem
    if raw.lower() in ("svt", "", ".") or raw in used:
        raw = svt_path.stem
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", raw).strip(" ._") or "car"
    name = cleaned
    n = 2
    while name in used:
        name = f"{cleaned}_{n}"
        n += 1
    used.add(name)
    return name


def summarize(matches: list[PartMatch]) -> dict[str, int]:
    real = [item for item in matches if item.part.process_class]
    found = sum(1 for item in real if item.found)
    other = sum(1 for item in real if item.status == "other")
    missing = sum(1 for item in real if item.status == "missing")
    files = sum(len(item.files) for item in real)
    unique = len(unique_source_files(real))
    return {
        "parts": len(real),
        "found": found,
        "other": other,
        "missing": missing,
        "files": files,
        "unique_files": unique,
        "incomplete": sum(1 for item in real if item.incomplete),
        "changed": sum(1 for item in real if item.change in (CHANGE_UPDATE, CHANGE_NEW)),
        "same": sum(1 for item in real if item.change == CHANGE_SAME),
        "no_files": len(matches) - len(real),
        "bytes": sum(path.stat().st_size for path in unique_source_files(real) if path.exists()),
    }
