"""Deterministic CPU rasterizer with Z-buffer and per-pixel A-buffer."""

from __future__ import annotations
import warnings

from collections.abc import Iterable
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import numpy as np

from ..camera import CameraTransform, triangulate_polygon
from ..contracts import (
    CameraSpec, LinePrimitive, Primitive,
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from ..lighting import STUDIO_SOFT_DIRECTION, lighting_id_for_cpu
from ..surface_material import (
    material_from_metadata,
    preserves_legacy_cylinder_shade,
    shade_blinn_phong,
)
from .fragments import (
    _DEPTH_EPSILON,
    _Fragment,
    _append_fragment,
    _composite_fragments,
    _rasterize_triangle,
    _write_samples,
)
from .raster_lines import rasterize_lines as _rasterize_lines
from .raster_math import _clip_polygon_attributes
from .surface_shade import (
    analytic_response,
    evaluate_surface,
    mesh_response_attributes,
    phong_vertex_attributes,
    split_blinn_phong,
    uses_split_composite,
)


def _polyhedron_face_rgb(rgb: np.ndarray, lambert: float) -> np.ndarray:
    """Apply restrained face shading without tinting neutral materials."""
    base = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    strength = float(np.clip(lambert, 0.0, 1.0))
    shade = 0.82 + 0.34 * strength
    if shade >= 1.0:
        return base + (1.0 - base) * min(shade - 1.0, 0.35)
    return base * max(shade, 0.55)


def render_png(
    plan: RenderPlan,
    output: str | Path | None = None,
) -> RenderResult:
    """Render one plan to deterministic PNG bytes or a path."""
    from PIL import Image

    scale = max(1, int(plan.metadata.get("scale", 1)))
    rgba = render_rgba(plan, scale=scale)
    image = Image.fromarray(rgba)
    if scale != 1:
        image = image.resize(
            (plan.width, plan.height),
            resample=Image.Resampling.LANCZOS,
        )
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    data = buffer.getvalue()
    destination = Path(output) if output is not None else None
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="cpu",
        format="png",
        width=plan.width,
        height=plan.height,
        plan_sha256=plan.fingerprint(),
        output_sha256=sha256(data).hexdigest(),
        output=destination,
        data=None if destination is not None else data,
        warnings=plan.warnings,
        metadata={"transparent_compositor": "per-pixel-fragment-list", "scale": scale},
    )


def render_rgba(plan: RenderPlan, *, scale: int = 1) -> np.ndarray:
    """Render a plan to an RGBA uint8 array, primarily for testing."""
    if int(scale) <= 0:
        raise ValueError("scale must be positive")
    scale = int(scale)
    lighting = lighting_id_for_cpu(plan.metadata)
    width, height = int(plan.width) * scale, int(plan.height) * scale
    background = np.asarray(plan.background, dtype=float)
    canvas = np.empty((height, width, 4), dtype=float)
    canvas[...] = background
    depth_buffers: dict[str, np.ndarray] = {}
    for viewport in sorted(plan.viewports, key=lambda item: item.semantic_id):
        left, top, right, bottom = _viewport_bounds(viewport, width, height)
        local_width = max(1, right - left)
        local_height = max(1, bottom - top)
        local, z_buffer = _render_viewport(
            viewport,
            local_width,
            local_height,
            background,
            line_scale=scale,
            lighting=lighting,
        )
        depth_buffers[viewport.semantic_id] = z_buffer
        canvas[top:bottom, left:right] = local[: bottom - top, : right - left]
    canvas = np.clip(np.rint(canvas * 255.0), 0.0, 255.0).astype(np.uint8)
    _draw_text(canvas, plan, depth_buffers, scale=scale)
    from PIL import Image

    from ..compass_overlay import draw_raster_compass
    from ..property_colorbar import draw_raster_colorbar

    image = Image.fromarray(canvas)
    draw_raster_compass(image, plan)
    draw_raster_colorbar(image, plan)
    canvas[:] = np.asarray(image)
    return canvas


