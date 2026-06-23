from __future__ import annotations

import base64
import copy
import os
import tempfile
from typing import Any, Dict, Iterable, Optional

from ..presets import get_default_catalog, workspace_root
from ..scene import scene_json
from .core import LoadedCrystal, _slugify, _unique_name, build_loaded_crystal, infer_source_format

def load_default_catalog(
    *,
    root_dir: Optional[str] = None,
    names: Optional[Iterable[str]] = None,
    preset: Optional[Dict[str, Any]] = None,
) -> Dict[str, LoadedCrystal]:
    catalog = get_default_catalog(root_dir=root_dir or workspace_root())
    selected = list(names) if names else list(catalog.keys())
    loaded = {}
    for name in selected:
        entry = catalog[name]
        loaded[name] = build_loaded_crystal(
            name=name,
            cif_path=entry["cif_path"],
            title=entry["title"],
            preset=preset,
            source="catalog",
        )
    return loaded


def infer_uploaded_name(filename: str, existing_names: Iterable[str]) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0]
    return _unique_name(_slugify(stem), existing_names)


def write_uploaded_structure(contents: str, filename: str, upload_dir: Optional[str] = None) -> str:
    if not contents.startswith("data:"):
        raise ValueError("Dash upload contents must be a data URL.")
    header, encoded = contents.split(",", 1)
    if "base64" not in header:
        raise ValueError("Only base64 structure uploads are supported.")
    data = base64.b64decode(encoded)
    target_dir = upload_dir or os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads")
    os.makedirs(target_dir, exist_ok=True)
    safe_name = _slugify(filename)
    path = os.path.join(target_dir, safe_name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def write_uploaded_cif(contents: str, filename: str, upload_dir: Optional[str] = None) -> str:
    return write_uploaded_structure(contents, filename, upload_dir=upload_dir)


def load_uploaded_structure(
    *,
    contents: str,
    filename: str,
    existing_names: Iterable[str],
    preset: Optional[Dict[str, Any]] = None,
    upload_dir: Optional[str] = None,
) -> LoadedCrystal:
    source_path = write_uploaded_structure(contents, filename, upload_dir=upload_dir)
    name = infer_uploaded_name(filename, existing_names)
    title = os.path.splitext(os.path.basename(filename))[0]
    return build_loaded_crystal(
        name=name,
        cif_path=source_path,
        title=title,
        preset=preset,
        source="upload",
        source_format=infer_source_format(source_path),
    )


def load_uploaded_cif(
    *,
    contents: str,
    filename: str,
    existing_names: Iterable[str],
    preset: Optional[Dict[str, Any]] = None,
    upload_dir: Optional[str] = None,
) -> LoadedCrystal:
    return load_uploaded_structure(
        contents=contents,
        filename=filename,
        existing_names=existing_names,
        preset=preset,
        upload_dir=upload_dir,
    )


def bundle_json(bundle: LoadedCrystal) -> Dict[str, Any]:
    return {
        "name": bundle.name,
        "title": bundle.title,
        "cif_path": bundle.cif_path,
        "source_path": bundle.source_path or bundle.cif_path,
        "source_format": bundle.source_format,
        "source_frame_index": bundle.source_frame_index,
        "source_frame_count": bundle.source_frame_count,
        "scene": scene_json(bundle.scene),
        "fragment_table": copy.deepcopy(bundle.fragment_table),
        "topology_fragment_table": copy.deepcopy(bundle.topology_fragment_table),
        "unwrap_overflow": copy.deepcopy(bundle.unwrap_overflow),
        "source": bundle.source,
    }