def _render_viewport(
    viewport: ViewportPlan,
    width: int,
    height: int,
    background: np.ndarray,
    *,
    line_scale: int,
    initial_color: np.ndarray | None = None,
    initial_depth: np.ndarray | None = None,
    lighting: str = "studio_soft",
) -> tuple[np.ndarray, np.ndarray]:
    transform = CameraTransform(viewport.camera, width, height)
    if initial_color is None:
        color = np.empty((height, width, 4), dtype=float)
        color[...] = background
    else:
        color = np.array(initial_color, dtype=float, copy=True)
        if color.shape != (height, width, 4):
            raise ValueError("initial_color must have shape (height, width, 4)")
    if initial_depth is None:
        z_buffer = np.full((height, width), np.inf, dtype=float)
    else:
        z_buffer = np.array(initial_depth, dtype=float, copy=True)
        if z_buffer.shape != (height, width):
            raise ValueError("initial_depth must have shape (height, width)")
    order_buffer = np.full((height, width), np.iinfo(np.int64).max, dtype=np.int64)
    fragments: dict[int, list[_Fragment]] = {}
    primitive_order = {
        primitive.semantic_id: index
        for index, primitive in enumerate(
            sorted(viewport.primitives, key=lambda item: item.semantic_id)
        )
    }
    for primitive in sorted(viewport.primitives, key=lambda item: item.semantic_id):
        order = primitive_order[primitive.semantic_id]
        if isinstance(primitive, TriangleMeshPrimitive):
            raster_shape = primitive.metadata.get("_raster_shape")
            if primitive.vertex_normals is not None and raster_shape == "sphere":
                _rasterize_sphere(
                    primitive,
                    transform,
                    color,
                    z_buffer,
                    order_buffer,
                    fragments,
                    order,
                    lighting=lighting,
                )
            elif primitive.vertex_normals is not None and raster_shape == "cylinder":
                _rasterize_cylinder(
                    primitive,
                    transform,
                    color,
                    z_buffer,
                    order_buffer,
                    fragments,
                    order,
                    lighting=lighting,
                )
            else:
                _rasterize_mesh(
                    primitive,
                    transform,
                    color,
                    z_buffer,
                    order_buffer,
                    fragments,
                    order,
                    lighting=lighting,
                )
        elif isinstance(primitive, LinePrimitive):
            _rasterize_lines(
                primitive,
                transform,
                color,
                z_buffer,
                order_buffer,
                fragments,
                order,
                width_scale=line_scale,
            )
    _composite_fragments(color, z_buffer, fragments)
    return color, z_buffer


def composite_primitives(
    rgba: np.ndarray,
    depth: np.ndarray,
    camera: CameraSpec,
    primitives: Iterable[Primitive],
    *,
    metadata: dict[str, object] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Composite RGBA ``(H,W,4)`` and depth ``(H,W)`` under matching camera/viewport.
    Depth is positive camera-space ``-z`` (not NDC; assumed); inputs are copied.
    Return new uint8 RGBA/float depth; nearest opaque hits update it, others retain input.
    Transparent/text/metadata do not write depth; opaque ``depth_test=False`` lines set ``-inf``."""

    values = np.ascontiguousarray(rgba, dtype=np.uint8)
    if values.ndim != 3 or values.shape[2] != 4:
        raise ValueError("rgba must have shape (height, width, 4)")
    height, width = values.shape[:2]
    viewport = ViewportPlan(
        semantic_id="main",
        camera=camera,
        primitives=tuple(primitives),
    )
    color, composed_depth = _render_viewport(
        viewport,
        width,
        height,
        np.zeros(4, dtype=float),
        line_scale=1,
        initial_color=values.astype(float) / 255.0,
        initial_depth=depth,
    )
    output = np.clip(np.rint(color * 255.0), 0.0, 255.0).astype(np.uint8)
    plan = RenderPlan(
        width=width,
        height=height,
        background=(0.0, 0.0, 0.0, 0.0),
        viewports=(viewport,),
        metadata=dict(metadata or {}),
    )
    _draw_text(output, plan, {"main": composed_depth}, scale=1)
    if metadata:
        from PIL import Image

        from ..compass_overlay import draw_raster_compass

        image = Image.fromarray(output)
        draw_raster_compass(image, plan)
        output[:] = np.asarray(image)
    return output, composed_depth


def _rasterize_sphere(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
    *,
    lighting: str = "studio_soft",
) -> None:
    center = np.asarray(primitive.metadata["_raster_center"], dtype=float)
    radius = float(primitive.metadata["_raster_radius"])
    offsets = np.asarray(primitive.vertices, dtype=float) - center[None, :]
    radial_error = np.abs(np.linalg.norm(offsets, axis=1) - radius)
    tolerance = max(1.0e-6, 1.0e-6 * max(radius, 1.0))
    if float(np.max(radial_error, initial=0.0)) > tolerance:
        warnings.warn(
            (
                f"{primitive.semantic_id}: sphere metadata does not match the mesh "
                f"(max radial deviation {float(np.max(radial_error)):.3g} > "
                f"{tolerance:.3g}); falling back to mesh rasterization"
            ),
            RuntimeWarning,
            stacklevel=2,
        )
        _rasterize_mesh(
            primitive,
            transform,
            color,
            z_buffer,
            order_buffer,
            fragments,
            primitive_order,
            lighting=lighting,
        )
        return
    camera_center = transform.world_to_camera(center[None, :])[0]
    center_depth = -float(camera_center[2])
    if (
        center_depth + radius < transform.spec.near
        or center_depth - radius > transform.spec.far
    ):
        return
    projected_center = transform.project_camera(camera_center[None, :]).xy[0]
    if transform.spec.projection == "orthographic":
        radius_px = radius * transform.height / (2.0 * transform.spec.ortho_scale)
    else:
        if center_depth <= radius + 1e-12:
            _rasterize_mesh(
                primitive,
                transform,
                color,
                z_buffer,
                order_buffer,
                fragments,
                primitive_order,
            )
            return
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        radius_px = (
            0.5
            * transform.height
            * focal
            * radius
            / np.sqrt(center_depth * center_depth - radius * radius)
        )
    minimum_x = max(0, int(np.floor(projected_center[0] - radius_px - 1.0)))
    maximum_x = min(
        transform.width - 1, int(np.ceil(projected_center[0] + radius_px + 1.0))
    )
    minimum_y = max(0, int(np.floor(projected_center[1] - radius_px - 1.0)))
    maximum_y = min(
        transform.height - 1, int(np.ceil(projected_center[1] + radius_px + 1.0))
    )
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    xs = np.arange(minimum_x, maximum_x + 1, dtype=float) + 0.5
    ys = np.arange(minimum_y, maximum_y + 1, dtype=float) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    if transform.spec.projection == "orthographic":
        world_per_pixel = 2.0 * transform.spec.ortho_scale / transform.height
        delta_x = (grid_x - projected_center[0]) * world_per_pixel
        delta_y = -(grid_y - projected_center[1]) * world_per_pixel
        radial_squared = delta_x * delta_x + delta_y * delta_y
        inside = radial_squared <= radius * radius
        front = np.sqrt(np.maximum(radius * radius - radial_squared, 0.0))
        depth_values = center_depth - front
        normals = np.stack((delta_x, delta_y, front), axis=-1) / radius
    else:
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        ndc_x = 2.0 * grid_x / transform.width - 1.0
        ndc_y = 1.0 - 2.0 * grid_y / transform.height
        rays = np.stack(
            (
                ndc_x * transform.aspect / focal,
                ndc_y / focal,
                -np.ones_like(ndc_x),
            ),
            axis=-1,
        )
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
        along = rays @ camera_center
        discriminant = along * along - (
            float(camera_center @ camera_center) - radius * radius
        )
        inside = discriminant >= 0.0
        distance = along - np.sqrt(np.maximum(discriminant, 0.0))
        hits = rays * distance[..., None]
        depth_values = -hits[..., 2]
        normals = (hits - camera_center) / radius
    inside &= (depth_values >= transform.spec.near - 1e-12) & (
        depth_values <= transform.spec.far + 1e-12
    )
    if not np.any(inside):
        return
    # Analytic spheres historically used a two-sided, low-contrast light so
    # that the fallback looked pleasant for tiny atom markers.  Native
    # geometric overlays (for example a DP cutoff sphere) need a real front /
    # back cue instead.  Keep the legacy default, while allowing a mesh record
    # to request a directional light without introducing a backend-specific
    # style option.
    metadata = primitive.metadata
    surface = material_from_metadata(metadata.get("surface_material"))
    unlit = lighting == "unlit"
    if not unlit and uses_split_composite(surface, primitive.rgba[3]):
        view = (
            np.array((0.0, 0.0, 1.0), dtype=float)
            if transform.spec.projection == "orthographic"
            else -hits
        )
        diffuse, specular, alpha_grid = analytic_response(
            np.asarray(primitive.rgba[:3], dtype=float),
            normals,
            surface,
            view,
            float(primitive.rgba[3]),
            two_sided=bool(metadata.get("_raster_two_sided", True)),
        )
        front_factor = metadata.get("_raster_alpha_front_factor")
        back_factor = metadata.get("_raster_alpha_back_factor")
        if front_factor is not None or back_factor is not None:
            front_factor = float(front_factor if front_factor is not None else 1.0)
            back_factor = float(back_factor if back_factor is not None else 1.0)
            frontness = np.clip(normals[..., 2], -1.0, 1.0)
            blend = 0.5 * (frontness + 1.0)
            alpha_grid = np.clip(
                alpha_grid * (back_factor + (front_factor - back_factor) * blend),
                0.0,
                1.0,
            )
        local_y, local_x = np.nonzero(inside)
        _write_samples(
            pixel_x=local_x + minimum_x,
            pixel_y=local_y + minimum_y,
            pixel_depths=depth_values[local_y, local_x],
            pixel_rgb=np.clip(diffuse[local_y, local_x], 0.0, 1.0),
            pixel_specular=np.maximum(specular[local_y, local_x], 0.0),
            pixel_alpha=alpha_grid[local_y, local_x],
            rgba=primitive.rgba,
            source=primitive.semantic_id,
            order=primitive_order * 1_000_000,
            color=color,
            z_buffer=z_buffer,
            order_buffer=order_buffer,
            fragments=fragments,
        )
        return
    if not unlit and surface is not None and surface.specular > 0.0:
        if transform.spec.projection == "orthographic":
            rgb_values = shade_blinn_phong(
                np.asarray(primitive.rgba[:3], dtype=float),
                normals,
                surface,
                two_sided=bool(metadata.get("_raster_two_sided", True)),
            )
        else:
            diffuse, specular, _alpha = evaluate_surface(
                np.asarray(primitive.rgba[:3], dtype=float),
                normals,
                -hits,
                surface,
                base_alpha=float(primitive.rgba[3]),
                two_sided=bool(metadata.get("_raster_two_sided", True)),
                view_is_direction=True,
            )
            rgb_values = diffuse + specular
        front_factor = metadata.get("_raster_alpha_front_factor")
        back_factor = metadata.get("_raster_alpha_back_factor")
        pixel_alpha = None
        if front_factor is not None or back_factor is not None:
            front_factor = float(front_factor if front_factor is not None else 1.0)
            back_factor = float(back_factor if back_factor is not None else 1.0)
            frontness = np.clip(normals[..., 2], -1.0, 1.0)
            blend = 0.5 * (frontness + 1.0)
            alpha_factor = back_factor + (front_factor - back_factor) * blend
            pixel_alpha = np.clip(float(primitive.rgba[3]) * alpha_factor, 0.0, 1.0)
        local_y, local_x = np.nonzero(inside)
        _write_samples(
            pixel_x=local_x + minimum_x,
            pixel_y=local_y + minimum_y,
            pixel_depths=depth_values[local_y, local_x],
            pixel_rgb=np.clip(rgb_values[local_y, local_x], 0.0, 1.0),
            rgba=primitive.rgba,
            pixel_alpha=None if pixel_alpha is None else pixel_alpha[local_y, local_x],
            source=primitive.semantic_id,
            order=primitive_order * 1_000_000,
            color=color,
            z_buffer=z_buffer,
            order_buffer=order_buffer,
            fragments=fragments,
        )
        return
    light = np.asarray(
        metadata.get("_raster_light", STUDIO_SOFT_DIRECTION), dtype=float
    )
    if light.shape != (3,) or not np.all(np.isfinite(light)):
        light = np.asarray(STUDIO_SOFT_DIRECTION, dtype=float)
    light /= max(float(np.linalg.norm(light)), 1.0e-12)
    if surface is not None:
        ambient = float(surface.ambient)
        diffuse = float(surface.diffuse)
    else:
        ambient = float(metadata.get("_raster_ambient", 0.68))
        diffuse = float(metadata.get("_raster_diffuse", 0.32))
    ambient = float(np.clip(ambient, 0.0, 1.0))
    diffuse = float(np.clip(diffuse, 0.0, 1.0))
    total_light = ambient + diffuse
    if total_light > 1.0:
        ambient /= total_light
        diffuse /= total_light
    if unlit:
        illumination = np.ones(normals.shape[:-1], dtype=float)
    elif bool(metadata.get("_raster_two_sided", True)):
        cosine = np.abs(normals @ light)
        illumination = ambient + diffuse * cosine
    else:
        cosine = np.clip(normals @ light, 0.0, 1.0)
        illumination = ambient + diffuse * cosine
    base_rgb = np.asarray(primitive.rgba[:3])[None, None, :]
    rgb_values = base_rgb * illumination[..., None]
    # Optional native specular cue for geometric overlays.  This is evaluated
    # in camera space, so a true analytic sphere gets a stable highlight that
    # survives alpha compositing on white while retaining the exact circular
    # silhouette.  Existing atom-marker spheres keep the zero default.
    specular = float(np.clip(metadata.get("_raster_specular", 0.0), 0.0, 1.0))
    if unlit:
        specular = 0.0
    if specular > 0.0:
        shininess = float(np.clip(metadata.get("_raster_shininess", 32.0), 1.0, 256.0))
        camera_view = np.array((0.0, 0.0, 1.0), dtype=float)
        half_vector = light + camera_view
        half_vector /= max(float(np.linalg.norm(half_vector)), 1.0e-12)
        highlight = np.clip(normals @ half_vector, 0.0, 1.0) ** shininess
        rgb_values = rgb_values + specular * highlight[..., None] * (1.0 - base_rgb)
    # A front/back alpha profile gives a translucent sphere a readable near
    # surface and a receding far surface without changing its exact circular
    # silhouette. Factors multiply the primitive opacity and are clipped.
    front_factor = metadata.get("_raster_alpha_front_factor")
    back_factor = metadata.get("_raster_alpha_back_factor")
    pixel_alpha = None
    if front_factor is not None or back_factor is not None:
        front_factor = float(front_factor if front_factor is not None else 1.0)
        back_factor = float(back_factor if back_factor is not None else 1.0)
        frontness = np.clip(normals[..., 2], -1.0, 1.0)
        blend = 0.5 * (frontness + 1.0)
        alpha_factor = back_factor + (front_factor - back_factor) * blend
        pixel_alpha = np.clip(float(primitive.rgba[3]) * alpha_factor, 0.0, 1.0)
    local_y, local_x = np.nonzero(inside)
    _write_samples(
        pixel_x=local_x + minimum_x,
        pixel_y=local_y + minimum_y,
        pixel_depths=depth_values[local_y, local_x],
        pixel_rgb=np.clip(rgb_values[local_y, local_x], 0.0, 1.0),
        rgba=primitive.rgba,
        pixel_alpha=None if pixel_alpha is None else pixel_alpha[local_y, local_x],
        source=primitive.semantic_id,
        order=primitive_order * 1_000_000,
        color=color,
        z_buffer=z_buffer,
        order_buffer=order_buffer,
        fragments=fragments,
    )


def _cylinder_view(transform, grid_x, grid_y, depth):
    """Direction toward the camera. Orthographic cylinders keep ``+Z``."""

    if transform.spec.projection == "orthographic":
        return np.array((0.0, 0.0, 1.0), dtype=float)
    ndc_x = 2.0 * grid_x / transform.width - 1.0
    ndc_y = 1.0 - 2.0 * grid_y / transform.height
    focal = 1.0 / np.tan(np.radians(float(transform.spec.fov_y_deg)) * 0.5)
    camera_z = -np.asarray(depth, dtype=float)
    return -np.stack(
        (
            ndc_x * (-camera_z) * transform.aspect / focal,
            ndc_y * (-camera_z) / focal,
            camera_z,
        ),
        axis=-1,
    )


def _rasterize_cylinder(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
    *,
    lighting: str = "studio_soft",
) -> None:
    endpoints = np.asarray(
        [primitive.metadata["_raster_start"], primitive.metadata["_raster_end"]],
        dtype=float,
    )
    camera = transform.world_to_camera(endpoints)
    clipped = transform.clip_segment_camera(camera[0], camera[1])
    if clipped is None:
        return
    projected = transform.project_camera(np.asarray(clipped))
    first, second = projected.xy
    vector = second - first
    length_squared = float(vector @ vector)
    if length_squared < 1e-16:
        return
    radius = float(primitive.metadata["_raster_radius"])
    if transform.spec.projection == "orthographic":
        half_width = radius * transform.height / (2.0 * transform.spec.ortho_scale)
    else:
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        half_width = (
            0.5
            * transform.height
            * focal
            * radius
            / max(float(np.min(projected.depth)), 1e-12)
        )
    half_width = max(0.5, half_width)
    minimum_x = max(0, int(np.floor(min(first[0], second[0]) - half_width - 1.0)))
    maximum_x = min(
        transform.width - 1,
        int(np.ceil(max(first[0], second[0]) + half_width + 1.0)),
    )
    minimum_y = max(0, int(np.floor(min(first[1], second[1]) - half_width - 1.0)))
    maximum_y = min(
        transform.height - 1,
        int(np.ceil(max(first[1], second[1]) + half_width + 1.0)),
    )
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    xs = np.arange(minimum_x, maximum_x + 1, dtype=float) + 0.5
    ys = np.arange(minimum_y, maximum_y + 1, dtype=float) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    parameter = np.clip(
        ((grid_x - first[0]) * vector[0] + (grid_y - first[1]) * vector[1])
        / length_squared,
        0.0,
        1.0,
    )
    closest_x = first[0] + parameter * vector[0]
    closest_y = first[1] + parameter * vector[1]
    distance_squared = (grid_x - closest_x) ** 2 + (grid_y - closest_y) ** 2
    inside = distance_squared <= half_width * half_width
    if transform.spec.projection == "perspective":
        reciprocal = (1.0 - parameter) / projected.depth[
            0
        ] + parameter / projected.depth[1]
        depth_values = 1.0 / np.maximum(reciprocal, 1e-15)
    else:
        depth_values = (1.0 - parameter) * projected.depth[
            0
        ] + parameter * projected.depth[1]
    radial = np.sqrt(
        np.maximum(1.0 - distance_squared / (half_width * half_width), 0.0)
    )
    depth_values = depth_values - radius * radial
    inside &= (depth_values >= transform.spec.near - 1e-12) & (
        depth_values <= transform.spec.far + 1e-12
    )
    if not np.any(inside):
        return
    base_rgb = np.asarray(primitive.rgba[:3], dtype=float)
    surface = material_from_metadata(primitive.metadata.get("surface_material"))
    unlit = lighting == "unlit"
    if not unlit and uses_split_composite(surface, primitive.rgba[3]):
        offset_x = (grid_x - closest_x) / half_width
        offset_y = (grid_y - closest_y) / half_width
        cylinder_normals = np.stack((offset_x, -offset_y, radial), axis=-1)
        diffuse, specular, alpha_grid = analytic_response(
            base_rgb,
            cylinder_normals,
            surface,
            _cylinder_view(transform, grid_x, grid_y, depth_values),
            float(primitive.rgba[3]),
        )
        local_y, local_x = np.nonzero(inside)
        _write_samples(
            pixel_x=local_x + minimum_x,
            pixel_y=local_y + minimum_y,
            pixel_depths=depth_values[local_y, local_x],
            pixel_rgb=np.clip(diffuse[local_y, local_x], 0.0, 1.0),
            pixel_specular=np.maximum(specular[local_y, local_x], 0.0),
            pixel_alpha=alpha_grid[local_y, local_x],
            rgba=primitive.rgba,
            source=primitive.semantic_id,
            order=primitive_order * 1_000_000,
            color=color,
            z_buffer=z_buffer,
            order_buffer=order_buffer,
            fragments=fragments,
        )
        return
    # Matte keeps the screen-space radial bond shade; specular presets use Blinn–Phong.
    if unlit:
        rgb_values = np.broadcast_to(base_rgb, radial.shape + (3,)).astype(float, copy=True)
    elif surface is None or preserves_legacy_cylinder_shade(surface):
        illumination = 0.72 + 0.28 * radial
        rgb_values = base_rgb[None, None, :] * illumination[..., None]
    else:
        offset_x = (grid_x - closest_x) / half_width
        offset_y = (grid_y - closest_y) / half_width
        cylinder_normals = np.stack((offset_x, -offset_y, radial), axis=-1)
        if transform.spec.projection == "orthographic":
            rgb_values = shade_blinn_phong(base_rgb, cylinder_normals, surface)
        else:
            diffuse, specular = split_blinn_phong(
                base_rgb,
                cylinder_normals,
                surface,
                view=_cylinder_view(transform, grid_x, grid_y, depth_values),
            )
            rgb_values = diffuse + specular
    local_y, local_x = np.nonzero(inside)
    _write_samples(
        pixel_x=local_x + minimum_x,
        pixel_y=local_y + minimum_y,
        pixel_depths=depth_values[local_y, local_x],
        pixel_rgb=np.clip(rgb_values[local_y, local_x], 0.0, 1.0),
        rgba=primitive.rgba,
        source=primitive.semantic_id,
        order=primitive_order * 1_000_000,
        color=color,
        z_buffer=z_buffer,
        order_buffer=order_buffer,
        fragments=fragments,
    )


def _rasterize_mesh(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
    *,
    lighting: str = "studio_soft",
) -> None:
    camera_vertices = transform.world_to_camera(primitive.vertices)
    rotation = transform.view_matrix[:3, :3]
    unlit = lighting == "unlit"
    light = np.asarray(STUDIO_SOFT_DIRECTION, dtype=float)
    light /= np.linalg.norm(light)
    material = primitive.metadata.get("material", {})
    ambient = float(material.get("ambient", 0.68))
    diffuse = float(material.get("diffuse", 0.32))
    surface = material_from_metadata(primitive.metadata.get("surface_material"))
    split_response = (not unlit) and uses_split_composite(surface, primitive.rgba[3])
    orthographic = transform.spec.projection == "orthographic"
    phong_mesh = (not unlit) and primitive.vertex_normals is not None and surface is not None and (
        surface.specular > 0.0
        or surface.modulates_opacity()
        or surface.fresnel_albedo > 0.0
    )
    if surface is not None and surface.specular <= 0.0:
        ambient = float(surface.ambient)
        diffuse = float(surface.diffuse)
    triangle_indices = np.asarray(primitive.triangles, dtype=int)
    if len(triangle_indices) == 0:
        return
    world_triangles = primitive.vertices[triangle_indices]
    world_normals = np.cross(
        world_triangles[:, 1] - world_triangles[:, 0],
        world_triangles[:, 2] - world_triangles[:, 0],
    )
    normal_lengths = np.linalg.norm(world_normals, axis=1)
    camera_normals = (
        world_normals / np.maximum(normal_lengths[:, None], 1e-14)
    ) @ rotation.T
    projected_vertices = transform.project_camera(camera_vertices)
    vertex_depths = -camera_vertices[:, 2]
    vertex_inside = (vertex_depths >= float(transform.spec.near) - 1e-12) & (
        vertex_depths <= float(transform.spec.far) + 1e-12
    )
    if primitive.vertex_colors is None:
        shaded_base = np.asarray(primitive.rgba[:3], dtype=float)
    else:
        shaded_base = np.asarray(primitive.vertex_colors, dtype=float)[:, :3]
    vertex_rgb_all = None
    if primitive.vertex_normals is not None:
        camera_vertex_normals = primitive.vertex_normals @ rotation.T
        camera_vertex_normals /= np.maximum(
            np.linalg.norm(camera_vertex_normals, axis=1, keepdims=True), 1e-12
        )
        if unlit:
            vertex_rgb_all = np.array(shaded_base, dtype=float, copy=True)
            if vertex_rgb_all.shape == (3,):
                vertex_rgb_all = np.broadcast_to(
                    vertex_rgb_all, (len(camera_vertex_normals), 3)
                ).copy()
        elif phong_mesh:
            vertex_rgb_all = phong_vertex_attributes(
                camera_vertex_normals, shaded_base, camera_vertices
            )
        elif surface is not None and surface.specular > 0.0:
            vertex_rgb_all = shade_blinn_phong(
                shaded_base, camera_vertex_normals, surface
            )
        else:
            vertex_illumination = ambient + diffuse * np.abs(
                camera_vertex_normals @ light
            )
            vertex_rgb_all = shaded_base * vertex_illumination[:, None]
    rgba = (*primitive.rgba[:3], float(primitive.rgba[3]))
    for triangle_index, indices in enumerate(primitive.triangles):
        if normal_lengths[triangle_index] < 1e-14:
            continue
        if (
            not primitive.double_sided
            and float(camera_normals[triangle_index, 2]) <= 0.0
        ):
            continue
        if unlit and vertex_rgb_all is None:
            if primitive.vertex_colors is None:
                triangle_rgb = np.tile(np.asarray(primitive.rgba[:3], dtype=float), (3, 1))
            else:
                triangle_rgb = np.array(shaded_base[indices], dtype=float, copy=True)
        elif vertex_rgb_all is None and split_response:
            if primitive.vertex_colors is None:
                base = np.asarray(primitive.rgba[:3], dtype=float)
            else:
                base = shaded_base[indices]
            triangle_rgb = mesh_response_attributes(
                base,
                np.repeat(camera_normals[triangle_index][None, :], 3, axis=0),
                camera_vertices[indices],
                surface,
                orthographic=orthographic,
                base_alpha=float(primitive.rgba[3]),
            )
        elif vertex_rgb_all is None and surface is not None and surface.specular > 0.0:
            if primitive.vertex_colors is None:
                base = np.asarray(primitive.rgba[:3], dtype=float)
            else:
                base = shaded_base[indices]
            triangle_rgb = np.asarray(
                shade_blinn_phong(base, camera_normals[triangle_index], surface),
                dtype=float,
            )
            if triangle_rgb.shape == (3,):
                triangle_rgb = np.tile(triangle_rgb, (3, 1))
        elif vertex_rgb_all is None:
            lambert = abs(float(camera_normals[triangle_index] @ light))
            if (
                primitive.vertex_colors is None
                and primitive.metadata.get("kind") == "polyhedron"
            ):
                face_rgb = _polyhedron_face_rgb(primitive.rgba[:3], lambert)
                triangle_rgb = np.tile(face_rgb, (3, 1))
            elif primitive.vertex_colors is None:
                face_rgb = np.asarray(primitive.rgba[:3]) * (
                    ambient + diffuse * lambert
                )
                triangle_rgb = np.tile(face_rgb, (3, 1))
            else:
                triangle_rgb = shaded_base[indices] * (ambient + diffuse * lambert)
        else:
            triangle_rgb = vertex_rgb_all[indices]
        stable_order = primitive_order * 1_000_000 + triangle_index * 16
        if bool(np.all(vertex_inside[indices])):
            _rasterize_triangle(
                projected_vertices.xy[indices],
                projected_vertices.depth[indices],
                rgba,
                triangle_rgb,
                primitive.semantic_id,
                stable_order,
                perspective=transform.spec.projection == "perspective",
                color=color,
                z_buffer=z_buffer,
                order_buffer=order_buffer,
                fragments=fragments,
                phong=surface if np.shape(triangle_rgb)[-1] == 9 else None,
                orthographic=orthographic,
            )
            continue
        camera_triangle = camera_vertices[indices]
        clipped, clipped_rgb = _clip_polygon_attributes(
            camera_triangle,
            triangle_rgb,
            near=transform.spec.near,
            far=transform.spec.far,
        )
        if len(clipped) < 3:
            continue
        triangles = triangulate_polygon(clipped)
        color_triangles = [
            np.asarray([clipped_rgb[0], clipped_rgb[index], clipped_rgb[index + 1]])
            for index in range(1, len(clipped_rgb) - 1)
        ]
        for clipped_index, (triangle, vertex_rgb) in enumerate(
            zip(triangles, color_triangles)
        ):
            clipped_order = (
                primitive_order * 1_000_000 + triangle_index * 16 + clipped_index
            )
            projected = transform.project_camera(triangle)
            _rasterize_triangle(
                projected.xy,
                projected.depth,
                rgba,
                vertex_rgb,
                primitive.semantic_id,
                clipped_order,
                perspective=transform.spec.projection == "perspective",
                color=color,
                z_buffer=z_buffer,
                order_buffer=order_buffer,
                fragments=fragments,
                phong=surface if np.shape(vertex_rgb)[-1] == 9 else None,
                orthographic=orthographic,
            )



def _draw_text(
    canvas: np.ndarray,
    plan: RenderPlan,
    depth_buffers: dict[str, np.ndarray],
    *,
    scale: int,
) -> None:
    texts: list[tuple[ViewportPlan, TextPrimitive]] = []
    for viewport in plan.viewports:
        texts.extend(
            (viewport, primitive)
            for primitive in viewport.primitives
            if isinstance(primitive, TextPrimitive)
        )
    if not texts:
        return
    from PIL import Image, ImageDraw, ImageFont

    image = Image.fromarray(canvas)
    full_width, full_height = image.size
    for viewport, primitive in sorted(texts, key=lambda item: item[1].semantic_id):
        left, top, right, bottom = _viewport_bounds(viewport, full_width, full_height)
        local_width = max(1, right - left)
        local_height = max(1, bottom - top)
        transform = CameraTransform(viewport.camera, local_width, local_height)
        projected = transform.project_world(np.asarray([primitive.position]))
        if not bool(projected.visible[0]):
            continue
        anchor = projected.xy[0]
        if not (0.0 <= anchor[0] < local_width and 0.0 <= anchor[1] < local_height):
            continue
        if primitive.depth_test:
            pixel_x, pixel_y = np.floor(anchor).astype(int)
            opaque_depth = depth_buffers[viewport.semantic_id][pixel_y, pixel_x]
            if opaque_depth < float(projected.depth[0]) - _DEPTH_EPSILON:
                continue
        screen = anchor + np.asarray(primitive.offset_px) * scale
        size_px = max(1, int(round(primitive.size_pt * scale * 96.0 / 72.0)))
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size_px)
        except OSError:  # pragma: no cover - platform font fallback
            font = ImageFont.load_default()
        rgba = tuple(int(round(channel * 255.0)) for channel in primitive.rgba)
        label_layer = Image.new("RGBA", (local_width, local_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(label_layer)
        draw.text(tuple(screen), primitive.text, fill=rgba, font=font, anchor="ls")
        image.alpha_composite(label_layer, dest=(left, top))
    canvas[:] = np.asarray(image)


def _viewport_bounds(
    viewport: ViewportPlan,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x, y, viewport_width, viewport_height = viewport.rect
    return (
        int(round(x * width)),
        int(round(y * height)),
        int(round((x + viewport_width) * width)),
        int(round((y + viewport_height) * height)),
    )










__all__ = ["composite_primitives", "render_png", "render_rgba"]
